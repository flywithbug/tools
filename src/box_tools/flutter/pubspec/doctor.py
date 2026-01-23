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
