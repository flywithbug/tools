from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import List, Optional, Tuple

from .models import RuntimeOptions
from .actions_pub_upgrade import run_pub_scan, format_pub_scan_text


BOX_TOOL = {
    "id": "flutter.pub_upgrade",
    "name": "box_pub_upgrade",
    "category": "flutter",
    "summary": "Flutter/Dart 依赖升级工具（优先私有依赖）：scan/apply/verify",
    "usage": [
        "box_pub_upgrade",
        "box_pub_upgrade --action scan",
        "box_pub_upgrade --action scan --private",
        "box_pub_upgrade --action scan --private --public",
        "box_pub_upgrade --root ./my_app --action scan",
        "box_pub_upgrade --format json --action scan",
        "box_pub_upgrade --dry-run --action apply",
        "box_pub_upgrade --config ./pub_upgrade.yaml --action scan",
    ],
    "options": [
        {"flag": "--action", "desc": "直接执行动作：scan/apply/verify（不传则进入交互菜单）"},
        {"flag": "--config", "desc": "指定配置文件路径（默认 ./pub_upgrade.yaml，可选）"},
        {"flag": "--root", "desc": "项目根目录（默认当前目录）"},
        {"flag": "--dry-run", "desc": "演练模式：不写文件（scan 默认不写）"},
        {"flag": "--private", "desc": "只输出私有依赖的升级列表（scan）"},
        {"flag": "--public", "desc": "只输出公开依赖的升级列表（scan）"},
        {"flag": "--include-dev", "desc": "包含 dev_dependencies（默认开启）"},
        {"flag": "--use-resolvable", "desc": "目标版本用 resolvable（默认，更安全）"},
        {"flag": "--use-latest", "desc": "目标版本用 latest（更激进）"},
        {"flag": "--format", "desc": "输出格式：text/json（默认 text）"},
    ],
    "dependencies": [
        "PyYAML>=6.0",
    ],
    "docs": "README.md",
}


MENU: List[Tuple[str, str, str]] = [
    ("1", "scan", "扫描可升级依赖（scan：按私有/公开列出，忽略 path）"),
    ("2", "apply", "应用升级（apply：预留）"),
    ("3", "verify", "升级后验证（verify：预留）"),
    ("0", "exit", "退出"),
]
ACTION_BY_KEY = {k: action for k, action, _ in MENU}


def _print_menu() -> None:
    print("\n请选择功能：")
    for k, _, label in MENU:
        print(f"  {k}. {label}")
    print("")


def _run_action(action: str, root_dir: Path, rt: RuntimeOptions, *, show_private: bool, show_public: bool,
                include_dev: bool, use_resolvable: bool, out_format: str) -> int:
    action = (action or "").strip().lower()

    try:
        if action == "scan":
            rep = run_pub_scan(
                root_dir=root_dir,
                rt=rt,
                include_dev=include_dev,
                use_resolvable=use_resolvable,
            )

            if out_format == "json":
                payload = {
                    "action": rep.action,
                    "ok": rep.ok,
                    "private_updates": rep.private_updates,
                    "public_updates": rep.public_updates,
                    "ignored_path": rep.ignored_path,
                    "counts_by_level": rep.counts_by_level() if hasattr(rep, "counts_by_level") else None,
                }
                print(json.dumps(payload, ensure_ascii=False, indent=2))
            else:
                print(format_pub_scan_text(rep, show_private=show_private, show_public=show_public))

            return 0 if rep.ok else 2

        if action == "apply":
            print("[提示] apply 尚未实现。")
            return 3

        if action == "verify":
            print("[提示] verify 尚未实现。")
            return 3

        print(f"[错误] 未知 action：{action}")
        return 3

    except KeyboardInterrupt:
        print("\n退出。")
        return 0
    except Exception as e:
        print(f"[错误] 执行失败：{e}")
        return 2


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog=BOX_TOOL["name"], add_help=True)

    parser.add_argument("--action", default=None, help="直接执行动作：scan/apply/verify（不传则进入交互菜单）")
    parser.add_argument("--config", default=None, help="配置文件路径（默认 ./pub_upgrade.yaml，可选）")
    parser.add_argument("--root", default=".", help="项目根目录（默认当前目录）")
    parser.add_argument("--dry-run", action="store_true", help="演练模式：不写文件（scan 默认不写）")

    # scan 相关
    parser.add_argument("--private", action="store_true", help="只输出私有依赖（scan）")
    parser.add_argument("--public", action="store_true", help="只输出公开依赖（scan）")
    parser.add_argument("--include-dev", action="store_true", help="包含 dev_dependencies（默认开启）")
    parser.add_argument("--use-resolvable", action="store_true", help="目标版本用 resolvable（默认）")
    parser.add_argument("--use-latest", action="store_true", help="目标版本用 latest（更激进）")
    parser.add_argument("--format", default="text", choices=["text", "json"], help="输出格式：text/json（默认 text）")

    args = parser.parse_args(argv)

    root_dir = Path(args.root).resolve()
    cfg_path = Path(args.config).resolve() if args.config else None

    rt = RuntimeOptions(
        dry_run=bool(args.dry_run),
        full_translate=False,
        config_path=cfg_path,
    )

    # 输出开关：默认两个都输出；用户显式指定其一则只输出其一
    show_private = True
    show_public = True
    if args.private and not args.public:
        show_public = False
    if args.public and not args.private:
        show_private = False

    # include-dev：默认开启；若用户传了 --include-dev 也开启（为了兼容 argparse 现状）
    include_dev = True

    # use_resolvable 默认 True；若显式 --use-latest 则用 latest
    use_resolvable = True
    if args.use_latest:
        use_resolvable = False
    if args.use_resolvable:
        use_resolvable = True

    # 非交互：直接 action
    if args.action:
        return _run_action(
            args.action,
            root_dir,
            rt,
            show_private=show_private,
            show_public=show_public,
            include_dev=include_dev,
            use_resolvable=use_resolvable,
            out_format=args.format,
        )

    # 交互菜单：直接运行时走这里
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
        code = _run_action(
            action,
            root_dir,
            rt,
            show_private=show_private,
            show_public=show_public,
            include_dev=include_dev,
            use_resolvable=use_resolvable,
            out_format=args.format,
        )

        if code != 0:
            print(f"[提示] action 返回码：{code}（继续可再次选择菜单）")


if __name__ == "__main__":
    raise SystemExit(main())
