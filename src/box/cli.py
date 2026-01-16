from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

try:
    from box import __version__  # 来自 src/box/__init__.py
except Exception:
    __version__ = "0.0.0"


PKG_NAME = "box"  # pipx/pip 卸载与升级时用的包名（与你 pyproject 的 project.name 对应）


def run(cmd: list[str]) -> int:
    p = subprocess.run(cmd)
    return int(p.returncode)


def which(cmd: str) -> str | None:
    return shutil.which(cmd)


def cmd_help(parser: argparse.ArgumentParser, _args: argparse.Namespace) -> int:
    parser.print_help()
    return 0


def cmd_version(_parser: argparse.ArgumentParser, _args: argparse.Namespace) -> int:
    print(__version__)
    return 0


def cmd_doctor(_parser: argparse.ArgumentParser, _args: argparse.Namespace) -> int:
    print("== box doctor ==")
    print(f"python: {sys.executable}")
    print(f"python_version: {sys.version.split()[0]}")

    pipx = which("pipx")
    print(f"pipx: {pipx or 'NOT FOUND'}")

    box_bin = which("box")
    print(f"box: {box_bin or 'NOT FOUND'}")

    # 常见 PATH 问题提示
    path = os.environ.get("PATH", "")
    candidates = [
        str(Path.home() / ".local" / "bin"),
    ]
    missing = [c for c in candidates if Path(c).exists() and c not in path.split(":")]
    if missing:
        print("warn: PATH 可能缺少以下目录（可能导致 box/pipx 命令找不到）：")
        for m in missing:
            print(f"  - {m}")

    cfg = Path.home() / ".config" / "box"
    print(f"config_dir: {cfg} ({'exists' if cfg.exists() else 'missing'})")

    print("doctor: OK")
    return 0


def cmd_update(_parser: argparse.ArgumentParser, args: argparse.Namespace) -> int:
    print("== box update ==")
    pipx = which("pipx")
    if pipx:
        # 优先 pipx upgrade；失败则 reinstall
        rc = run([pipx, "upgrade", PKG_NAME])
        if rc == 0:
            print("update: OK (pipx upgrade)")
            return 0
        print("update: pipx upgrade failed, trying reinstall...")
        rc = run([pipx, "reinstall", PKG_NAME])
        if rc == 0:
            print("update: OK (pipx reinstall)")
            return 0
        print("update: FAILED (pipx)")
        return 1

    # 没有 pipx，给出清晰指引
    print("pipx not found.")
    print("建议：重新运行 install.sh（首次安装脚本）来修复/安装 pipx，然后再执行 box update。")
    return 2


def cmd_uninstall(_parser: argparse.ArgumentParser, _args: argparse.Namespace) -> int:
    print("== box uninstall ==")
    pipx = which("pipx")
    if pipx:
        rc = run([pipx, "uninstall", PKG_NAME])
        if rc == 0:
            print("uninstall: OK (pipx)")
            return 0
        print("uninstall: FAILED (pipx)")
        return 1

    # 没有 pipx 的保底提示
    print("pipx not found, cannot auto-uninstall safely.")
    print("如果你是用 pip 安装的，可以尝试：")
    print("  python3 -m pip uninstall box")
    return 2


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="box",
        description="box: flywithbug 的命令行工具集",
    )
    sub = p.add_subparsers(dest="command", required=True)

    sp = sub.add_parser("help", help="显示帮助")
    sp.set_defaults(handler=cmd_help)

    sp = sub.add_parser("doctor", help="诊断环境（python/pipx/path/配置目录）")
    sp.set_defaults(handler=cmd_doctor)

    sp = sub.add_parser("update", help="更新 box（优先使用 pipx）")
    sp.set_defaults(handler=cmd_update)

    sp = sub.add_parser("version", help="显示版本")
    sp.set_defaults(handler=cmd_version)

    sp = sub.add_parser("uninstall", help="卸载 box（优先使用 pipx）")
    sp.set_defaults(handler=cmd_uninstall)

    return p


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    parser = build_parser()
    args = parser.parse_args(argv)

    # help 需要拿到 parser 本体
    handler = getattr(args, "handler", None)
    if handler is None:
        parser.print_help()
        return 2
    return int(handler(parser, args))


if __name__ == "__main__":
    raise SystemExit(main())
