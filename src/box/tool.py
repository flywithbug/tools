from __future__ import annotations

import argparse
import importlib
import importlib.metadata as md
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional, Any


try:
    # 从包内 __init__ 取版本（避免循环 import 风险）
    from . import __version__
except Exception:
    __version__ = "0.0.0"

from _share.tool_spec import tool, opt, ex

BOX_TOOL = tool(
    id="core.box",
    name="box",
    category="core",
    summary="工具集管理入口：诊断、更新、版本查看、卸载、工具列表",
    usage=[
        "box --help",
        "box help",
        "box doctor",
        "box update",
        "box version",
        "box uninstall",
        "box tools",
        "box tools --full",
    ],
    options=[
        opt("--help", "显示帮助（等同 box help）"),
        opt("tools --full", "显示工具的详细信息（options/examples），并显示导入失败原因"),
    ],
    examples=[
        ex("box doctor", "诊断环境（python/pipx/PATH）"),
        ex("box update", "更新工具集"),
        ex("box tools", "列出当前工具与简介"),
    ],
    docs="README.md",  # 也可以省略（tool() 默认就是 README.md）
)


# ----------------------------
# 基础工具函数
# ----------------------------

def which(cmd: str) -> str | None:
    return shutil.which(cmd)


def _run_capture(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, capture_output=True, text=True)


def run(cmd: list[str]) -> int:
    """
    运行命令并返回 returncode。失败时尽量打印 stderr，方便定位问题。
    """
    try:
        p = subprocess.run(cmd, text=True, capture_output=True)
        if p.returncode != 0:
            stderr = (p.stderr or "").strip()
            stdout = (p.stdout or "").strip()
            print(f"[cmd] {' '.join(cmd)}")
            if stdout:
                print(stdout)
            if stderr:
                print(stderr)
        return int(p.returncode)
    except FileNotFoundError:
        print(f"[cmd] NOT FOUND: {cmd[0]}")
        return 127
    except Exception as e:
        print(f"[cmd] ERROR: {' '.join(cmd)} -> {type(e).__name__}: {e}")
        return 1


def _ep_module_from_value(value: str) -> str:
    # "box_tools.flutter.pub_version:main" -> "box_tools.flutter.pub_version"
    return value.split(":", 1)[0].strip()


def _safe_get(d: dict, key: str, default=None):
    v = d.get(key, default)
    return v if v is not None else default


# ----------------------------
# pipx 探测（用于避免“升级错包”）
# ----------------------------

def _pipx_list_json(pipx_bin: str) -> Optional[dict]:
    """
    尝试读取 `pipx list --json` 输出。
    """
    try:
        p = _run_capture([pipx_bin, "list", "--json"])
        if p.returncode != 0:
            return None
        return json.loads(p.stdout or "{}")
    except Exception:
        return None


def _pipx_find_package_for_app(app_name: str, pipx_bin: Optional[str] = None) -> Optional[str]:
    """
    在 pipx 管理的 venv 列表中，找到提供 app_name（如 box）的那个 package 名（distribution name）。
    这能显著降低“pyenv shim 抢占导致升级/卸载错包”的概率。
    """
    pipx_bin = pipx_bin or which("pipx")
    if not pipx_bin:
        return None

    data = _pipx_list_json(pipx_bin)
    if not data:
        return None

    venvs: dict = data.get("venvs", {}) or {}
    for _venv_name, info in venvs.items():
        # info 示例字段（不同版本 pipx 会略有差异）
        # - "package": "<dist_name>"
        # - "metadata": {"main_package": {"package": "<dist_name>", ...}, ...}
        # - "apps": ["box", "box_xxx", ...]
        apps = info.get("apps") or []
        if app_name in apps:
            pkg = info.get("package")
            if isinstance(pkg, str) and pkg.strip():
                return pkg.strip()

            md_info = info.get("metadata") or {}
            main_pkg = (md_info.get("main_package") or {}).get("package")
            if isinstance(main_pkg, str) and main_pkg.strip():
                return main_pkg.strip()

    return None


# ----------------------------
# Distribution name 探测（非 pipx 的兜底）
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
    2) 如果 pipx 可用：在 pipx list 里找提供 `box` app 的发行包名（更可靠）
    3) 自动探测：谁提供了 console_script=box 且模块来自 box.*
    4) 最后兜底：返回 "box"
    """
    env = os.environ.get("BOX_DIST_NAME", "").strip()
    if env:
        return env

    pipx_bin = which("pipx")
    if pipx_bin:
        pkg = _pipx_find_package_for_app("box", pipx_bin=pipx_bin)
        if pkg:
            return pkg

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
# PATH 诊断
# ----------------------------

def _path_entries() -> list[str]:
    path = os.environ.get("PATH", "") or ""
    return [p for p in path.split(":") if p]


def _find_duplicates(items: list[str]) -> dict[str, int]:
    m: dict[str, int] = {}
    for x in items:
        m[x] = m.get(x, 0) + 1
    return {k: v for k, v in m.items() if v > 1}


def _index_of(entries: list[str], target: str) -> Optional[int]:
    try:
        return entries.index(target)
    except ValueError:
        return None


def _print_which_a(cmd: str) -> None:
    p = _run_capture(["which", "-a", cmd])
    if p.returncode != 0:
        print(f"which -a {cmd}: FAILED")
        return
    out = (p.stdout or "").strip()
    if not out:
        print(f"which -a {cmd}: (no result)")
        return
    print(f"which -a {cmd}:")
    for line in out.splitlines():
        print(f"  {line.strip()}")


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

    pipx_bin = which("pipx")
    print(f"pipx: {pipx_bin or 'NOT FOUND'}")

    box_bin = which("box")
    print(f"box (which): {box_bin or 'NOT FOUND'}")

    # 输出 which -a，帮助定位 pyenv shim / pipx 真实入口冲突
    _print_which_a("box")

    # PATH 问题诊断
    entries = _path_entries()
    dup = _find_duplicates(entries)

    local_bin = str(Path.home() / ".local" / "bin")
    pyenv_shims = str(Path.home() / ".pyenv" / "shims")

    # 1) PATH 缺失提示（pipx 常用目录）
    missing = [local_bin] if Path(local_bin).exists() and local_bin not in entries else []
    if missing:
        print("warn: PATH 可能缺少以下目录（可能导致 pipx 安装的命令找不到）：")
        for m in missing:
            print(f"  - {m}")

    # 2) PATH 重复提示（which -a 会重复、也容易误导）
    if dup:
        print("warn: PATH 存在重复目录（会导致 which -a 输出重复，建议去重）：")
        for k, v in sorted(dup.items(), key=lambda kv: (-kv[1], kv[0])):
            if k.endswith("/.local/bin") or k.endswith("/.pyenv/shims") or v >= 2:
                print(f"  - {k}  (x{v})")

    # 3) 顺序提示：pyenv shims 通常会抢占 pipx 的 ~/.local/bin
    i_local = _index_of(entries, local_bin)
    i_shims = _index_of(entries, pyenv_shims)
    if i_local is not None and i_shims is not None and i_shims < i_local:
        print("warn: PATH 顺序可能导致 pyenv shims 抢占 pipx 命令：")
        print(f"  - {pyenv_shims} (index={i_shims}) 在 {local_bin} (index={i_local}) 之前")
        print("hint: 建议把 ~/.local/bin 放在 ~/.pyenv/shims 之前（并执行 `hash -r` 刷新缓存）。")

    # pipx 维度信息：提供 box 的包是谁
    if pipx_bin:
        pkg = _pipx_find_package_for_app("box", pipx_bin=pipx_bin)
        print(f"pipx_box_package: {pkg or 'UNKNOWN'}")

    cfg = Path.home() / ".config" / "box"
    print(f"config_dir: {cfg} ({'exists' if cfg.exists() else 'missing'})")

    # 额外：确认 dist 是否存在（注意：这是“当前 python 环境”的 metadata，不一定等于 pipx venv）
    try:
        md.distribution(dist_name)
        print("dist(metadata): OK")
    except md.PackageNotFoundError:
        print("dist(metadata): NOT FOUND (可能是发行包名不一致，或当前 python 环境不是 pipx venv)")
        print("hint: 检查 pyproject.toml 的 [project].name，并可用环境变量 BOX_DIST_NAME 强制指定。")
        if pipx_bin:
            print("hint: 也可用 `pipx list --json` 确认 pipx 管理的包名，再设置 BOX_DIST_NAME。")

    print("doctor: OK")
    return 0


def cmd_update(_parser: argparse.ArgumentParser, _args: argparse.Namespace) -> int:
    pipx_bin = which("pipx")

    # 优先用 pipx list 找到真正的包名（避免升级错包）
    dist_name = None
    if pipx_bin:
        dist_name = _pipx_find_package_for_app("box", pipx_bin=pipx_bin)
    dist_name = dist_name or get_dist_name()

    print("== box update ==")
    print(f"dist_name: {dist_name}")

    if pipx_bin:
        rc = run([pipx_bin, "upgrade", dist_name])
        if rc == 0:
            print("update: OK (pipx upgrade)")
            return 0

        print("update: pipx upgrade failed, trying reinstall...")
        rc = run([pipx_bin, "reinstall", dist_name])
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
    pipx_bin = which("pipx")

    # 优先用 pipx list 找到真正的包名（避免卸载错包）
    dist_name = None
    if pipx_bin:
        dist_name = _pipx_find_package_for_app("box", pipx_bin=pipx_bin)
    dist_name = dist_name or get_dist_name()

    print("== box uninstall ==")
    print(f"dist_name: {dist_name}")

    if pipx_bin:
        rc = run([pipx_bin, "uninstall", dist_name])
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

    # tools 列表展示层面，也优先以 pipx 为准（更贴近用户实际安装方式）
    pipx_bin = which("pipx")
    dist_name = None
    if pipx_bin:
        dist_name = _pipx_find_package_for_app("box", pipx_bin=pipx_bin)
    dist_name = dist_name or get_dist_name()

    print("== box tools ==")
    print(f"dist_name: {dist_name}")

    try:
        dist = md.distribution(dist_name)
    except md.PackageNotFoundError:
        print(f"❌ 找不到已安装包元数据：{dist_name}")
        print("hint: 检查 pyproject.toml 的 [project].name，并可用环境变量 BOX_DIST_NAME 强制指定。")
        if pipx_bin:
            print("hint: 也可用 `pipx list --json` 确认 pipx 管理的包名，再设置 BOX_DIST_NAME。")
        return 2

    eps = list(dist.entry_points)
    scripts = [ep for ep in eps if ep.group == "console_scripts"]

    if not scripts:
        print("未发现该工具集发布的命令入口点。")
        return 0

    # 排序：box 放最前，其它按名字
    scripts.sort(key=lambda ep: (0 if ep.name == "box" else 1, ep.name))

    # 可选：检测 BOX_TOOL.id 重复（仅在 --full 时更有价值）
    seen_ids: dict[str, list[str]] = {}

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

            tid = str(tool_info.get("id", "")).strip()
            if tid:
                seen_ids.setdefault(tid, []).append(name)

            print(_format_tool_card(tool_info, full))
        else:
            print(f"- {name}")
            print(f"  entry: {value}")
            if full and import_err:
                print(f"  import_error: {import_err}")
            if name == "box":
                print("  about: toolset manager (use `box help`)")

    if full:
        dups = {k: v for k, v in seen_ids.items() if len(v) > 1}
        if dups:
            print("\nwarn: 检测到重复 BOX_TOOL.id（建议保持唯一）：")
            for tid, names in sorted(dups.items(), key=lambda kv: kv[0]):
                print(f"  - {tid}: {', '.join(names)}")

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
