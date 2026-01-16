from __future__ import annotations

import argparse
import sys

from box.commands.doctor import cmd_doctor
from box.commands.update import cmd_update
from box.commands.clean import cmd_clean
from box.commands.sync import cmd_sync


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="box",
        description="box: flywithbug 的命令行工具集（tools）",
    )
    sub = p.add_subparsers(dest="command", required=True)

    # doctor
    sp = sub.add_parser("doctor", help="诊断环境（python/pipx/path/配置目录）")
    sp.set_defaults(func=cmd_doctor)

    # update
    sp = sub.add_parser("update", help="更新 box 工具集（优先通过 pipx）")
    sp.add_argument("--verbose", action="store_true", help="输出更多信息")
    sp.set_defaults(func=cmd_update)

    # clean
    sp = sub.add_parser("clean", help="清理常见垃圾文件（默认当前目录，支持 dry-run）")
    sp.add_argument("path", nargs="?", default=".", help="要清理的目录（默认 .）")
    sp.add_argument("--dry-run", action="store_true", help="只显示将要删除的内容，不实际删除")
    sp.add_argument("--yes", action="store_true", help="不再二次确认（危险操作时才需要）")
    sp.set_defaults(func=cmd_clean)

    # sync
    sp = sub.add_parser("sync", help="同步目录/文件：SRC -> DST（支持 dry-run / delete）")
    sp.add_argument("src", help="源路径")
    sp.add_argument("dst", help="目标路径")
    sp.add_argument("--dry-run", action="store_true", help="只显示将要执行的动作")
    sp.add_argument("--delete", action="store_true", help="删除 DST 中 SRC 不存在的文件（需配合 --yes）")
    sp.add_argument("--yes", action="store_true", help="确认执行包含删除的同步")
    sp.set_defaults(func=cmd_sync)

    return p


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
