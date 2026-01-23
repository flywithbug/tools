from __future__ import annotations

import re
from typing import List

from .tool import Context, read_text, flutter_pub_publish


def check(ctx: Context) -> tuple[bool, List[str], List[str]]:
    warnings: List[str] = []
    errors: List[str] = []

    if not ctx.pubspec_path.exists():
        errors.append(f"pubspec.yaml 不存在：{ctx.pubspec_path}")
        return False, warnings, errors

    raw = read_text(ctx.pubspec_path)
    lines = raw.splitlines()

    if not any(re.match(r"^\s*name:\s*\S+", ln) for ln in lines):
        warnings.append("缺少 name:（如果是 package 发布会有影响）")

    if not any(re.match(r"^\s*version:\s*\S+", ln) for ln in lines):
        errors.append("缺少 version:")

    ok = len(errors) == 0
    return ok, warnings, errors


def run_menu(ctx: Context) -> int:
    menu = [
        ("check", "发布前检查（不发布）"),
        ("dry-run", "发布 dry-run（flutter pub publish --dry-run）"),
        ("publish", "执行发布（flutter pub publish）"),
    ]

    while True:
        ctx.echo("\n=== pubspec publish ===")
        for i, (cmd, label) in enumerate(menu, start=1):
            ctx.echo(f"{i}. {cmd:<10} {label}")
        ctx.echo("0. back       返回")

        choice = input("> ").strip()
        if choice == "0":
            return 0
        if not choice.isdigit() or not (1 <= int(choice) <= len(menu)):
            ctx.echo("无效选择")
            continue

        cmd = menu[int(choice) - 1][0]

        if cmd == "check":
            ok, warnings, errors = check(ctx)
            for w in warnings:
                ctx.echo(f"⚠️ {w}")
            for e in errors:
                ctx.echo(f"❌ {e}")
            ctx.echo("✅ check 通过" if ok else "❌ check 未通过")
            return 0 if ok else 1

        if cmd == "dry-run":
            r = flutter_pub_publish(ctx, dry_run=True)
            if r.out.strip():
                ctx.echo(r.out.strip())
            if r.code != 0 and r.err.strip():
                ctx.echo(r.err.strip())
            return r.code

        if cmd == "publish":
            if ctx.dry_run:
                ctx.echo("（dry-run）全局 dry-run 下不允许执行 publish；请用 publish->dry-run 子功能")
                return 1
            if ctx.interactive and (not ctx.yes):
                if not ctx.confirm("确认执行 flutter pub publish？"):
                    ctx.echo("已取消")
                    return 1
            r = flutter_pub_publish(ctx, dry_run=False)
            if r.out.strip():
                ctx.echo(r.out.strip())
            if r.code != 0 and r.err.strip():
                ctx.echo(r.err.strip())
            return r.code

        ctx.echo("未知选择")
        return 1
