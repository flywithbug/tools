from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path
from typing import List, Optional

from box_tools._share.openai_translate.models import OpenAIModel

from .config import ALLOWED_OPENAI_MODELS, CONFIG_FILE, init_config, read_config_or_throw
from .doctor import doctor
from .diff import collect_redundant, report_redundant
from .fs import ensure_i18n_dir, get_active_groups, load_json_obj, save_json, split_slang_json
from .sort import sort_all_json
from .translate import TranslationError, ensure_all_language_files, translate_all


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
        {"flag": "--model", "desc": "模型（默认 gpt-4o，且可覆盖配置 openAIModel）"},
        {"flag": "--full", "desc": "全量翻译（默认增量翻译）"},
        {"flag": "--yes", "desc": "clean 删除冗余时跳过确认"},
        {"flag": "--no-exitcode-3", "desc": "check 发现冗余时仍返回 0（默认返回 3）"},
    ],
}

EXIT_OK = 0
EXIT_FAIL = 1
EXIT_BAD = 2
EXIT_REDUNDANT_FOUND = 3


def _read_choice(prompt: str, valid: List[str]) -> str:
    valid_set = {v.lower() for v in valid}
    while True:
        s = input(prompt).strip().lower()
        if s in valid_set:
            return s
        if s in ("q", "quit", "exit"):
            return "0"
        print(f"请输入 {' / '.join(sorted(valid_set))}（或 q 退出）")


def _read_choice_default(prompt: str, valid: List[str], default: str) -> str:
    valid_set = {v.lower() for v in valid}
    d = default.strip().lower()
    while True:
        s = input(prompt).strip().lower()
        if not s:
            return d
        if s in ("q", "quit", "exit"):
            return "0"
        if s in valid_set:
            return s
        print(f"请输入 {' / '.join(sorted(valid_set))}（回车默认 {d}，或 q 退出）")


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
    p.add_argument(
        "--model",
        default=None,
        help=f"模型（命令行优先；不传则使用配置 openAIModel；允许：{', '.join(ALLOWED_OPENAI_MODELS)}）",
    )
    p.add_argument("--full", action="store_true", help="全量翻译（默认增量翻译）")
    p.add_argument("--yes", action="store_true", help="clean 删除冗余时跳过确认")
    p.add_argument("--no-exitcode-3", action="store_true", help="check 发现冗余时仍返回 0（默认返回 3）")
    return p


def _interactive_context_line(cfg_path: Path) -> str:
    i18n_ok = (Path.cwd() / "i18n").is_dir()
    cfg_ok = cfg_path.exists()
    key_ok = bool((os.getenv("OPENAI_API_KEY") or "").strip())
    return f"[ctx] i18n={'OK' if i18n_ok else 'MISSING'}  config={'OK' if cfg_ok else 'MISSING'}  OPENAI_API_KEY={'OK' if key_ok else 'MISSING'}"


def choose_action_interactive(cfg_path: Path) -> str:
    menu = [
        ("1", "sort", "排序（sort）"),
        ("2", "translate", "翻译（translate：默认增量）"),
        ("3", "check", "检查冗余（check）"),
        ("4", "clean", "删除冗余（clean）"),
        ("5", "doctor", "环境诊断（doctor）"),
        ("6", "init", "生成/校验配置（init）"),
        ("0", "exit", "退出"),
    ]
    aliases = {k: v for k, v, _ in menu}

    default_action = "doctor"
    while True:
        print("\n== slang_i18n 交互模式 ==")
        print(_interactive_context_line(cfg_path))
        print("")
        for k, _v, label in menu:
            print(f"{k}. {label}")
        print("")

        s = input(f"请选择操作（默认 {default_action}，回车采用默认）: ").strip().lower()
        if not s:
            return default_action
        if s in ("q", "quit", "exit", "0"):
            return "exit"
        if s in aliases:
            return aliases[s]
        if s in ("h", "help", "?"):
            print("输入数字选择：1/2/3/4/5/6；q/0 退出。")
            continue
        print("无效输入。")


def main(argv: Optional[List[str]] = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    args = build_parser().parse_args(argv)

    cfg_path = Path.cwd() / CONFIG_FILE

    action = args.action
    interactive = False
    if not action:
        interactive = True
        action = choose_action_interactive(cfg_path)
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

    # 以下 action 需要 cfg + i18n
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

    # 模型选择：命令行 --model > 配置 openai_model > 默认
    model = (args.model or "").strip() or (cfg.openai_model or "").strip() or OpenAIModel.GPT_4O.value
    if model not in set(ALLOWED_OPENAI_MODELS):
        print(f"❌ model 不合法：{model!r}，可选：{', '.join(ALLOWED_OPENAI_MODELS)}")
        return EXIT_BAD

    # 补齐/规范化语言文件
    try:
        ensure_all_language_files(i18n_dir, cfg)
    except Exception as e:
        print(f"❌ 补齐/规范化语言文件失败：{e}")
        return EXIT_BAD

    if action == "sort":
        try:
            sort_all_json(i18n_dir, sort_keys=cfg.options.sort_keys)
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

            # 删除：直接读写文件（用 fs 的 split/save）
            for it in items:
                meta, body = split_slang_json(it.file, load_json_obj(it.file))
                for k in it.extra_keys:
                    body.pop(k, None)
                save_json(it.file, meta, body, sort_keys=cfg.options.sort_keys)
                print(f"🗑️ Removed {len(it.extra_keys)} keys from {it.file}")

            print("✅ 已删除冗余 key")
            return EXIT_OK
        except Exception as e:
            print(f"❌ 删除冗余失败：{e}")
            return EXIT_FAIL

    if action == "translate":
        api_key = args.api_key or os.getenv("OPENAI_API_KEY")
        if not api_key and interactive:
            api_key = input("未检测到 OPENAI_API_KEY。请输入 apiKey（直接回车取消翻译）: ").strip() or None
        if not api_key:
            print("❌ 未提供 apiKey（--api-key 或 OPENAI_API_KEY）")
            return EXIT_BAD

        full = bool(args.full)
        if interactive and args.action is None:
            print(f"🤖 当前模式：{'全量' if full else '增量'}")
            m = _read_choice_default(
                "选择翻译模式：1 增量（默认） / 2 全量 / 0 取消（回车=1）: ",
                valid=["0", "1", "2"],
                default="1",
            )
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
        print(f"✅ 翻译完成（{cost:.1f}s，模式={'全量' if full else '增量'}，model={model}）")

        # 翻译后可选排序
        try:
            if cfg.options.sort_keys:
                sort_all_json(i18n_dir, sort_keys=True)
        except Exception:
            pass

        return EXIT_OK

    print("❌ 未知 action")
