from __future__ import annotations

import argparse
import importlib
import importlib.metadata as md
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional


try:
    # 从包内 __init__ 取版本（避免循环 import 风险）
    from . import __version__
except Exception:
    __version__ = "0.0.0"


BOX_TOOL = {
    "id": "core.box",
    "name": "box",
    "category": "core",
    "summary": "工具集管理入口：诊断、更新、版本查看、卸载、工具列表",
    "usage": [
        "box --help",
        "box help",
        "box doctor",
        "box update",
        "box version",
        "box uninstall",
        "box tools",
        "box tools --full",
    ],
    "options": [
        {"flag": "--help", "desc": "显示帮助（等同 box help）"},
        {"flag": "tools --full", "desc": "显示工具的详细信息（options/examples），并显示导入失败原因"},
    ],
    "examples": [
        {"cmd": "box doctor", "desc": "诊断环境（python/pipx/PATH）"},
        {"cmd": "box update", "desc": "更新工具集"},
        {"cmd": "box tools", "desc": "列出当前工具与简介"},
    ],
    # ✅ 约定：所有工具 docs 都用 README.md，稳定、适合组件化
    "docs": "README.md",
}


# ----------------------------
# 基础工具函数
# ----------------------------

def which(cmd: str) -> str | None:
    return shutil.which(cmd)


def run(cmd: list[str]) -> int:
    p = subprocess.run(cmd)
    return int(p.returncode)


def _ep_module_from_value(value: str) -> str:
    # "box_tools.flutter.pub_version:main" -> "box_tools.flutter.pub_version"
    return value.split(":", 1)[0].strip()


def _safe_get(d: dict, key: str, default=None):
    v = d.get(key, default)
    return v if v is not None else default


# ----------------------------
# Distribution name 探测
# ----------------------------

def _find_dist_name_by_console_script(script_name: str = "box") -> Optional[str]:
    """
    在所有已安装 distributions 中，寻找提供 console_script=script_name 的那个 dist。
    这是“最稳”的自动探测方式：无论发行包叫什么，只要它提供了 box 入口，就能找出来。
    """
    try:
        for dist in md.distributions():
            try:
                eps = list(dist.entry_points)
            except Exception:
                continue

            for ep in eps:
                if ep.group != "console_scripts":
                    continue
                if ep.name != script_name:
                    continue

                # 进一步过滤：入口模块应当来自 box 包（避免系统里恰好也有另一个 box 命令）
                mod = _ep_module_from_value(ep.value)
                if mod == "box.cli" or mod.startswith("box."):
                    return dist.metadata.get("Name") or dist.name
        return None
    except Exception:
        return None


def get_dist_name() -> str:
    """
    获取发行包名（distribution name）。
    优先顺序：
    1) 环境变量 BOX_DIST_NAME（你可以在 CI/本地强制指定）
    2) 自动探测：谁提供了 console_script=box 且模块来自 box.*
    3) 最后兜底：返回 "box"
    """
    env = os.environ.get("BOX_DIST_NAME", "").strip()
    if env:
        return env

    guessed = _find_dist_name_by_console_script("box")
    if guessed:
        return guessed

    return "box"


# ----------------------------
# 输出格式
# ----------------------------

def _format_tool_card(tool: dict, full: bool) -> str:
    category = _safe_get(tool, "category", "").strip()
    name = _safe_get(tool, "name", "").strip()
    summary = _safe_get(tool, "summary", "").strip()
    docs = _safe_get(tool, "docs", "").strip()

    header = f"{category} / {name}" if category else name
    lines: list[str] = [f"- {header}"]

    if summary:
        lines.append(f"  {summary}")

    usage = _safe_get(tool, "usage", [])
    if usage:
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
                    lines.append(f"    {flag:<14} {desc}".rstrip())

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


# ----------------------------
# 子命令
# ----------------------------

def cmd_help(parser: argparse.ArgumentParser, _args: argparse.Namespace) -> int:
    parser.print_help()
    return 0


def cmd_version(_parser: argparse.ArgumentParser, _args: argparse.Namespace) -> int:
    print(__version__)
    return 0


def cmd_doctor(_parser: argparse.ArgumentParser, _args: argparse.Namespace) -> int:
    dist_name = get_dist_name()

    print("== box doctor ==")
    print(f"python: {sys.executable}")
    print(f"python_version: {sys.version.split()[0]}")
    print(f"dist_name: {dist_name}")

    pipx = which("pipx")
    print(f"pipx: {pipx or 'NOT FOUND'}")

    box_bin = which("box")
    print(f"box: {box_bin or 'NOT FOUND'}")

    # 常见 PATH 问题提示（pipx 常用目录）
    path = os.environ.get("PATH", "")
    candidates = [str(Path.home() / ".local" / "bin")]
    missing = [c for c in candidates if Path(c).exists() and c not in path.split(":")]
    if missing:
        print("warn: PATH 可能缺少以下目录（可能导致 box/pipx 命令找不到）：")
        for m in missing:
            print(f"  - {m}")

    cfg = Path.home() / ".config" / "box"
    print(f"config_dir: {cfg} ({'exists' if cfg.exists() else 'missing'})")

    # 额外：确认 dist 是否存在
    try:
        md.distribution(dist_name)
        print("dist: OK")
    except md.PackageNotFoundError:
        print("dist: NOT FOUND (可能是发行包名不一致或未正确安装)")
        print("hint: 检查 pyproject.toml 的 [project].name，并可用环境变量 BOX_DIST_NAME 强制指定。")

    print("doctor: OK")
    return 0


def cmd_update(_parser: argparse.ArgumentParser, _args: argparse.Namespace) -> int:
    dist_name = get_dist_name()

    print("== box update ==")
    print(f"dist_name: {dist_name}")

    pipx = which("pipx")
    if pipx:
        rc = run([pipx, "upgrade", dist_name])
        if rc == 0:
            print("update: OK (pipx upgrade)")
            return 0

        print("update: pipx upgrade failed, trying reinstall...")
        rc = run([pipx, "reinstall", dist_name])
        if rc == 0:
            print("update: OK (pipx reinstall)")
            return 0

        print("update: FAILED (pipx)")
        return 1

    print("pipx not found.")
    print("建议：重新运行 install.sh（首次安装脚本）来修复/安装 pipx，然后再执行 box update。")
    print("如果你是用 pip 安装的，可以尝试：")
    print(f"  python3 -m pip install -U {dist_name}")
    return 2


def cmd_uninstall(_parser: argparse.ArgumentParser, _args: argparse.Namespace) -> int:
    dist_name = get_dist_name()

    print("== box uninstall ==")
    print(f"dist_name: {dist_name}")

    pipx = which("pipx")
    if pipx:
        rc = run([pipx, "uninstall", dist_name])
        if rc == 0:
            print("uninstall: OK (pipx)")
            return 0
        print("uninstall: FAILED (pipx)")
        return 1

    print("pipx not found, cannot auto-uninstall safely.")
    print("如果你是用 pip 安装的，可以尝试：")
    print(f"  python3 -m pip uninstall {dist_name}")
    return 2


def cmd_tools(_parser: argparse.ArgumentParser, args: argparse.Namespace) -> int:
    """
    自动列出当前工具集发布的 console scripts，并优先读取每个工具模块里的 BOX_TOOL 标准信息。
    """
    full = bool(getattr(args, "full", False))
    dist_name = get_dist_name()

    print("== box tools ==")
    print(f"dist_name: {dist_name}")

    try:
        dist = md.distribution(dist_name)
    except md.PackageNotFoundError:
        print(f"❌ 找不到已安装包元数据：{dist_name}")
        print("hint: 检查 pyproject.toml 的 [project].name，并可用环境变量 BOX_DIST_NAME 强制指定。")
        return 2

    eps = list(dist.entry_points)
    scripts = [ep for ep in eps if ep.group == "console_scripts"]

    if not scripts:
        print("未发现该工具集发布的命令入口点。")
        return 0

    # 排序：box 放最前，其它按名字
    scripts.sort(key=lambda ep: (0 if ep.name == "box" else 1, ep.name))

    for ep in scripts:
        name = ep.name
        value = ep.value
        module_name = _ep_module_from_value(value)

        tool_info = None
        import_err: Optional[str] = None

        try:
            mod = importlib.import_module(module_name)
            tool_info = getattr(mod, "BOX_TOOL", None)
        except Exception as e:
            tool_info = None
            import_err = f"{type(e).__name__}: {e}"

        if isinstance(tool_info, dict):
            declared_name = str(tool_info.get("name", "")).strip()
            if declared_name and declared_name != name:
                print(f"- {name}")
                print(f"  ⚠️ BOX_TOOL.name='{declared_name}' 与入口命令名不一致")
                print(f"  entry: {value}")
                continue

            print(_format_tool_card(tool_info, full))
        else:
            print(f"- {name}")
            print(f"  entry: {value}")
            if full and import_err:
                print(f"  import_error: {import_err}")
            if name == "box":
                print("  about: toolset manager (use `box help`)")

    if not full:
        print("\n提示：使用 `box tools --full` 查看 options / examples 等详细信息，并显示导入失败原因。")
    return 0


# ----------------------------
# CLI 组装
# ----------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="box",
        description="box: 工具集管理入口",
    )
    sub = p.add_subparsers(dest="command")

    sp = sub.add_parser("help", help="显示帮助")
    sp.set_defaults(handler=cmd_help)

    sp = sub.add_parser("doctor", help="诊断环境（python/pipx/path/配置目录）")
    sp.set_defaults(handler=cmd_doctor)

    sp = sub.add_parser("update", help="更新工具集（优先使用 pipx）")
    sp.set_defaults(handler=cmd_update)

    sp = sub.add_parser("version", help="显示版本")
    sp.set_defaults(handler=cmd_version)

    sp = sub.add_parser("uninstall", help="卸载工具集（优先使用 pipx）")
    sp.set_defaults(handler=cmd_uninstall)

    sp = sub.add_parser("tools", help="列出工具集中的工具与简介（读取 BOX_TOOL 标准信息）")
    sp.add_argument("--full", action="store_true", help="显示 options/examples 等详细信息，并显示导入失败原因")
    sp.set_defaults(handler=cmd_tools)

    return p


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    parser = build_parser()

    # box（无参数）时，等同 --help
    if not argv:
        parser.print_help()
        return 0

    args = parser.parse_args(argv)
    handler = getattr(args, "handler", None)

    if handler is None:
        parser.print_help()
        return 0

    return int(handler(parser, args))


if __name__ == "__main__":
    raise SystemExit(main())
