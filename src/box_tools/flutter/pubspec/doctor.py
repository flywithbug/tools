from __future__ import annotations

import re
from typing import List

from .tool import Context, read_text, run_cmd


_VERSION_RE = re.compile(
    r"^\s*version:\s*([0-9]+)\.([0-9]+)\.([0-9]+)(?:\+([0-9A-Za-z.\-_]+))?\s*$"
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
        # 只校验第一条 version 行（pubspec 应该只有一条）
        if not _VERSION_RE.match(ver_lines[0].strip()):
            errors.append(f"version 格式不合法：{ver_lines[0].strip()}（期望 x.y.z 或 x.y.z+build）")


def _check_flutter(ctx: Context, warnings: List[str]) -> None:
    r = run_cmd(["flutter", "--version"], cwd=ctx.project_root, capture=True)
    if r.code != 0:
        warnings.append("flutter 不可用：无法执行 flutter --version")
    else:
        # 简短输出第一行
        first = (r.out.strip().splitlines()[:1] or ["flutter OK"])[0]
        warnings.append(f"flutter: {first}")


def run(ctx: Context) -> int:
    ctx.echo("=== pubspec doctor ===")
    warnings: List[str] = []
    errors: List[str] = []

    _check_pubspec_exists(ctx, errors)
    _check_pubspec_basic(ctx, warnings, errors)
    _check_flutter(ctx, warnings)

    for w in warnings:
        ctx.echo(f"⚠️ {w}")
    for e in errors:
        ctx.echo(f"❌ {e}")

    if errors:
        ctx.echo("❌ doctor 未通过")
        return 1
    ctx.echo("✅ doctor 通过")
    return 0


def run_menu(ctx: Context) -> int:
    menu = [
        ("run", "执行环境/规范检查"),
    ]
    while True:
        ctx.echo("\n=== pubspec doctor ===")
        for i, (cmd, label) in enumerate(menu, start=1):
            ctx.echo(f"{i}. {cmd:<10} {label}")
        ctx.echo("0. back       返回")

        choice = input("> ").strip()
        if choice == "0":
            return 0
        if choice == "1":
            return run(ctx)
        ctx.echo("无效选择")
