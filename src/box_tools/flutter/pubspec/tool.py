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

from _share.tool_spec import tool, opt, ex 

BOX_TOOL = tool(
    id="flutter.box_pubspec",
    name="box_pubspec",
    category="flutter",
    summary=(
        "Flutter pubspec.yaml 管理 CLI：支持 version 升级（patch/minor）、"
        "依赖升级（基于 flutter pub outdated --json 的计划/执行）、"
        "依赖发布（flutter pub publish / dry-run），以及 doctor 本地检查。"
        "修改 pubspec.yaml 时只做最小必要的文本级局部替换，保留原有注释与结构。"
        "启动时会自动执行 doctor：无问题静默，有问题中断并输出错误。"
    ),
    usage=[
        "box_pubspec",
        "box_pubspec upgrade",
        "box_pubspec publish",
        "box_pubspec version",
        "box_pubspec doctor",
        "box_pubspec upgrade --yes",
        "box_pubspec upgrade --outdated-json outdated.json",
        "box_pubspec --project-root path/to/project",
        "box_pubspec --box_pubspec path/to/pubspec.yaml doctor",
    ],
    options=[
        opt("command", "子命令：menu/upgrade/publish/version/doctor（默认 menu）"),
        opt("--project-root", "项目根目录（默认当前目录）"),
        opt("--box_pubspec", "pubspec.yaml 路径（默认 project-root/pubspec.yaml）"),
        opt("--outdated-json", "指定 flutter pub outdated --json 的输出文件（可选，用于离线/复用）"),
        opt("--dry-run", "只打印计划/预览，不写入文件，不执行危险操作"),
        opt("--yes", "跳过所有确认（适合 CI/脚本）"),
        opt("--no-interactive", "关闭交互菜单（脚本模式）"),
        opt("--mode", "version：show/patch/minor（脚本模式快捷入口）"),
    ],
    examples=[
        ex("box_pubspec", "进入交互菜单（启动时自动 doctor；无问题不输出）"),
        ex("box_pubspec doctor", "手动运行 doctor（会输出详细检查结果）"),
        ex("box_pubspec upgrade", "执行依赖升级（默认直接 apply + pub get + analyze + 自动提交）"),
        ex("box_pubspec upgrade --outdated-json outdated.json", "使用已有 outdated.json"),
        ex("box_pubspec upgrade --yes", "无交互执行升级"),
        ex("box_pubspec version --mode patch --yes", "补丁版本自增并直接写入（只改 version 行）"),
    ],
    dependencies=[],
    docs="README.md",  # 可省略：默认就是 README.md
)



# ----------------------------
# Context：统一运行上下文
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


def run_cmd(cmd: list[str], cwd: Path | str, capture: bool = True) -> CmdResult:
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
    """执行 `flutter pub outdated --show-all --json` 并解析 JSON 返回。"""
    r = run_cmd(["flutter", "pub", "outdated", "--show-all", "--json"], cwd=ctx.project_root, capture=True)
    if r.code != 0:
        raise RuntimeError((r.err.strip() or r.out.strip() or "flutter pub outdated 执行失败"))
    return json.loads(r.out)


# ----------------------------
# CLI / Menu
# ----------------------------
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="box_pubspec")
    p.add_argument(
        "command",
        nargs="?",
        default="menu",
        choices=["menu", "upgrade", "publish", "version", "doctor"],
        help="子命令",
    )
    p.add_argument("--project-root", default=".", help="项目根目录（默认当前目录）")
    p.add_argument("--box_pubspec", default=None, help="pubspec.yaml 路径（默认 project-root/pubspec.yaml）")
    p.add_argument("--outdated-json", default=None, help="outdated json 文件路径（可选）")

    p.add_argument("--dry-run", action="store_true", help="只预览，不写入/不发布")
    p.add_argument("--yes", action="store_true", help="跳过确认（适合 CI）")
    p.add_argument("--no-interactive", action="store_true", help="关闭交互菜单（脚本模式）")

    # version 脚本模式
    p.add_argument("--mode", default=None, choices=["show", "patch", "minor"], help="version：show/patch/minor")
    return p


def _mk_ctx(args) -> Context:
    project_root = Path(args.project_root).resolve()
    pubspec_path = Path(args.box_pubspec).resolve() if args.box_pubspec else (project_root / "pubspec.yaml").resolve()
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
        ctx.echo("\n=== box_pubspec ===")
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
        argv = ["box_pubspec", cmd, "--project-root", str(ctx.project_root), "--box_pubspec", str(ctx.pubspec_path)]
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


def run_startup_doctor(ctx: Context, *, allow_failure: bool = False) -> bool:
    """启动时自动执行 doctor。

    - 通过：静默（不输出）
    - 不通过：
        - allow_failure=False：抛错阻断（默认行为）
        - allow_failure=True：打印问题但不阻断（用于 publish，让用户稍后选择是否继续）
    """
    from .doctor import collect

    ok, warnings, errors = collect(ctx)
    if ok:
        return True

    # 有问题：先把清单打印出来
    if warnings:
        for w in warnings:
            print(f"⚠️  {w}" if not w.startswith(("⚠️", "❌")) else w)
    if errors:
        for e in errors:
            print(f"❌ {e}" if not e.startswith(("⚠️", "❌")) else e)

    if allow_failure:
        print("ℹ️  doctor 未通过：publish 时会再次进行 doctor 闸门检查，并让你选择是否继续。")
        return False

    print("❌ doctor 未通过")
    raise SystemExit(1)

def main(argv=None) -> int:
    argv = argv or sys.argv
    args = build_parser().parse_args(argv[1:])
    ctx = _mk_ctx(args)

    try:
        # 启动即 doctor：
        # - doctor 命令本身不做静默拦截（用户就是来看的）
        # - publish 仍会提前跑 doctor，有问题先提示；真正的“是否继续发布”由 publish flow 决定
        if args.command != "doctor":
            run_startup_doctor(ctx, allow_failure=(args.command == "publish"))

        # doctor 允许在缺少 box_pubspec 时也能跑（会提示/报错但不直接崩）
        if args.command != "doctor":
            ensure_pubspec_exists(ctx)

        if args.command == "menu":
            return run_menu(ctx)

        if args.command == "doctor":
            from .doctor import run as doctor_menu
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

    except KeyboardInterrupt:
        ctx.echo("\n已取消。")
        return 130
    except Exception as e:
        ctx.echo(f"❌ {e}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
