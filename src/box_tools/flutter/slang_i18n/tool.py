from __future__ import annotations

import argparse
from pathlib import Path
from typing import List, Optional, Tuple

from .models import RuntimeOptions
from .actions_core import (
    run_init,
    run_doctor,
    run_sort,
    run_check,
    run_clean,
)
from .config import ConfigError, default_config_path, load_config

BOX_TOOL = {
    "id": "flutter.slang_i18n",
    "name": "box_slang_i18n",
    "category": "flutter",
    "summary": "Flutter slang 多语言管理与 AI 翻译工具（init/doctor/sort/check/clean/translate）",
    "usage": [
        "box_slang_i18n",
        "box_slang_i18n --action init",
        "box_slang_i18n --action doctor",
        "box_slang_i18n --dry-run --action init",
        "box_slang_i18n --config ./slang_i18n.yaml --action doctor",
    ],
    "options": [
        {"flag": "--action", "desc": "直接执行某个动作：init/doctor/sort/check/clean/translate"},
        {"flag": "--config", "desc": "指定配置文件路径（默认 ./slang_i18n.yaml）"},
        {"flag": "--root", "desc": "项目根目录（默认当前目录）"},
        {"flag": "--dry-run", "desc": "演练模式：不写文件、不创建目录"},
    ],
    "dependencies": [
        "PyYAML>=6.0",
    ],
    "docs": "README.md",
}

MENU: List[Tuple[str, str, str]] = [
    ("1", "sort", "排序（sort）"),
    ("2", "translate", "翻译（translate：默认增量）"),
    ("3", "check", "检查冗余（check）"),
    ("4", "clean", "删除冗余（clean）"),
    ("5", "doctor", "环境诊断（doctor）"),
    ("6", "init", "生成/校验配置（init）"),
    ("0", "exit", "退出"),
]

ACTION_BY_KEY = {k: action for k, action, _ in MENU}


# =========================
# UI helpers
# =========================

def _print_menu(only_init: bool) -> None:
    print("\n请选择功能：")
    for k, action, label in MENU:
        if only_init and action not in ("init", "exit"):
            continue
        print(f"  {k}. {label}")
    print("")


def _print_report(rep) -> None:
    print(f"\n== {rep.action} 结果 ==")

    if hasattr(rep, "counts_by_level"):
        c = rep.counts_by_level()
        print(f"issues: info={c.get('info', 0)} warn={c.get('warn', 0)} error={c.get('error', 0)}")

    for key in ("files_scanned", "files_changed", "keys_added", "keys_removed", "keys_translated"):
        val = getattr(rep, key, 0)
        if val:
            print(f"{key}: {val}")

    issues = getattr(rep, "issues", ()) or ()
    if issues:
        print("\n说明：")
        for i in issues:
            loc = f" ({i.path})" if getattr(i, "path", None) else ""
            print(f"- [{i.level.value}] {i.code.value}: {i.message}{loc}")
            if i.details:
                print(f"    details: {i.details}")
    else:
        print("\n未发现问题。")

    print("\n状态：", "OK" if getattr(rep, "ok", True) else "FAILED")
    print("")


def _print_config_missing_help(cfg_path: Path) -> None:
    print(f"[错误] 未找到配置文件：{cfg_path}")
    print("建议：")
    print("  1) 执行：box_slang_i18n --action init")
    print("  2) 或使用 --config 指定配置文件路径")
    print("  3) 确认 --root 指向项目根目录")
    print("")


def _print_config_invalid_help(cfg_path: Path, err: Exception) -> None:
    print(f"[错误] 配置文件校验失败：{cfg_path}")
    print(f"原因：{err}")
    print("建议：")
    print("  1) 执行：box_slang_i18n --action init 生成/修复配置")
    print("  2) 或手动修复 YAML 结构（source_locale / target_locales / options / prompts 等）")
    print("")


# =========================
# Startup config gate
# =========================

def _startup_check_config(root_dir: Path, rt: RuntimeOptions) -> bool:
    cfg_path = rt.config_path or default_config_path(root_dir)

    if not cfg_path.exists():
        _print_config_missing_help(cfg_path)
        return False

    try:
        load_config(root_dir=root_dir, config_path=cfg_path)
        return True
    except ConfigError as e:
        _print_config_invalid_help(cfg_path, e)
        return False
    except Exception as e:
        _print_config_invalid_help(cfg_path, e)
        return False


# =========================
# Action dispatch
# =========================

def _run_action(action: str, root_dir: Path, rt: RuntimeOptions) -> int:
    action = (action or "").strip().lower()

    try:
        if action == "init":
            rep = run_init(root_dir=root_dir, rt=rt)
        elif action == "doctor":
            rep = run_doctor(root_dir=root_dir, rt=rt)
        elif action == "sort":
            rep = run_sort(root_dir=root_dir, rt=rt)
        elif action == "check":
            rep = run_check(root_dir=root_dir, rt=rt)
        elif action == "clean":
            rep = run_clean(root_dir=root_dir, rt=rt)
        elif action == "translate":
            print("[提示] translate 将在 actions_translate.py 中实现（后续接入 OpenAI 翻译底座）。")
            return 3
        else:
            print(f"[错误] 未知 action：{action}")
            return 3

        _print_report(rep)
        return 0 if rep.ok else 2

    except NotImplementedError as e:
        print(f"[提示] 功能尚未实现：{e}")
        return 3
    except KeyboardInterrupt:
        print("\n退出。")
        return 0
    except Exception as e:
        print(f"[错误] 执行失败：{e}")
        return 2


# =========================
# Main
# =========================

def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog=BOX_TOOL["name"], add_help=True)
    parser.add_argument("--action", default=None)
    parser.add_argument("--config", default=None)
    parser.add_argument("--root", default=".")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    root_dir = Path(args.root).resolve()
    cfg_path = Path(args.config).resolve() if args.config else None

    rt = RuntimeOptions(
        dry_run=bool(args.dry_run),
        full_translate=False,
        config_path=cfg_path,
    )

    # 启动即检查（一次）
    config_ok = _startup_check_config(root_dir, rt)

    # 非交互模式
    if args.action:
        action = args.action.strip().lower()
        if not config_ok and action != "init":
            return 2
        return _run_action(action, root_dir, rt)

    # 交互模式
    only_init = not config_ok

    while True:
        _print_menu(only_init)

        try:
            s = input("选择 > ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n退出。")
            return 0

        if not s:
            continue

        if s in ("0", "exit", "quit", "q"):
            print("退出。")
            return 0

        action = (ACTION_BY_KEY.get(s) or s).strip().lower()

        if only_init and action not in ("init", "exit"):
            print("[提示] 当前配置文件缺失或不合法，请先执行 init 修复配置。")
            continue

        code = _run_action(action, root_dir, rt)

        if action == "init":
            config_ok = _startup_check_config(root_dir, rt)
            only_init = not config_ok

        if code != 0:
            print(f"[提示] action 返回码：{code}（可继续选择其他操作）")


if __name__ == "__main__":
    raise SystemExit(main())
