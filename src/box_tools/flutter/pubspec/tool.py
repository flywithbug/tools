#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional


# ----------------------------
# BOX_TOOL（对齐示例结构）
# ----------------------------
BOX_TOOL = {
    "id": "flutter.pubspec",
    "name": "pubspec",
    "category": "flutter",
    "summary": (
        "Flutter pubspec.yaml 管理 CLI：支持 version 升级（patch/minor）、"
        "依赖升级（基于 flutter pub outdated --json 的计划/执行）、"
        "依赖发布（flutter pub publish / dry-run），以及 doctor 本地检查。"
        "修改 pubspec.yaml 时只做最小必要的文本级局部替换，保留原有注释与结构。"
    ),
    "usage": [
        "pubspec",
        "pubspec upgrade",
        "pubspec publish",
        "pubspec version",
        "pubspec doctor",
        "pubspec version --mode patch --yes",
        "pubspec version --mode minor",
        "pubspec upgrade --dry-run",
        "pubspec upgrade --outdated-json outdated.json",
        "pubspec --project-root path/to/project",
        "pubspec --pubspec path/to/pubspec.yaml doctor",
    ],
    "options": [
        {"flag": "command", "desc": "子命令：menu/upgrade/publish/version/doctor（默认 menu）"},
        {"flag": "--project-root", "desc": "项目根目录（默认当前目录）"},
        {"flag": "--pubspec", "desc": "pubspec.yaml 路径（默认 project-root/pubspec.yaml）"},
        {"flag": "--outdated-json", "desc": "指定 flutter pub outdated --json 的输出文件（可选，用于离线/复用）"},
        {"flag": "--dry-run", "desc": "只打印计划/预览，不写入文件，不执行危险操作"},
        {"flag": "--yes", "desc": "跳过所有确认（适合 CI/脚本）"},
        {"flag": "--no-interactive", "desc": "关闭交互菜单（脚本模式）"},
        {"flag": "--mode", "desc": "version：show/patch/minor（脚本模式快捷入口）"},
    ],
    "examples": [
        {"cmd": "pubspec", "desc": "进入交互菜单"},
        {"cmd": "pubspec doctor", "desc": "本地检查：pubspec 是否存在/字段规范/环境可用"},
        {"cmd": "pubspec version --mode patch --yes", "desc": "补丁版本自增并直接写入（只改 version 行）"},
        {"cmd": "pubspec version --mode minor", "desc": "小版本自增（只改 version 行，默认需要确认）"},
        {"cmd": "pubspec upgrade", "desc": "进入依赖升级子菜单（scan/apply）"},
        {"cmd": "pubspec upgrade --outdated-json outdated.json", "desc": "使用已有 outdated.json 生成升级计划"},
        {"cmd": "pubspec publish", "desc": "进入发布子菜单（check/dry-run/publish）"},
    ],
    "dependencies": [
        "Flutter SDK（flutter 可执行）",
    ],
    "docs": "README.md",
}


# ----------------------------
# Context：统一运行上下文（尽量少）
# ----------------------------
@dataclass(frozen=True)
class Context:
    project_root: Path
    pubspec_path: Path
    outdated_json_path: Optional[Path]
    dry_run: bool
    yes: bool
    interactive: bool

    echo: Callable[[str], None]
    confirm: Callable[[str], bool]


# ----------------------------
# IO：读写（原子写入，无 .bak 备份）
# 注意：调用者必须只做最小必要的文本替换，避免破坏注释/结构
# ----------------------------
def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def write_text_atomic(path: Path, content: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_text(content, encoding="utf-8")
        tmp.replace(path)
    finally:
        try:
            if tmp.exists():
                tmp.unlink()
        except Exception:
            pass


# ----------------------------
# Shell：命令执行（薄封装）
# ----------------------------
@dataclass(frozen=True)
class CmdResult:
    code: int
    out: str
    err: str


def run_cmd(cmd: list[str], cwd: Path, capture: bool = True) -> CmdResult:
    p = subprocess.run(
        cmd,
        cwd=str(cwd),
        capture_output=capture,
        text=True,
        check=False,
        env=os.environ.copy(),
    )
    return CmdResult(code=p.returncode, out=p.stdout or "", err=p.stderr or "")


def flutter_pub_outdated_json(ctx: Context) -> dict:
    r = run_cmd(["flutter", "pub", "outdated", "--json"], cwd=ctx.project_root, capture=True)
    if r.code != 0:
        raise RuntimeError((r.err.strip() or r.out.strip() or "flutter pub outdated 执行失败"))
    return json.loads(r.out)


def flutter_pub_publish(ctx: Context, dry_run: bool) -> CmdResult:
    cmd = ["flutter", "pub", "publish"]
    if dry_run:
        cmd.append("--dry-run")
    return run_cmd(cmd, cwd=ctx.project_root, capture=True)


# ----------------------------
# CLI / Menu
# ----------------------------
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="pubspec")
    p.add_argument(
        "command",
        nargs="?",
        default="menu",
        choices=["menu", "upgrade", "publish", "version", "doctor"],
        help="子命令",
    )
    p.add_argument("--project-root", default=".", help="项目根目录（默认当前目录）")
    p.add_argument("--pubspec", default=None, help="pubspec.yaml 路径（默认 project-root/pubspec.yaml）")
    p.add_argument("--outdated-json", default=None, help="outdated json 文件路径（可选）")

    p.add_argument("--dry-run", action="store_true", help="只预览，不写入/不发布")
    p.add_argument("--yes", action="store_true", help="跳过确认（适合 CI）")
    p.add_argument("--no-interactive", action="store_true", help="关闭交互菜单（脚本模式）")

    # version 脚本模式
    p.add_argument("--mode", default=None, choices=["show", "patch", "minor"], help="version：show/patch/minor")
    return p


def _mk_ctx(args) -> Context:
    project_root = Path(args.project_root).resolve()
    pubspec_path = Path(args.pubspec).resolve() if args.pubspec else (project_root / "pubspec.yaml").resolve()
    outdated_json_path = Path(args.outdated_json).resolve() if args.outdated_json else None

    def echo(msg: str) -> None:
        print(msg)

    def confirm(prompt: str) -> bool:
        if args.yes:
            return True
        ans = input(f"{prompt} (y/N) ").strip().lower()
        return ans in ("y", "yes")

    return Context(
        project_root=project_root,
        pubspec_path=pubspec_path,
        outdated_json_path=outdated_json_path,
        dry_run=bool(args.dry_run),
        yes=bool(args.yes),
        interactive=(not args.no_interactive),
        echo=echo,
        confirm=confirm,
    )


def run_menu(ctx: Context) -> int:
    menu = [
        ("upgrade", "依赖升级"),
        ("publish", "依赖发布"),
        ("version", "版本升级"),
        ("doctor", "环境检测"),
    ]

    while True:
        ctx.echo("\n=== pubspec ===")
        for i, (cmd, label) in enumerate(menu, start=1):
            ctx.echo(f"{i}. {cmd:<10} {label}")
        ctx.echo("0. exit       退出")

        choice = input("> ").strip()
        if choice == "0":
            return 0
        if not choice.isdigit() or not (1 <= int(choice) <= len(menu)):
            ctx.echo("无效选择")
            continue

        cmd = menu[int(choice) - 1][0]
        argv = ["pubspec", cmd, "--project-root", str(ctx.project_root), "--pubspec", str(ctx.pubspec_path)]
        if ctx.outdated_json_path:
            argv += ["--outdated-json", str(ctx.outdated_json_path)]
        if ctx.dry_run:
            argv += ["--dry-run"]
        if ctx.yes:
            argv += ["--yes"]
        if not ctx.interactive:
            argv += ["--no-interactive"]
        return main(argv)


def ensure_pubspec_exists(ctx: Context) -> None:
    if not ctx.pubspec_path.exists():
        raise FileNotFoundError(f"pubspec.yaml 不存在：{ctx.pubspec_path}")


def main(argv=None) -> int:
    argv = argv or sys.argv
    args = build_parser().parse_args(argv[1:])
    ctx = _mk_ctx(args)

    # doctor 允许在缺少 pubspec 时也能跑（会提示/报错但不直接崩）
    if args.command != "doctor":
        try:
            ensure_pubspec_exists(ctx)
        except Exception as e:
            ctx.echo(f"❌ {e}")
            return 2

    if args.command == "menu":
        return run_menu(ctx)

    if args.command == "doctor":
        from .doctor import run_menu as doctor_menu
        return doctor_menu(ctx)

    if args.command == "version":
        from .pub_version import run as version_run, run_menu as version_menu
        if args.mode:
            return version_run(ctx, mode=args.mode)
        return version_menu(ctx)

    if args.command == "upgrade":
        from .pub_upgrade import run as upgrade_run
        return upgrade_run(ctx)


    if args.command == "publish":
        from .pub_publish import run_menu as publish_menu
        return publish_menu(ctx)

    ctx.echo("未知命令")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
