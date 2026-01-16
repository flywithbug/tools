from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

try:
    from openai import OpenAI  # noqa: F401
except Exception:
    OpenAI = None  # type: ignore

# ✅ 关键：翻译能力来自 translate/comm/translate_flat.py（你给的文件）
from .comm.translate_flat import OpenAIModel, TranslationError, translate_flat_dict  # type: ignore


BOX_TOOL = {
    "id": "flutter.slang_i18n",
    "name": "slang_i18n",
    "category": "flutter",
    "summary": "Flutter slang i18n（flat .i18n.json）排序 / 冗余检查清理 / 增量翻译（交互 + 非交互）",
    "usage": [
        "slang_i18n",
        "slang_i18n init",
        "slang_i18n doctor",
        "slang_i18n sort",
        "slang_i18n check",
        "slang_i18n clean",
        "slang_i18n translate --api-key $OPENAI_API_KEY",
    ],
    "options": [
        {"flag": "--api-key", "desc": "OpenAI API key（也可用环境变量 OPENAI_API_KEY）"},
        {"flag": "--model", "desc": "模型（默认 gpt-4o）"},
        {"flag": "--full", "desc": "全量翻译（默认增量）"},
        {"flag": "--yes", "desc": "clean 删除冗余时跳过确认"},
        {"flag": "--no-exitcode-3", "desc": "check 发现冗余时仍返回 0（默认返回 3）"},
    ],
    "examples": [
        {"cmd": "slang_i18n", "desc": "进入交互菜单"},
        {"cmd": "slang_i18n init", "desc": "生成 slang_i18n.yaml（存在则校验不覆盖）"},
        {"cmd": "slang_i18n translate --api-key $OPENAI_API_KEY", "desc": "增量翻译补齐缺失 key"},
        {"cmd": "slang_i18n clean --yes", "desc": "直接删除冗余 key（免确认）"},
    ],
    "docs": "src/box_tools/flutter/slang_i18n.md",
}


CONFIG_FILE = "slang_i18n.yaml"
I18N_DIR = "i18n"

# 你给的默认语言集合
DEFAULT_ALL_LOCALES = [
    "en", "zh_Hant", "de", "es", "fil", "fr", "hi", "id", "ja",
    "kk", "ko", "pt", "ru", "th", "uk", "vi", "tr", "nl"
]
DEFAULT_SOURCE_LOCALE = "en"
DEFAULT_TARGET_LOCALES = [x for x in DEFAULT_ALL_LOCALES if x != DEFAULT_SOURCE_LOCALE]

DEFAULT_CONFIG: Dict[str, Any] = {
    "source_locale": DEFAULT_SOURCE_LOCALE,
    "target_locales": DEFAULT_TARGET_LOCALES,
    "prompt_en": "",
    "options": {
        "sort_keys": True,
        "cleanup_extra_keys": True,
        "incremental_translate": True,
    },
}

# Exit codes
EXIT_OK = 0
EXIT_FAIL = 1
EXIT_BAD = 2
EXIT_REDUNDANT_FOUND = 3


# =========================================================
# Lazy import for PyYAML (避免没装就 traceback)
# =========================================================

def _require_yaml():
    try:
        import yaml  # type: ignore
        return yaml
    except Exception:
        raise SystemExit(
            "❌ 缺少依赖 PyYAML（import yaml 失败）\n"
            "修复方式：\n"
            "1) 如果你用 pipx 安装：pipx inject box pyyaml\n"
            "2) 或在 pyproject.toml dependencies 加入 PyYAML>=6.0 后重新发布/安装\n"
        )


# =========================================================
# Config
# =========================================================

def _schema_error(msg: str) -> ValueError:
    return ValueError(
        "slang_i18n.yaml 格式错误：\n"
        f"- {msg}\n\n"
        "期望结构示例：\n"
        "source_locale: en\n"
        "target_locales:\n"
        "  - zh_Hant\n"
        "  - ja\n"
        "prompt_en: |\n"
        "  Translate UI strings naturally.\n"
        "options:\n"
        "  sort_keys: true\n"
        "  cleanup_extra_keys: true\n"
        "  incremental_translate: true\n"
    )


def validate_config(cfg: Any) -> Dict[str, Any]:
    if not isinstance(cfg, dict):
        raise _schema_error("根节点必须是 YAML object/map")

    src = cfg.get("source_locale")
    if not isinstance(src, str) or not src.strip():
        raise _schema_error("source_locale 必须是非空字符串，例如 en")
    src = src.strip()

    targets = cfg.get("target_locales")
    if not isinstance(targets, list) or not targets:
        raise _schema_error("target_locales 必须是非空数组")
    targets2: List[str] = []
    for i, t in enumerate(targets):
        if not isinstance(t, str) or not t.strip():
            raise _schema_error(f"target_locales[{i}] 必须是非空字符串")
        targets2.append(t.strip())

    if src in targets2:
        raise _schema_error(f"target_locales 不应包含 source_locale（当前 source_locale={src}）")

    prompt_en = cfg.get("prompt_en", "")
    if prompt_en is None:
        prompt_en = ""
    if not isinstance(prompt_en, str):
        raise _schema_error("prompt_en 必须是字符串（可为空）")

    opts = cfg.get("options")
    if not isinstance(opts, dict):
        raise _schema_error("options 必须是 object/map")

    def need_bool(key: str) -> bool:
        v = opts.get(key)
        if not isinstance(v, bool):
            raise _schema_error(f"options.{key} 必须是 bool（true/false）")
        return v

    return {
        "source_locale": src,
        "target_locales": targets2,
        "prompt_en": prompt_en,
        "options": {
            "sort_keys": need_bool("sort_keys"),
            "cleanup_extra_keys": need_bool("cleanup_extra_keys"),
            "incremental_translate": need_bool("incremental_translate"),
        },
    }


def read_config(path: Path) -> Dict[str, Any]:
    yaml = _require_yaml()
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return validate_config(raw)


def read_config_or_throw(path: Path) -> Dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"未找到 {CONFIG_FILE}（请先 slang_i18n init）")
    return read_config(path)


def init_config(path: Path) -> None:
    yaml = _require_yaml()
    if path.exists():
        # 存在就校验，不覆盖；格式不对直接报错
        _ = read_config(path)
        print(f"✅ {CONFIG_FILE} 已存在且格式正确（不会覆盖）")
        return

    path.write_text(
        yaml.safe_dump(DEFAULT_CONFIG, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    print(f"📝 已生成 {CONFIG_FILE}（请按需修改）")


# =========================================================
# i18n scanning / json helpers
# =========================================================

def ensure_i18n_dir() -> Path:
    p = Path.cwd() / I18N_DIR
    if not p.exists() or not p.is_dir():
        raise FileNotFoundError("当前目录未找到 i18n/（请在项目根目录执行）")
    return p


def find_groups(i18n_dir: Path) -> List[Path]:
    groups = [i18n_dir]
    for child in i18n_dir.iterdir():
        if child.is_dir():
            groups.append(child)
    return groups


def group_file_name(group: Path, locale: str) -> Path:
    """
    i18n/: en.i18n.json
    i18n/assets/: assets_en.i18n.json
    """
    prefix = "" if group.name == I18N_DIR else group.name
    name = f"{locale}.i18n.json" if not prefix else f"{prefix}_{locale}.i18n.json"
    return group / name


def load_json_obj(path: Path) -> Dict[str, Any]:
    obj = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(obj, dict):
        raise ValueError(f"JSON 必须是 object：{path}")
    return obj


def ensure_flat_string_map(path: Path, obj: Dict[str, Any]) -> Dict[str, str]:
    """
    slang flat：允许 @@locale(str)，其余必须是 str->str
    """
    out: Dict[str, str] = {}
    for k, v in obj.items():
        if not isinstance(k, str):
            raise ValueError(f"非法 key（非字符串）：{path}")
        if k == "@@locale":
            if not isinstance(v, str):
                raise ValueError(f"@@locale 必须是字符串：{path}")
            out[k] = v
            continue
        if not isinstance(v, str):
            raise ValueError(f"仅支持平铺 string->string：{path}，key={k!r} value_type={type(v).__name__}")
        out[k] = v
    return out


def save_json(path: Path, data: Dict[str, str], sort_keys: bool) -> None:
    """
    - @@locale 永远放第一
    - 其余按 key 排序（如果 sort_keys=True）
    """
    locale = data.get("@@locale")
    body = {k: v for k, v in data.items() if k != "@@locale"}
    if sort_keys:
        body = dict(sorted(body.items(), key=lambda kv: kv[0]))
    out = {"@@locale": locale, **body} if locale is not None else body
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def ensure_language_files_in_group(group: Path, src_locale: str, targets: List[str]) -> None:
    src_path = group_file_name(group, src_locale)
    if not src_path.exists():
        save_json(src_path, {"@@locale": src_locale}, sort_keys=False)
        print(f"➕ Created {src_path}")

    for loc in targets:
        p = group_file_name(group, loc)
        if not p.exists():
            save_json(p, {"@@locale": loc}, sort_keys=False)
            print(f"➕ Created {p}")


def ensure_all_language_files(i18n_dir: Path, cfg: Dict[str, Any]) -> None:
    for g in find_groups(i18n_dir):
        ensure_language_files_in_group(g, cfg["source_locale"], cfg["target_locales"])


# =========================================================
# Sort
# =========================================================

def sort_all_json(i18n_dir: Path, sort_keys: bool) -> None:
    for g in find_groups(i18n_dir):
        for p in g.glob("*.i18n.json"):
            obj = ensure_flat_string_map(p, load_json_obj(p))
            save_json(p, obj, sort_keys=sort_keys)


# =========================================================
# Redundant check/delete
# =========================================================

@dataclass
class RedundantItem:
    group: str
    file: Path
    locale: str
    extra_keys: List[str]


def collect_redundant(cfg: Dict[str, Any], i18n_dir: Path) -> List[RedundantItem]:
    src_locale = cfg["source_locale"]
    targets = cfg["target_locales"]

    items: List[RedundantItem] = []
    for group in find_groups(i18n_dir):
        src_path = group_file_name(group, src_locale)
        src_obj = ensure_flat_string_map(src_path, load_json_obj(src_path))
        src_keys = set(k for k in src_obj.keys() if k != "@@locale")

        for loc in targets:
            tgt_path = group_file_name(group, loc)
            tgt_obj = ensure_flat_string_map(tgt_path, load_json_obj(tgt_path))
            tgt_keys = set(k for k in tgt_obj.keys() if k != "@@locale")
            extra = sorted(tgt_keys - src_keys)
            if extra:
                items.append(
                    RedundantItem(
                        group=("i18n" if group.name == I18N_DIR else group.name),
                        file=tgt_path,
                        locale=loc,
                        extra_keys=extra,
                    )
                )
    return items


def report_redundant(items: List[RedundantItem], max_keys_preview: int = 40) -> None:
    if not items:
        print("✅ 未发现冗余 key")
        return

    total_keys = sum(len(x.extra_keys) for x in items)
    print(f"⚠️ 发现冗余：{len(items)} 个文件，合计 {total_keys} 个 key\n")
    for it in items:
        preview = it.extra_keys[:max_keys_preview]
        more = len(it.extra_keys) - len(preview)
        print(f"- module={it.group} locale={it.locale} file={it.file}")
        for k in preview:
            print(f"    • {k}")
        if more > 0:
            print(f"    … and {more} more")
        print("")


def delete_redundant(items: List[RedundantItem], sort_keys: bool) -> None:
    for it in items:
        obj = ensure_flat_string_map(it.file, load_json_obj(it.file))
        locale = obj.get("@@locale", it.locale)
        body = {k: v for k, v in obj.items() if k != "@@locale"}
        for k in it.extra_keys:
            body.pop(k, None)
        save_json(it.file, {"@@locale": locale, **body}, sort_keys=sort_keys)
        print(f"🗑️ Removed {len(it.extra_keys)} keys from {it.file}")


# =========================================================
# Translation
# =========================================================

def translate_group(
        group: Path,
        cfg: Dict[str, Any],
        api_key: str,
        model: str,
        incremental: bool,
        cleanup_extra: bool,
        sort_keys: bool,
) -> None:
    src_locale = cfg["source_locale"]
    targets = cfg["target_locales"]
    prompt_en_cfg = (cfg.get("prompt_en") or "").strip() or None

    src_path = group_file_name(group, src_locale)
    src_obj = ensure_flat_string_map(src_path, load_json_obj(src_path))
    src_body = {k: v for k, v in src_obj.items() if k != "@@locale"}

    for loc in targets:
        tgt_path = group_file_name(group, loc)
        tgt_obj = ensure_flat_string_map(tgt_path, load_json_obj(tgt_path))
        tgt_body = {k: v for k, v in tgt_obj.items() if k != "@@locale"}

        if cleanup_extra:
            tgt_body = {k: v for k, v in tgt_body.items() if k in src_body}

        need = {k: v for k, v in src_body.items() if k not in tgt_body} if incremental else dict(src_body)
        if not need:
            save_json(tgt_path, {"@@locale": loc, **tgt_body}, sort_keys=sort_keys)
            continue

        module_name = "i18n" if group.name == I18N_DIR else group.name
        print(f"🌍 {module_name}: {src_locale} → {loc}  ({'+' if incremental else ''}{len(need)} keys)")

        translated = translate_flat_dict(
            prompt_en=prompt_en_cfg,
            src_dict=need,
            src_lang=src_locale,
            tgt_locale=loc,
            model=model,
            api_key=api_key,
        )

        tgt_body.update(translated)
        save_json(tgt_path, {"@@locale": loc, **tgt_body}, sort_keys=sort_keys)


def translate_all(i18n_dir: Path, cfg: Dict[str, Any], api_key: str, model: str, full: bool) -> None:
    incremental = not full
    cleanup_extra = bool(cfg["options"]["cleanup_extra_keys"])
    sort_keys = bool(cfg["options"]["sort_keys"])

    for g in find_groups(i18n_dir):
        translate_group(
            group=g,
            cfg=cfg,
            api_key=api_key,
            model=model,
            incremental=incremental,
            cleanup_extra=cleanup_extra,
            sort_keys=sort_keys,
        )


# =========================================================
# Doctor
# =========================================================

def doctor(cfg_path: Path, api_key: Optional[str]) -> None:
    ok = True

    if OpenAI is None:
        ok = False
        print("❌ OpenAI SDK 不可用：请 pip install openai>=1.0.0")
    else:
        print("✅ OpenAI SDK OK")

    # PyYAML 检查（懒加载）
    try:
        _require_yaml()
        print("✅ PyYAML OK")
    except SystemExit as e:
        ok = False
        print(str(e).strip())

    i18n_dir = Path.cwd() / I18N_DIR
    if not i18n_dir.exists() or not i18n_dir.is_dir():
        ok = False
        print("❌ 未找到 i18n/（请在项目根目录执行）")
    else:
        groups = find_groups(i18n_dir)
        print(f"✅ i18n/ OK（groups: {len(groups)}）")

    if not cfg_path.exists():
        ok = False
        print(f"❌ 未找到 {CONFIG_FILE}（请先 slang_i18n init）")
    else:
        try:
            cfg = read_config(cfg_path)
            prompt_on = bool((cfg.get("prompt_en") or "").strip())
            print(
                f"✅ {CONFIG_FILE} OK "
                f"(source={cfg['source_locale']} targets={len(cfg['target_locales'])} prompt_en={'ON' if prompt_on else 'OFF'})"
            )
        except Exception as e:
            ok = False
            print(f"❌ {CONFIG_FILE} 解析失败：{e}")

    ak = api_key or os.getenv("OPENAI_API_KEY")
    if not ak:
        print("⚠️ 未提供 API Key：--api-key 或环境变量 OPENAI_API_KEY（翻译时需要）")
    else:
        print("✅ API Key 已配置（来源：参数或环境变量）")

    if not ok:
        raise SystemExit(EXIT_BAD)
    print("✅ doctor 完成")


# =========================================================
# Interactive (pub_version style)
# =========================================================

def _read_choice(prompt: str, valid: Iterable[str]) -> str:
    valid_set = {v.lower() for v in valid}
    while True:
        s = input(prompt).strip().lower()
        if s in valid_set:
            return s
        if s in ("q", "quit", "exit"):
            return "0"
        print(f"请输入 {' / '.join(sorted(valid_set))}（或 q 退出）")


def _ensure_api_key_interactive(passed: Optional[str]) -> Optional[str]:
    if passed:
        return passed
    env = os.getenv("OPENAI_API_KEY")
    if env:
        return env
    s = input("未检测到 OPENAI_API_KEY。请输入 apiKey（直接回车取消翻译）: ").strip()
    return s or None


def _print_header(cfg: Optional[Dict[str, Any]], i18n_dir: Optional[Path], model: str) -> None:
    print("🧩 slang_i18n")
    if i18n_dir and i18n_dir.exists():
        groups = find_groups(i18n_dir)
        print(f"📁 i18n 目录: {i18n_dir}（groups: {len(groups)}）")
    else:
        print("📁 i18n 目录: 未找到")

    if cfg:
        print(f"🌐 source_locale: {cfg['source_locale']}")
        print(f"🎯 target_locales: {len(cfg['target_locales'])} 个（默认内置列表）")
        prompt_on = bool((cfg.get('prompt_en') or '').strip())
        print(f"📝 prompt_en: {'ON' if prompt_on else 'OFF'}")
        opts = cfg["options"]
        print(f"⚙️  sort_keys={opts['sort_keys']} cleanup_extra_keys={opts['cleanup_extra_keys']} incremental_translate={opts['incremental_translate']}")
    else:
        print("⚙️  配置: 未加载（请先 slang_i18n init）")

    print(f"🤖 默认模型: {model}")
    print("")


def choose_action_interactive(model_default: str) -> str:
    cfg_path = Path.cwd() / CONFIG_FILE
    cfg: Optional[Dict[str, Any]] = None
    i18n_dir: Optional[Path] = None

    try:
        i18n_dir = ensure_i18n_dir()
    except Exception:
        i18n_dir = None

    if cfg_path.exists():
        try:
            cfg = read_config(cfg_path)
        except Exception as e:
            print(f"❌ {e}")
            cfg = None

    _print_header(cfg, i18n_dir, model_default)

    print("请选择操作：")
    print("1 - 排序（sort）")
    print("2 - 增量翻译（translate incremental）")
    print("3 - 检查冗余（check）")
    print("4 - 删除冗余（clean）")
    print("5 - doctor")
    print("6 - init")
    print("0 - 退出")

    choice = _read_choice("请输入 0 / 1 / 2 / 3 / 4 / 5 / 6（或 q 退出）: ", valid=["0", "1", "2", "3", "4", "5", "6"])
    if choice == "0":
        return "exit"
    if choice == "1":
        return "sort"
    if choice == "2":
        return "translate"
    if choice == "3":
        return "check"
    if choice == "4":
        return "clean"
    if choice == "5":
        return "doctor"
    if choice == "6":
        return "init"
    return "exit"


# =========================================================
# CLI
# =========================================================

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="slang_i18n",
        description="Flutter slang i18n（flat .i18n.json）排序 / 冗余检查清理 / 增量翻译（支持交互）",
    )
    p.add_argument(
        "action",
        nargs="?",
        choices=["init", "doctor", "sort", "translate", "check", "clean"],
        help="动作（不填则进入交互菜单）",
    )
    p.add_argument("--api-key", default=None, help="OpenAI API key（也可用环境变量 OPENAI_API_KEY）")
    p.add_argument("--model", default=OpenAIModel.GPT_4O.value, help="模型（默认 gpt-4o）")
    p.add_argument("--full", action="store_true", help="全量翻译（默认增量翻译）")
    p.add_argument("--yes", action="store_true", help="clean 删除冗余时跳过确认")
    p.add_argument("--no-exitcode-3", action="store_true", help="check 发现冗余时仍返回 0（默认返回 3）")
    return p


def main(argv: Optional[List[str]] = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    args = build_parser().parse_args(argv)

    cfg_path = Path.cwd() / CONFIG_FILE
    model = args.model

    action = args.action
    interactive = False
    if not action:
        interactive = True
        action = choose_action_interactive(model_default=model)
        if action == "exit":
            return EXIT_OK

    # init / doctor
    if action == "init":
        try:
            init_config(cfg_path)
            return EXIT_OK
        except SystemExit as e:
            print(str(e).strip())
            return EXIT_BAD
        except Exception as e:
            print(f"❌ {e}")
            return EXIT_BAD

    if action == "doctor":
        try:
            doctor(cfg_path, api_key=args.api_key)
            return EXIT_OK
        except SystemExit as e:
            return int(getattr(e, "code", EXIT_BAD))
        except Exception as e:
            print(f"❌ {e}")
            return EXIT_BAD

    # 其余动作：需要 config + i18n
    try:
        cfg = read_config_or_throw(cfg_path)
    except Exception as e:
        print(f"❌ {e}")
        return EXIT_BAD

    try:
        i18n_dir = ensure_i18n_dir()
    except Exception as e:
        print(f"❌ {e}")
        return EXIT_BAD

    # 补齐语言文件（en + targets）
    try:
        ensure_all_language_files(i18n_dir, cfg)
    except Exception as e:
        print(f"❌ 补齐语言文件失败：{e}")
        return EXIT_BAD

    # sort
    if action == "sort":
        try:
            sort_all_json(i18n_dir, sort_keys=bool(cfg["options"]["sort_keys"]))
            print("✅ 已完成排序")
            return EXIT_OK
        except Exception as e:
            print(f"❌ 排序失败：{e}")
            return EXIT_FAIL

    # check redundant
    if action == "check":
        try:
            items = collect_redundant(cfg, i18n_dir)
            report_redundant(items)
            if items and not args.no_exitcode_3:
                return EXIT_REDUNDANT_FOUND
            return EXIT_OK
        except Exception as e:
            print(f"❌ 冗余检查失败：{e}")
            return EXIT_FAIL

    # clean redundant
    if action == "clean":
        try:
            items = collect_redundant(cfg, i18n_dir)
            report_redundant(items)
            if not items:
                return EXIT_OK

            if not args.yes:
                ans = _read_choice("确认删除以上冗余 key？请输入 1 删除 / 0 取消: ", valid=["0", "1"])
                if ans != "1":
                    print("🧊 已取消删除")
                    return EXIT_OK

            delete_redundant(items, sort_keys=bool(cfg["options"]["sort_keys"]))
            print("✅ 已删除冗余 key")
            return EXIT_OK
        except Exception as e:
            print(f"❌ 删除冗余失败：{e}")
            return EXIT_FAIL

    # translate
    if action == "translate":
        api_key = args.api_key or os.getenv("OPENAI_API_KEY")
        if not api_key:
            api_key = _ensure_api_key_interactive(None) if interactive else None
        if not api_key:
            print("❌ 未提供 apiKey（--api-key 或 OPENAI_API_KEY）")
            return EXIT_BAD
        if OpenAI is None:
            print("❌ OpenAI SDK 不可用：请 pip install openai>=1.0.0")
            return EXIT_BAD

        full = bool(args.full)

        if interactive and args.action is None:
            print(f"🤖 当前模式：{'全量' if full else '增量'}")
            m = _read_choice("选择翻译模式：1 增量 / 2 全量 / 0 取消: ", valid=["0", "1", "2"])
            if m == "0":
                print("🧊 已取消翻译")
                return EXIT_OK
            full = (m == "2")

        started = time.time()
        try:
            translate_all(i18n_dir, cfg, api_key=api_key, model=model, full=full)
        except TranslationError as e:
            print(f"❌ TranslationError: {e}")
            return EXIT_FAIL
        except Exception as e:
            print(f"❌ 翻译失败：{e}")
            return EXIT_FAIL

        cost = time.time() - started
        print(f"✅ 翻译完成（{cost:.1f}s，模式={'全量' if full else '增量'}）")

        # 翻译后可选排序
        try:
            if bool(cfg["options"]["sort_keys"]):
                sort_all_json(i18n_dir, sort_keys=True)
        except Exception:
            pass

        return EXIT_OK

    print("❌ 未知 action")
    return EXIT_BAD


if __name__ == "__main__":
    raise SystemExit(main())
