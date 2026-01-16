from __future__ import annotations

import argparse
import importlib
import importlib.metadata as md
import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

try:
    from box import __version__
except Exception:
    __version__ = "0.0.0"


# 这里是 distribution 名称（与你 pyproject.toml [project].name 对齐）
PKG_NAME = "box"


def which(cmd: str) -> str | None:
    return shutil.which(cmd)


def run(cmd: list[str]) -> int:
    p = subprocess.run(cmd)
    return int(p.returncode)


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

    # 常见 PATH 问题提示（pipx 常用目录）
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


def cmd_update(_parser: argparse.ArgumentParser, _args: argparse.Namespace) -> int:
    print("== box update ==")
    pipx = which("pipx")
    if pipx:
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

    print("pipx not found, cannot auto-uninstall safely.")
    print("如果你是用 pip 安装的，可以尝试：")
    print("  python3 -m pip uninstall box")
    return 2


def _indent(text: str, prefix: str = "  ") -> str:
    return textwrap.indent(text, prefix)


def _safe_get(d: dict, key: str, default=None):
    v = d.get(key, default)
    return v if v is not None else default


def _format_tool_card(tool: dict, full: bool) -> str:
    """
    tool: BOX_TOOL dict
    """
    category = _safe_get(tool, "category", "").strip()
    name = _safe_get(tool, "name", "").strip()
    summary = _safe_get(tool, "summary", "").strip()
    docs = _safe_get(tool, "docs", "").strip()

    header = f"{category} / {name}" if category else name
    lines = [f"- {header}"]

    if summary:
        lines.append(f"  {summary}")

    usage = _safe_get(tool, "usage", [])
    if usage:
        # 简洁模式只显示前三条，full 显示全部
        show = usage if full else usage[:3]
        lines.append("  usage:")
        for u in show:
            lines.append(f"    {u}")
        if (not full) and len(usage) > 3:
            lines.append(f"    ... ({len(usage) - 3} more)")

    if full:
        options = _safe_get(tool, "options", [])
        if options:
            lines.append("  options:")
            for opt in options:
                flag = _safe_get(opt, "flag", "").strip()
                desc = _safe_get(opt, "desc", "").strip()
                if flag:
                    lines.append(f"    {flag:<12} {desc}".rstrip())

        examples = _safe_get(tool, "examples", [])
        if examples:
            lines.append("  examples:")
            for ex in examples:
                cmd = _safe_get(ex, "cmd", "").strip()
                desc = _safe_get(ex, "desc", "").strip()
                if cmd:
                    if desc:
                        lines.append(f"    {cmd}    # {desc}")
                    else:
                        lines.append(f"    {cmd}")

    if docs:
        lines.append(f"  docs: {docs}")

    return "\n".join(lines)


def _ep_module_from_value(value: str) -> str:
    # "box_tools.flutter.pub_version:main" -> "box_tools.flutter.pub_version"
    return value.split(":", 1)[0].strip()


def cmd_tools(_parser: argparse.ArgumentParser, args: argparse.Namespace) -> int:
    """
    自动列出当前工具集发布的 console scripts，并优先读取每个工具模块里的 BOX_TOOL 标准信息。
    """
    full = bool(getattr(args, "full", False))
    print("== box tools ==")
    print(f"package: {PKG_NAME}")

    try:
        dist = md.distribution(PKG_NAME)
    except md.PackageNotFoundError:
        print(f"❌ 找不到已安装包元数据：{PKG_NAME}")
        return 2

    eps = list(dist.entry_points)
    scripts = [ep for ep in eps if ep.group == "console_scripts"]

    if not scripts:
        print("未发现该工具集发布的命令入口点。")
        return 0

    # 排序：box 放最前，其它按名字
    scripts.sort(key=lambda ep: (0 if ep.name == "box" else 1, ep.name))

    for ep in scripts:
        # ep.name = 命令名，ep.value = 模块:函数
        name = ep.name
        value = ep.value

        # 只展示你定义的工具命令；box 本身也展示，但不会强依赖 BOX_TOOL
        module_name = _ep_module_from_value(value)

        tool_info = None
        try:
            mod = importlib.import_module(module_name)
            tool_info = getattr(mod, "BOX_TOOL", None)
        except Exception:
            tool_info = None

        if isinstance(tool_info, dict):
            # 标准卡片输出
            # 确保 name 一致（不一致也不阻塞，但会提示）
            declared_name = str(tool_info.get("name", "")).strip()
            if declared_name and declared_name != name:
                # 轻提示：避免你未来维护时踩坑
                print(f"- {name}")
                print(f"  ⚠️ BOX_TOOL.name='{declared_name}' 与入口命令名不一致")
                print(f"  entry: {value}")
                continue

            print(_format_tool_card(tool_info, full))
        else:
            # fallback：没有 BOX_TOOL 的命令（例如 box 本体）
            print(f"- {name}")
            print(f"  entry: {value}")
            if name == "box":
                print("  about: toolset manager (use `box help`)")

    if not full:
        print("\n提示：使用 `box tools --full` 查看 options / examples 等详细信息。")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="box",
        description="box: 工具集管理入口",
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

    sp = sub.add_parser("tools", help="列出工具集中的工具与简介（读取 BOX_TOOL 标准信息）")
    sp.add_argument("--full", action="store_true", help="显示 options/examples 等详细信息")
    sp.set_defaults(handler=cmd_tools)

    return p


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    parser = build_parser()
    args = parser.parse_args(argv)

    handler = getattr(args, "handler", None)
    if handler is None:
        parser.print_help()
        return 2

    # help 需要 parser 本体
    return int(handler(parser, args))


if __name__ == "__main__":
    raise SystemExit(main())
