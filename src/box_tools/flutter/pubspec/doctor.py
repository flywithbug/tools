from __future__ import annotations

import os
import re
from typing import List, Tuple

from .tool import Context, read_text, run_cmd


_VERSION_RE = re.compile(
    r"^\s*version:\s*([0-9]+)\.([0-9]+)\.([0-9]+)(?:\+([0-9A-Za-z.\-_]+))?\s*$"
)


def _check_cloudsmith_api_key(warnings: List[str], errors: List[str]) -> None:
    """
    如果没有 cloudsmithApiKey，私有 hosted/url 仓库通常无法访问。
    这里按你的要求：缺失视为“错误”，会阻断操作。
    """
    key = (os.environ.get("cloudsmithApiKey") or "").strip()
    if not key:
        errors.append(
            "缺少环境变量：cloudsmithApiKey\n"
            "私有组件库需要该 Key 才能访问/升级依赖。\n"
            "解决办法（示例）：\n"
            "  export cloudsmithApiKey=5de**"
        )


def _check_pubspec_exists(ctx: Context, errors: List[str]) -> None:
    if not ctx.pubspec_path.exists():
        errors.append(f"pubspec.yaml 不存在：{ctx.pubspec_path}")


def _check_pubspec_basic(ctx: Context, warnings: List[str], errors: List[str]) -> None:
    if not ctx.pubspec_path.exists():
        return

    raw = read_text(ctx.pubspec_path)
    lines = raw.splitlines()

    has_name = any(re.match(r"^\s*name:\s*\S+", ln) for ln in lines)
    if not has_name:
        warnings.append("缺少 name:（如果是应用项目可忽略；如果是 package 发布会有影响）")

    ver_lines = [ln for ln in lines if re.match(r"^\s*version:\s*", ln)]
    if not ver_lines:
        errors.append("缺少 version: 行")
    else:
        if not _VERSION_RE.match(ver_lines[0].strip()):
            errors.append(f"version 格式不合法：{ver_lines[0].strip()}（期望 x.y.z 或 x.y.z+build）")




def _parse_pubspec_publish_to(raw: str) -> str | None:
    """
    返回 publish_to 的值（去除引号），若未配置则返回 None。
    注意：未配置 publish_to 在 Dart/Flutter 中默认是“可发布到 pub.dev”（等价于非 none）。
    """
    for ln in raw.splitlines():
        s = ln.strip()
        if not s or s.startswith("#"):
            continue
        m = re.match(r"^publish_to:\s*(.+?)\s*$", s)
        if m:
            v = m.group(1).strip()
            # 去掉简单引号/双引号
            if (v.startswith("'") and v.endswith("'")) or (v.startswith('"') and v.endswith('"')):
                v = v[1:-1].strip()
            return v
    return None


def _check_changelog_if_publishable(ctx: Context, warnings: List[str], errors: List[str]) -> None:
    """
    规则：
    - 如果 publish_to 明确是 none -> 不检查 CHANGELOG.md
    - 否则（包括未配置 / 配置为 url / 配置为 hosted）-> 必须存在 CHANGELOG.md
    """
    if not ctx.pubspec_path.exists():
        return
    raw = read_text(ctx.pubspec_path)
    publish_to = _parse_pubspec_publish_to(raw)
    if publish_to is not None and publish_to.strip().lower() == "none":
        return

    changelog = ctx.pubspec_path.parent / "CHANGELOG.md"
    if not changelog.exists():
        errors.append(
            "缺少 CHANGELOG.md\n"
            "publish_to 不是 none（或未配置，默认可发布），发布包要求提供变更记录。\n"
            f"建议：在 {changelog} 新增并维护版本变更说明。"
        )


_LOCAL_DEP_INLINE_RE = re.compile(
    r"^\s{2,}([A-Za-z0-9_]+)\s*:\s*\{[^#]*\bpath\s*:\s*([^,}]+)",
    re.IGNORECASE,
)


def _check_local_dependencies(ctx: Context, warnings: List[str]) -> None:
    """
    检查 pubspec.yaml 内是否存在本地 path 依赖（含 dependencies/dev_dependencies/dependency_overrides）。
    本地依赖通常会导致：
    - CI / 发布环境无法解析
    - 依赖锁定与可复现性变差
    这里按你的要求：发现则提示（warning），不阻断。
    """
    if not ctx.pubspec_path.exists():
        return

    raw = read_text(ctx.pubspec_path)
    lines = raw.splitlines()

    locals_found: List[tuple[str, str]] = []
    current_pkg: str | None = None
    current_pkg_indent: int | None = None

    for ln in lines:
        # 忽略注释行
        stripped = ln.strip()
        if not stripped or stripped.startswith("#"):
            continue

        # inline: foo: {path: ../foo}
        m_inline = _LOCAL_DEP_INLINE_RE.match(ln)
        if m_inline:
            pkg = m_inline.group(1).strip()
            pth = m_inline.group(2).strip().strip("'\"")
            locals_found.append((pkg, pth))
            current_pkg = None
            current_pkg_indent = None
            continue

        # 形如：  foo:
        m_pkg = re.match(r"^(\s{2,})([A-Za-z0-9_]+)\s*:\s*$", ln)
        if m_pkg:
            current_pkg = m_pkg.group(2).strip()
            current_pkg_indent = len(m_pkg.group(1))
            continue

        # 形如：    path: ../foo
        m_path = re.match(r"^(\s+)path\s*:\s*(\S+)\s*$", ln, re.IGNORECASE)
        if m_path and current_pkg is not None and current_pkg_indent is not None:
            indent = len(m_path.group(1))
            if indent > current_pkg_indent:
                pth = m_path.group(2).strip().strip("'\"")
                locals_found.append((current_pkg, pth))
            current_pkg = None
            current_pkg_indent = None
            continue

        # 遇到新的顶层 key，重置
        if re.match(r"^[A-Za-z0-9_]+\s*:\s*", stripped):
            current_pkg = None
            current_pkg_indent = None

    if locals_found:
        # 去重并保持顺序
        seen = set()
        uniq = []
        for pkg, pth in locals_found:
            key = (pkg, pth)
            if key in seen:
                continue
            seen.add(key)
            uniq.append((pkg, pth))

        preview = "\n".join([f"  - {pkg}: path: {pth}" for pkg, pth in uniq])
        warnings.append(
            "检测到本地 path 依赖（发布/CI 可能无法解析）：\n"
            f"{preview}\n"
            "建议：将其替换为 hosted/git 依赖，或在发布前移除/改为可解析的来源。"
        )


def _check_flutter(warnings: List[str], errors: List[str]) -> None:
    """
    flutter 不可用在多数子命令下属于硬错误（upgrade/publish/version 常常依赖 flutter）
    但 doctor 本身仍然能给出清晰提示。
    """
    r = run_cmd(["flutter", "--version"], cwd=os.getcwd(), capture=True)
    if r.code != 0:
        errors.append("flutter 不可用：无法执行 flutter --version（请确认 Flutter 已安装并在 PATH 中）")
        return

    first = ((r.out or "").strip().splitlines()[:1] or ["flutter OK"])[0]
    warnings.append(f"flutter: {first}")


def collect(ctx: Context) -> Tuple[bool, List[str], List[str]]:
    """
    收集模式：不输出日志，只返回 (ok, warnings, errors)
    """
    warnings: List[str] = []
    errors: List[str] = []

    _check_cloudsmith_api_key(warnings, errors)
    _check_pubspec_exists(ctx, errors)
    _check_pubspec_basic(ctx, warnings, errors)
    _check_changelog_if_publishable(ctx, warnings, errors)
    _check_local_dependencies(ctx, warnings)
    _check_flutter(warnings, errors)

    ok = not errors
    return ok, warnings, errors


def run(ctx: Context) -> int:
    """
    标准 doctor：会打印日志（用于用户手动运行 doctor 命令）
    """
    ctx.echo("=== pubspec doctor ===")
    ok, warnings, errors = collect(ctx)

    for w in warnings:
        ctx.echo(f"⚠️ {w}")
    for e in errors:
        # errors 里可能包含多行提示，这里保持原样缩进打印
        for ln in str(e).splitlines():
            ctx.echo(f"❌ {ln}" if ln else "❌")

    if not ok:
        ctx.echo("❌ doctor 未通过")
        return 1

    ctx.echo("✅ doctor 通过")
    return 0


def run_menu(ctx: Context) -> int:
    menu = [
        ("run", "执行环境/规范检查"),
    ]
    while True:
        ctx.echo("\n=== box_pubspec doctor ===")
        for i, (cmd, label) in enumerate(menu, start=1):
            ctx.echo(f"{i}. {cmd:<10} {label}")
        ctx.echo("0. back       返回")

        choice = input("> ").strip()
        if choice == "0":
            return 0
        if choice == "1":
            return run(ctx)
        ctx.echo("无效选择")
