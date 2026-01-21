from __future__ import annotations

import argparse
from pathlib import Path
from typing import List, Optional, Tuple

from .models import RuntimeOptions
from .actions_core import run_init, run_doctor, run_sort, run_check, run_clean

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


def _print_menu() -> None:
    print("\n请选择功能：")
    for k, _, label in MENU:
        print(f"  {k}. {label}")
    print("")


def _print_report(rep) -> None:
    # 结构化打印 Report（来自 models.py）
    print(f"\n== {rep.action} 结果 ==")
    if hasattr(rep, "counts_by_level"):
        c = rep.counts_by_level()
        print(f"issues: info={c.get('info', 0)} warn={c.get('warn', 0)} error={c.get('error', 0)}")
    if getattr(rep, "files_scanned", 0):
        print(f"files_scanned: {rep.files_scanned}")
    if getattr(rep, "files_changed", 0):
        print(f"files_changed: {rep.files_changed}")
    if getattr(rep, "keys_added", 0):
        print(f"keys_added: {rep.keys_added}")
    if getattr(rep, "keys_removed", 0):
        print(f"keys_removed: {rep.keys_removed}")
    if getattr(rep, "keys_translated", 0):
        print(f"keys_translated: {rep.keys_translated}")

    issues = getattr(rep, "issues", ()) or ()
    if issues:
        print("\n问题列表：")
        for i in issues:
            loc = ""
            if getattr(i, "path", None):
                loc = f"  ({i.path})"
            print(f"- [{i.level.value}] {i.code.value}: {i.message}{loc}")
            details = getattr(i, "details", None) or {}
            if details:
                # 简洁打印 details（不展开太深）
                print(f"    details: {details}")
    else:
        print("\n未发现问题。")

    ok = getattr(rep, "ok", True)
    print("\n状态：", "OK" if ok else "FAILED")
    print("")


def _run_action(action: str, root_dir: Path, rt: RuntimeOptions) -> int:
    action = (action or "").strip().lower()

    try:
        if action == "init":
            rep = run_init(root_dir=root_dir, rt=rt)
            _print_report(rep)
            return 0 if rep.ok else 2

        if action == "doctor":
            rep = run_doctor(root_dir=root_dir, rt=rt)
            _print_report(rep)
            return 0 if rep.ok else 2

        if action == "sort":
            rep = run_sort(root_dir=root_dir, rt=rt)
            _print_report(rep)
            return 0 if rep.ok else 2

        if action == "check":
            rep = run_check(root_dir=root_dir, rt=rt)
            _print_report(rep)
            return 0 if rep.ok else 2

        if action == "clean":
            rep = run_clean(root_dir=root_dir, rt=rt)
            _print_report(rep)
            return 0 if rep.ok else 2

        if action == "translate":
            print("[提示] translate 将在 actions_translate.py 中实现（下一步会接入 OpenAI 翻译底座）。")
            return 3

        print(f"[错误] 未知 action：{action}")
        return 3

    except NotImplementedError as e:
        print(f"[提示] 功能尚未实现：{e}")
        return 3
    except KeyboardInterrupt:
        print("\n退出。")
        return 0
    except Exception as e:
        print(f"[错误] 执行失败：{e}")
        return 2


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog=BOX_TOOL["name"], add_help=True)
    parser.add_argument("--action", default=None, help="直接执行动作：init/doctor/sort/check/clean/translate")
    parser.add_argument("--config", default=None, help="配置文件路径（默认 ./slang_i18n.yaml）")
    parser.add_argument("--root", default=".", help="项目根目录（默认当前目录）")
    parser.add_argument("--dry-run", action="store_true", help="演练模式：不写文件、不创建目录")
    args = parser.parse_args(argv)

    root_dir = Path(args.root).resolve()
    cfg_path = Path(args.config).resolve() if args.config else None

    rt = RuntimeOptions(
        dry_run=bool(args.dry_run),
        full_translate=False,
        config_path=cfg_path,
    )

    # 非交互：直接 action
    if args.action:
        return _run_action(args.action, root_dir, rt)

    # 交互菜单
    while True:
        _print_menu()
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

        action = ACTION_BY_KEY.get(s) or s  # 允许直接输入 action 名称
        code = _run_action(action, root_dir, rt)

        # 非 0 的返回码不直接退出，方便继续测试；需要退出就选 0
        if code != 0:
            print(f"[提示] action 返回码：{code}（继续可再次选择菜单）")


if __name__ == "__main__":
    raise SystemExit(main())
