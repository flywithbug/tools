from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

try:
    from openai import OpenAI  # noqa: F401
except Exception:
    OpenAI = None  # type: ignore

# ✅ 使用同目录下 gpt 模块（不改动 translate.py）
from .comm.translate import OpenAIModel, TranslationError, translate_flat_dict  # type: ignore


BOX_TOOL = {
    "id": "flutter.slang_i18n",
    "name": "slang_i18n",
    "category": "flutter",
    "summary": "Flutter slang i18n（flat .i18n.json）排序 / 冗余检查清理 / 增量翻译（支持交互）",
    "usage": [
        "slang_i18n",
        "slang_i18n init",
        "slang_i18n doctor",
        "slang_i18n sort",
        "slang_i18n check",
        "slang_i18n clean --yes",
        "slang_i18n translate --api-key $OPENAI_API_KEY",
    ],
    "options": [
        {"flag": "--api-key", "desc": "OpenAI API key（也可用环境变量 OPENAI_API_KEY）"},
        {"flag": "--model", "desc": "模型（默认 gpt-4o）"},
        {"flag": "--full", "desc": "全量翻译（默认增量翻译）"},
        {"flag": "--yes", "desc": "clean 删除冗余时跳过确认"},
        {"flag": "--no-exitcode-3", "desc": "check 发现冗余时仍返回 0（默认返回 3）"},
    ],
    "examples": [
        {"cmd": "slang_i18n init", "desc": "生成 slang_i18n.yaml 模板（新 schema：source/target 都含 code+name_en）"},
        {"cmd": "slang_i18n translate --api-key $OPENAI_API_KEY", "desc": "增量翻译缺失的 keys"},
        {"cmd": "slang_i18n clean --yes", "desc": "删除所有冗余 key（不询问）"},
    ],
    "dependencies": [
        "PyYAML>=6.0",
        "openai>=1.0.0",
    ],
}

CONFIG_FILE = "slang_i18n.yaml"
I18N_DIR = "i18n"

# Exit codes
EXIT_OK = 0
EXIT_FAIL = 1
EXIT_BAD = 2
EXIT_REDUNDANT_FOUND = 3


# =========================================================
# Lazy import for PyYAML
# =========================================================
def _require_yaml():
    try:
        import yaml  # type: ignore

        return yaml
    except Exception:
        raise SystemExit(
            "❌ 缺少依赖 PyYAML（import yaml 失败）\n"
            "修复方式：\n"
            "1) pipx 安装：pipx inject box pyyaml\n"
            "2) 或在 pyproject.toml dependencies 加入 PyYAML>=6.0 后重新发布/安装\n"
        )


# =========================================================
# Config (NEW schema, no backward compatibility)
#   - source_locale: {code, name_en}
#   - target_locales: list[{code, name_en}]
#   - prompts: {default_en, by_locale_en}
# =========================================================
def _schema_error(msg: str) -> ValueError:
    return ValueError(
        "slang_i18n.yaml 格式错误：\n"
        f"- {msg}\n\n"
        "期望结构（新 schema）示例：\n"
        "source_locale:\n"
        "  code: en\n"
        "  name_en: English\n"
        "target_locales:\n"
        "  - code: zh_Hant\n"
        "    name_en: Traditional Chinese\n"
        "  - code: ja\n"
        "    name_en: Japanese\n"
        "prompts:\n"
        "  default_en: |\n"
        "    Translate UI strings naturally.\n"
        "  by_locale_en:\n"
        "    zh_Hant: |\n"
        "      Use Taiwan Traditional Chinese UI style.\n"
        "options:\n"
        "  sort_keys: true\n"
        "  cleanup_extra_keys: true\n"
        "  incremental_translate: true\n"
        "  normalize_filenames: true\n"
    )


def _need_nonempty_str(obj: Dict[str, Any], key: str, path: str) -> str:
    v = obj.get(key)
    if not isinstance(v, str) or not v.strip():
        raise _schema_error(f"{path}.{key} 必须是非空字符串")
    return v.strip()


def _need_bool(obj: Dict[str, Any], key: str, path: str) -> bool:
    v = obj.get(key)
    if not isinstance(v, bool):
        raise _schema_error(f"{path}.{key} 必须是 bool（true/false）")
    return v


def validate_config(cfg: Any) -> Dict[str, Any]:
    if not isinstance(cfg, dict):
        raise _schema_error("根节点必须是 YAML object/map")

    src_raw = cfg.get("source_locale")
    if not isinstance(src_raw, dict):
        raise _schema_error("source_locale 必须是 object/map（包含 code / name_en）")
    src_code = _need_nonempty_str(src_raw, "code", "source_locale")
    src_name_en = _need_nonempty_str(src_raw, "name_en", "source_locale")

    # target_locales: list[ {code, name_en} ]
    targets_raw = cfg.get("target_locales")
    if not isinstance(targets_raw, list) or not targets_raw:
        raise _schema_error("target_locales 必须是非空数组（每项为 {code, name_en}）")

    targets: List[Dict[str, str]] = []
    seen: set[str] = set()
    for i, it in enumerate(targets_raw):
        if not isinstance(it, dict):
            raise _schema_error(f"target_locales[{i}] 必须是 object/map（包含 code / name_en）")
        code = _need_nonempty_str(it, "code", f"target_locales[{i}]")
        name_en = _need_nonempty_str(it, "name_en", f"target_locales[{i}]")
        if code == src_code:
            raise _schema_error(f"target_locales[{i}].code 不应等于 source_locale.code（{src_code}）")
        if code in seen:
            raise _schema_error(f"target_locales[{i}].code 重复：{code}")
        seen.add(code)
        targets.append({"code": code, "name_en": name_en})

    prompts = cfg.get("prompts")
    if prompts is None:
        prompts = {}
    if not isinstance(prompts, dict):
        raise _schema_error("prompts 必须是 object/map（可省略）")

    default_en = prompts.get("default_en", "")
    if default_en is None:
        default_en = ""
    if not isinstance(default_en, str):
        raise _schema_error("prompts.default_en 必须是字符串（可为空）")

    by_locale_en = prompts.get("by_locale_en", {})
    if by_locale_en is None:
        by_locale_en = {}
    if not isinstance(by_locale_en, dict):
        raise _schema_error("prompts.by_locale_en 必须是 object/map（可省略）")
    by_locale_en2: Dict[str, str] = {}
    for k, v in by_locale_en.items():
        if not isinstance(k, str) or not k.strip():
            raise _schema_error("prompts.by_locale_en 的 key 必须是非空字符串（locale code）")
        if not isinstance(v, str):
            raise _schema_error(f"prompts.by_locale_en[{k!r}] 必须是字符串")
        by_locale_en2[k.strip()] = v

    opts = cfg.get("options")
    if not isinstance(opts, dict):
        raise _schema_error("options 必须是 object/map")

    normalize_filenames = opts.get("normalize_filenames", True)
    if not isinstance(normalize_filenames, bool):
        raise _schema_error("options.normalize_filenames 必须是 bool（true/false）")

    return {
        "source_locale": {"code": src_code, "name_en": src_name_en},
        "target_locales": targets,  # list[{code,name_en}]
        "prompts": {
            "default_en": default_en,
            "by_locale_en": by_locale_en2,
        },
        "options": {
            "sort_keys": _need_bool(opts, "sort_keys", "options"),
            "cleanup_extra_keys": _need_bool(opts, "cleanup_extra_keys", "options"),
            "incremental_translate": _need_bool(opts, "incremental_translate", "options"),
            "normalize_filenames": normalize_filenames,
        },
    }


def read_config(path: Path) -> Dict[str, Any]:
    yaml = _require_yaml()
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return validate_config(raw)


def read_config_or_throw(path: Path) -> Dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"❌ 未找到 {CONFIG_FILE}（请先 slang_i18n init）")
    return read_config(path)


def _config_template_text() -> str:
    # 用模板文本生成（为了保留注释）
    return """# slang_i18n.yaml
# Flutter slang i18n 配置（NEW schema）
#
# 目录约定：
# - 在项目根目录执行
# - i18n/ 目录存在
# - 若 i18n/ 下存在子目录：只处理子目录中的 *.i18n.json（视为模块）
# - 若 i18n/ 下无子目录：处理 i18n/ 根目录中的 *.i18n.json

# 源语言（结构化：code + 英文语言名）
source_locale:
  code: en
  name_en: English

# 目标语言列表：每项包含 code + 英文语言名
# 注意：code 用于文件读写与命名；name_en 用于翻译提示词与 translate_flat_dict 入参（更稳定，避免串语言）
target_locales:
  - code: zh_Hant
    name_en: Traditional Chinese
  - code: de
    name_en: German
  - code: es
    name_en: Spanish
  - code: fil
    name_en: Filipino
  - code: fr
    name_en: French
  - code: hi
    name_en: Hindi
  - code: id
    name_en: Indonesian
  - code: ja
    name_en: Japanese
  - code: kk
    name_en: Kazakh
  - code: ko
    name_en: Korean
  - code: pt
    name_en: Portuguese
  - code: ru
    name_en: Russian
  - code: th
    name_en: Thai
  - code: uk
    name_en: Ukrainian
  - code: vi
    name_en: Vietnamese
  - code: tr
    name_en: Turkish
  - code: nl
    name_en: Dutch

# 提示词（英文）：支持默认 + 按 locale code “追加”
# 说明：
# - default_en：所有语言通用的基础提示词（永远生效）
# - by_locale_en：针对某些语言做风格/地区/术语的特殊约束（key 为 locale code；不会覆盖 default_en，而是追加）
# - 工具会在运行时自动拼接一段“强约束 guard”，明确目标语言必须是 name_en 对应语言，
#   并禁止输出与目标不符的语言（尤其是防止输出中文串到西语/德语里）。
prompts:
  default_en: |
    Translate UI strings naturally for a mobile app.
    Keep placeholders/variables unchanged.
    Keep punctuation appropriate for the target language.

  by_locale_en:
    zh_Hant: |
      Use Taiwan Traditional Chinese UI style.
      Prefer common Taiwan wording (e.g., “帳號”, “登入”, “請稍後再試”).

    ja: |
      Use polite and concise Japanese UI tone.
      Prefer natural app wording.

    ko: |
      Use natural Korean UI style.
      Prefer concise mobile UI wording.

# 选项（布尔值）
options:
  # 是否按 key 排序输出（建议开启，便于 diff）
  # - true：输出时对 body keys 按字典序排序（metadata 仍会保持 @@locale 在最前）
  # - false：尽量保持原有顺序（但 JSON 本身的“原有顺序”依赖文件内容）
  sort_keys: true

  # 是否清理冗余 key（目标语言中存在、但源语言中不存在的 key）
  # - true：translate 时会先过滤掉目标语言中的冗余 key（避免“幽灵 key”越积越多）
  # - clean/check 命令依然会以“源语言 keys”为准进行检测/删除
  cleanup_extra_keys: true

  # 是否增量翻译
  # - true：仅翻译目标文件中缺失的 key（推荐，省钱省 tokens）
  # - false：全量覆盖式翻译（等价于 translate --full）
  incremental_translate: true

  # 是否规范化模块目录下的文件命名
  # 规则：
  # - i18n/ 根目录：{locale}.i18n.json
  # - i18n/<module>/：{camelModule}_{locale}.i18n.json
  # 行为：
  # - true：只对能从文件名明确识别 locale 的文件尝试重命名；不覆盖已有目标文件
  # - false：不做任何重命名
  normalize_filenames: true
"""


def init_config(path: Path) -> None:
    _require_yaml()  # 确保依赖存在
    if path.exists():
        _ = read_config(path)  # 存在就校验，不覆盖
        print(f"✅ {CONFIG_FILE} 已存在且格式正确（不会覆盖）")
        return
    path.write_text(_config_template_text(), encoding="utf-8")
    print(f"📝 已生成 {CONFIG_FILE}（新 schema，含详细注释）")


def _source_code(cfg: Dict[str, Any]) -> str:
    return str(cfg["source_locale"]["code"])


def _source_name_en(cfg: Dict[str, Any]) -> str:
    return str(cfg["source_locale"]["name_en"])


def _target_codes(cfg: Dict[str, Any]) -> List[str]:
    return [x["code"] for x in cfg["target_locales"]]


def _target_name_en(cfg: Dict[str, Any], code: str) -> str:
    for x in cfg["target_locales"]:
        if x["code"] == code:
            return x["name_en"]
    return code


# =========================================================
# i18n scanning helpers
# =========================================================
def ensure_i18n_dir() -> Path:
    p = Path.cwd() / I18N_DIR
    if not p.exists() or not p.is_dir():
        raise FileNotFoundError("❌ 当前目录未找到 i18n/（请在项目根目录执行）")
    return p


def _has_any_subdir(i18n_dir: Path) -> bool:
    return any(c.is_dir() for c in i18n_dir.iterdir())


def get_active_groups(i18n_dir: Path) -> List[Path]:
    """规则：
    - i18n/ 下如果存在任何子目录：只处理子目录，不处理 i18n/ 根目录
    - 否则（没有子目录）：处理 i18n/ 根目录
    """
    subdirs = [c for c in i18n_dir.iterdir() if c.is_dir()]
    if subdirs:
        return sorted(subdirs)
    return [i18n_dir]


# =========================================================
# Filename helpers (camelCase folder + _locale suffix)
# =========================================================
def _to_camel(s: str) -> str:
    parts = [p for p in re.split(r"[_\-\s]+", s.strip()) if p]
    if not parts:
        return s
    head = parts[0].lower()
    tail = "".join(p[:1].upper() + p[1:] for p in parts[1:])
    return head + tail


def group_file_name(group: Path, locale_code: str) -> Path:
    """规则：
    - i18n/ 根目录：{locale}.i18n.json
    - i18n/<module>/：{camelFolder}_{locale}.i18n.json
    """
    if group.name == I18N_DIR:
        return group / f"{locale_code}.i18n.json"

    prefix = _to_camel(group.name)
    return group / f"{prefix}_{locale_code}.i18n.json"


# =========================================================
# JSON helpers (meta/body split)
# =========================================================
def load_json_obj(path: Path) -> Dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    try:
        obj = json.loads(text)
    except json.JSONDecodeError as e:
        line = e.lineno
        col = e.colno
        lines = text.splitlines()
        start = max(1, line - 2)
        end = min(len(lines), line + 2)

        ctx: List[str] = []
        for i in range(start, end + 1):
            prefix = ">>" if i == line else "  "
            ctx.append(f"{prefix} {i:4d} | {lines[i-1]}")
        pointer = " " * (col + 8) + "^"
        ctx.append(pointer)

        raise ValueError(
            "❌ JSON 解析失败\n"
            f"- file: {path}\n"
            f"- error: {e.msg}\n"
            f"- at: line {line}, column {col} (char {e.pos})\n"
            "----- context -----\n"
            + "\n".join(ctx)
            + "\n-------------------"
        ) from None

    if not isinstance(obj, dict):
        raise ValueError(f"❌ JSON 必须是 object：{path}")
    return obj


def split_slang_json(path: Path, obj: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, str]]:
    """slang flat json:
    - 所有以 @@ 开头的是 metadata，不翻译
    - 其余 key 必须是 str -> str
    """
    meta: Dict[str, Any] = {}
    body: Dict[str, str] = {}

    for k, v in obj.items():
        if not isinstance(k, str):
            raise ValueError(f"❌ 非法 key（非字符串）：{path}")

        if k.startswith("@@"):
            meta[k] = v
            continue

        if not isinstance(v, str):
            raise ValueError(
                f"❌ 仅支持平铺 string->string：{path}，key={k!r} value_type={type(v).__name__}"
            )
        body[k] = v

    return meta, body


def save_json(path: Path, meta: Dict[str, Any], body: Dict[str, str], sort_keys: bool) -> None:
    """输出顺序：
    1) @@locale（如果存在）
    2) 其它 @@meta（按 key 排序）
    3) 普通 key（按 key 排序可选）
    """
    out: Dict[str, Any] = {}

    if "@@locale" in meta:
        out["@@locale"] = meta.get("@@locale")

    other_meta_keys = sorted([k for k in meta.keys() if k != "@@locale"])
    for k in other_meta_keys:
        out[k] = meta[k]

    if sort_keys:
        for k, v in sorted(body.items(), key=lambda kv: kv[0]):
            out[k] = v
    else:
        for k, v in body.items():
            out[k] = v

    path.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


# =========================================================
# Filename normalization (accurate + conservative)
# =========================================================
def _match_locale_from_filename(filename: str, locales_sorted: List[str]) -> Optional[str]:
    if not filename.endswith(".i18n.json"):
        return None
    stem = filename[: -len(".i18n.json")]
    for loc in locales_sorted:
        if stem.endswith(f"_{loc}"):
            return loc
        if stem == loc:
            return loc
    return None


def normalize_group_filenames(group: Path, locale_codes: List[str], verbose: bool = True) -> None:
    """只规范化 i18n/<module>/ 下的文件名：{camelFolder}_{locale}.i18n.json
    只对“能从文件名明确识别 locale”的文件动手；不覆盖已有目标文件。
    """
    if group.name == I18N_DIR:
        return

    locales_sorted = sorted(set(locale_codes), key=len, reverse=True)
    expected_prefix_camel = _to_camel(group.name)

    for p in group.glob("*.i18n.json"):
        loc = _match_locale_from_filename(p.name, locales_sorted)
        if not loc:
            continue

        expected_name = f"{expected_prefix_camel}_{loc}.i18n.json"
        if p.name == expected_name:
            continue

        target = group / expected_name
        if target.exists():
            if verbose:
                print(f"⚠️ 跳过重命名（目标已存在）：{p.name} -> {target.name}")
            continue

        if verbose:
            print(f"🛠️ 重命名：{p.name} -> {target.name}")
        p.rename(target)


# =========================================================
# Ensure language files
# =========================================================
def ensure_language_files_in_group(group: Path, src_code: str, targets_code: List[str]) -> None:
    """只创建缺失的文件，创建内容仅包含 @@locale"""
    sort_keys = False

    src_path = group_file_name(group, src_code)
    if not src_path.exists():
        save_json(src_path, {"@@locale": src_code}, {}, sort_keys=sort_keys)
        print(f"➕ Created {src_path}")

    for code in targets_code:
        p = group_file_name(group, code)
        if not p.exists():
            save_json(p, {"@@locale": code}, {}, sort_keys=sort_keys)
            print(f"➕ Created {p}")


def ensure_all_language_files(i18n_dir: Path, cfg: Dict[str, Any]) -> None:
    groups = get_active_groups(i18n_dir)
    locale_codes = [_source_code(cfg), *_target_codes(cfg)]

    if bool(cfg["options"].get("normalize_filenames", True)):
        for g in groups:
            normalize_group_filenames(g, locale_codes=locale_codes, verbose=True)

    for g in groups:
        ensure_language_files_in_group(g, _source_code(cfg), _target_codes(cfg))


# =========================================================
# Sort
# =========================================================
def sort_all_json(i18n_dir: Path, sort_keys: bool) -> None:
    for g in get_active_groups(i18n_dir):
        for p in g.glob("*.i18n.json"):
            meta, body = split_slang_json(p, load_json_obj(p))
            save_json(p, meta, body, sort_keys=sort_keys)


# =========================================================
# Redundant check/delete (only body keys)
# =========================================================
@dataclass
class RedundantItem:
    group: str
    file: Path
    locale: str
    extra_keys: List[str]


def collect_redundant(cfg: Dict[str, Any], i18n_dir: Path) -> List[RedundantItem]:
    src_code = _source_code(cfg)
    targets = _target_codes(cfg)

    items: List[RedundantItem] = []
    for group in get_active_groups(i18n_dir):
        module_name = group.name if group.name != I18N_DIR else "i18n"

        src_path = group_file_name(group, src_code)
        try:
            _, src_body = split_slang_json(src_path, load_json_obj(src_path))
        except Exception as e:
            raise ValueError(
                "❌ 读取源语言文件失败\n"
                f"- module={module_name}\n"
                f"- locale={src_code}\n"
                f"- file={src_path}\n"
                f"{e}"
            ) from None

        src_keys = set(src_body.keys())

        for code in targets:
            tgt_path = group_file_name(group, code)
            try:
                _, tgt_body = split_slang_json(tgt_path, load_json_obj(tgt_path))
            except Exception as e:
                raise ValueError(
                    "❌ 读取目标语言文件失败\n"
                    f"- module={module_name}\n"
                    f"- locale={code}\n"
                    f"- file={tgt_path}\n"
                    f"{e}"
                ) from None

            tgt_keys = set(tgt_body.keys())
            extra = sorted(tgt_keys - src_keys)
            if extra:
                items.append(RedundantItem(group=module_name, file=tgt_path, locale=code, extra_keys=extra))
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
        meta, body = split_slang_json(it.file, load_json_obj(it.file))
        for k in it.extra_keys:
            body.pop(k, None)
        save_json(it.file, meta, body, sort_keys=sort_keys)
        print(f"🗑️ Removed {len(it.extra_keys)} keys from {it.file}")


# =========================================================
# Progress
# =========================================================
@dataclass
class Progress:
    total_keys: int
    done_keys: int = 0
    started_at: float = 0.0

    def __post_init__(self) -> None:
        if self.started_at <= 0:
            self.started_at = time.time()

    def bump(self, n: int) -> None:
        self.done_keys += max(0, n)

    def percent(self) -> int:
        if self.total_keys <= 0:
            return 100
        return int(self.done_keys * 100 / self.total_keys)

    def eta_text(self) -> str:
        if self.total_keys <= 0 or self.done_keys <= 0:
            return "ETA: --"
        elapsed = time.time() - self.started_at
        rate = self.done_keys / max(elapsed, 1e-6)
        remain = max(self.total_keys - self.done_keys, 0)
        sec = int(remain / max(rate, 1e-6))
        if sec < 60:
            return f"ETA: {sec}s"
        if sec < 3600:
            return f"ETA: {sec//60}m{sec%60:02d}s"
        return f"ETA: {sec//3600}h{(sec%3600)//60:02d}m"


def _compute_need_for_one(group: Path, cfg: Dict[str, Any], tgt_code: str, incremental: bool, cleanup_extra: bool) -> int:
    src_code = _source_code(cfg)
    src_path = group_file_name(group, src_code)
    tgt_path = group_file_name(group, tgt_code)

    _, src_body = split_slang_json(src_path, load_json_obj(src_path))
    _, tgt_body = split_slang_json(tgt_path, load_json_obj(tgt_path))

    if cleanup_extra:
        tgt_body = {k: v for k, v in tgt_body.items() if k in src_body}

    need = {k: v for k, v in src_body.items() if k not in tgt_body} if incremental else dict(src_body)
    return len(need)


# =========================================================
# Prompt builder (per target code)
#   - prompts.default_en 永远生效
#   - prompts.by_locale_en[code] 为追加，不覆盖 default_en
#   - guard 最后兜底，并使用 name_en 强约束目标语言
# =========================================================
def _build_prompt_for_target(cfg: Dict[str, Any], src_code: str, src_name_en: str, tgt_code: str, tgt_name_en: str) -> str:
    prompts = cfg.get("prompts") or {}
    default_en = (prompts.get("default_en") or "").strip()
    by_locale = prompts.get("by_locale_en") or {}
    locale_extra_en = (by_locale.get(tgt_code) or "").strip()

    guard = (
        "You are translating UI strings for a mobile app.\n"
        f"Source locale code: {src_code}\n"
        f"Source language (English name): {src_name_en}\n"
        f"Target locale code: {tgt_code}\n"
        f"Target language (English name): {tgt_name_en}\n"
        "Rules:\n"
        f"- Output MUST be written in {tgt_name_en}.\n"
        "- Do NOT output any other language.\n"
        "- Do NOT output Chinese unless the target language is Chinese.\n"
        "- Keep placeholders/variables/formatting unchanged.\n"
        "- Keep the meaning accurate and natural for the target language UI.\n"
    )

    parts: List[str] = []
    if default_en:
        parts.append(default_en)
    if locale_extra_en:
        parts.append(locale_extra_en)
    parts.append(guard)
    return "\n\n".join(parts).strip() + "\n"


# =========================================================
# Translation
#   - 文件读写：用 code（src_code / tgt_code）
#   - translate_flat_dict 入参：src_lang 用 source.name_en；tgt_locale 用 target.name_en（更稳定）
# =========================================================
def translate_group(
        group: Path,
        cfg: Dict[str, Any],
        api_key: str,
        model: str,
        incremental: bool,
        cleanup_extra: bool,
        sort_keys: bool,
        progress: Progress,
) -> None:
    src_code = _source_code(cfg)
    src_name_en = _source_name_en(cfg)
    targets_code = _target_codes(cfg)

    src_path = group_file_name(group, src_code)
    _, src_body = split_slang_json(src_path, load_json_obj(src_path))

    module_name = group.name if group.name != I18N_DIR else "i18n"

    for tgt_code in targets_code:
        tgt_path = group_file_name(group, tgt_code)
        tgt_meta, tgt_body = split_slang_json(tgt_path, load_json_obj(tgt_path))

        if cleanup_extra:
            tgt_body = {k: v for k, v in tgt_body.items() if k in src_body}

        need = {k: v for k, v in src_body.items() if k not in tgt_body} if incremental else dict(src_body)

        if not need:
            tgt_meta = dict(tgt_meta)
            tgt_meta.setdefault("@@locale", tgt_code)
            save_json(tgt_path, tgt_meta, tgt_body, sort_keys=sort_keys)
            continue

        tgt_name_en = _target_name_en(cfg, tgt_code)

        print(f"🌍 {module_name}: {src_code} → {tgt_code}  (+{len(need)} keys)")

        prompt_for_target = _build_prompt_for_target(
            cfg,
            src_code=src_code,
            src_name_en=src_name_en,
            tgt_code=tgt_code,
            tgt_name_en=tgt_name_en,
        )

        translated = translate_flat_dict(
            prompt_en=prompt_for_target,
            src_dict=need,
            src_lang=src_name_en,      # ✅ 使用配置里的 name_en
            tgt_locale=tgt_name_en,    # ✅ 使用配置里的 name_en
            model=model,
            api_key=api_key,
        )

        # 翻译完（本次 need 全量完成）后打印 source -> target
        print(f"   🧾 translated ({module_name} {src_code} → {tgt_code}) : {len(translated)} keys")
        for k in need.keys():
            src_text = need.get(k, "")
            tgt_text = translated.get(k, "")
            print(f"     {src_text} -> {tgt_text}")

        tgt_body.update(translated)
        tgt_meta = dict(tgt_meta)
        tgt_meta.setdefault("@@locale", tgt_code)
        save_json(tgt_path, tgt_meta, tgt_body, sort_keys=sort_keys)

        progress.bump(len(translated))
        print(f"   📈 {progress.done_keys}/{progress.total_keys} ({progress.percent()}%) {progress.eta_text()}")


def translate_all(i18n_dir: Path, cfg: Dict[str, Any], api_key: str, model: str, full: bool) -> None:
    incremental = not full
    cleanup_extra = bool(cfg["options"]["cleanup_extra_keys"])
    sort_keys = bool(cfg["options"]["sort_keys"])

    groups = get_active_groups(i18n_dir)
    targets = _target_codes(cfg)

    group_need: Dict[Path, int] = {}
    total_need = 0
    for g in groups:
        need_sum = 0
        for code in targets:
            need_sum += _compute_need_for_one(g, cfg, code, incremental=incremental, cleanup_extra=cleanup_extra)
        group_need[g] = need_sum
        total_need += need_sum

    prog = Progress(total_keys=total_need)
    print(f"🧮 Total keys to translate: {total_need}（模式={'全量' if full else '增量'}）")
    if total_need == 0:
        print("✅ 无需翻译：所有语言文件已齐全")
        return

    for g in groups:
        if group_need.get(g, 0) <= 0:
            continue
        translate_group(
            group=g,
            cfg=cfg,
            api_key=api_key,
            model=model,
            incremental=incremental,
            cleanup_extra=cleanup_extra,
            sort_keys=sort_keys,
            progress=prog,
        )


# =========================================================
# Doctor
# =========================================================
def doctor(cfg_path: Path, api_key: Optional[str]) -> None:
    ok = True

    if OpenAI is None:
        ok = False
        print("❌ OpenAI SDK 不可用：pipx: pipx inject box 'openai>=1.0.0'")
    else:
        print("✅ OpenAI SDK OK")

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
        groups = get_active_groups(i18n_dir)
        if _has_any_subdir(i18n_dir):
            print(f"✅ i18n/ OK（检测到子目录：仅处理 {len(groups)} 个模块目录；根目录不会生成/处理 json）")
        else:
            print("✅ i18n/ OK（无子目录：处理根目录 json）")

    if not cfg_path.exists():
        ok = False
        print(f"❌ 未找到 {CONFIG_FILE}（请先 slang_i18n init）")
    else:
        try:
            cfg = read_config(cfg_path)
            targets = _target_codes(cfg)
            prompt_on = bool((cfg.get("prompts", {}).get("default_en") or "").strip())
            normalize_on = bool(cfg["options"].get("normalize_filenames", True))
            src = cfg["source_locale"]
            print(
                f"✅ {CONFIG_FILE} OK (source={src['code']}({src['name_en']}) targets={len(targets)} "
                f"default_prompt={'ON' if prompt_on else 'OFF'} normalize_filenames={'ON' if normalize_on else 'OFF'})"
            )
        except Exception as e:
            ok = False
            print(f"❌ {CONFIG_FILE} 解析失败：{e}")

    ak = api_key or os.getenv("OPENAI_API_KEY")
    if not ak:
        print("⚠️ 未提供 API Key：--api-key 或环境变量 OPENAI_API_KEY（翻译时需要）")
        print("   macOS/Linux: export OPENAI_API_KEY=\"sk-...\"")
        print("   Windows(PowerShell): setx OPENAI_API_KEY \"sk-...\"")
    else:
        print("✅ API Key 已配置（来源：参数或环境变量）")

    if not ok:
        raise SystemExit(EXIT_BAD)
    print("✅ doctor 完成")


# =========================================================
# Interactive
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


def choose_action_interactive() -> str:
    print("请选择操作：")
    print("1 - 排序（sort）")
    print("2 - 翻译（默认增量，可选全量）")
    print("3 - 检查冗余（check）")
    print("4 - 删除冗余（clean）")
    print("5 - doctor")
    print("6 - init")
    print("0 - 退出")
    choice = _read_choice(
        "请输入 0 / 1 / 2 / 3 / 4 / 5 / 6（或 q 退出）: ",
        valid=["0", "1", "2", "3", "4", "5", "6"],
    )
    if choice == "0":
        return "exit"
    return {
        "1": "sort",
        "2": "translate",
        "3": "check",
        "4": "clean",
        "5": "doctor",
        "6": "init",
    }[choice]


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
        action = choose_action_interactive()
        if action == "exit":
            return EXIT_OK

    if action == "init":
        try:
            init_config(cfg_path)
            return EXIT_OK
        except Exception as e:
            print(str(e))
            return EXIT_BAD

    if action == "doctor":
        try:
            doctor(cfg_path, api_key=args.api_key)
            return EXIT_OK
        except SystemExit as e:
            return int(getattr(e, "code", EXIT_BAD))
        except Exception as e:
            print(str(e))
            return EXIT_BAD

    # below require cfg + i18n
    try:
        cfg = read_config_or_throw(cfg_path)
    except Exception as e:
        print(str(e))
        return EXIT_BAD

    try:
        i18n_dir = ensure_i18n_dir()
    except Exception as e:
        print(str(e))
        return EXIT_BAD

    try:
        ensure_all_language_files(i18n_dir, cfg)
    except Exception as e:
        print(f"❌ 补齐/规范化语言文件失败：{e}")
        return EXIT_BAD

    if action == "sort":
        try:
            sort_all_json(i18n_dir, sort_keys=bool(cfg["options"]["sort_keys"]))
            print("✅ 已完成排序")
            return EXIT_OK
        except Exception as e:
            print(f"❌ 排序失败：{e}")
            return EXIT_FAIL

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

    if action == "translate":
        api_key = args.api_key or os.getenv("OPENAI_API_KEY")
        if not api_key and interactive:
            api_key = _ensure_api_key_interactive(None)

        if not api_key:
            print("❌ 未提供 apiKey（--api-key 或 OPENAI_API_KEY）")
            return EXIT_BAD
        if OpenAI is None:
            print("❌ OpenAI SDK 不可用：pipx: pipx inject box 'openai>=1.0.0'")
            return EXIT_BAD

        full = bool(args.full)
        if interactive and args.action is None:
            print(f"🤖 当前模式：{'全量' if full else '增量'}")
            m = _read_choice("选择翻译模式：1 增量 / 2 全量 / 0 取消: ", valid=["0", "1", "2"])
            if m == "0":
                print("🧊 已取消翻译")
                return EXIT_OK
            full = m == "2"

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
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\n已取消。")
        raise SystemExit(130)
