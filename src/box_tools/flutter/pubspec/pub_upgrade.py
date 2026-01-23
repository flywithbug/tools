from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Optional

from .tool import Context, read_text, write_text_atomic, flutter_pub_outdated_json
from .pub_version import parse_version


# ----------------------------
# Plan（先做框架：能 scan、能列清单）
# apply 后续逐步实现：必须“局部替换，保持结构/注释”
# ----------------------------
@dataclass(frozen=True)
class PlanItem:
    package: str
    kind: str
    current: Optional[str]
    latest: Optional[str]
    action: str          # "AUTO" | "PROMPT" | "SKIP"
    reason: str
    target: Optional[str]


def _load_outdated(ctx: Context) -> dict:
    if ctx.outdated_json_path:
        return json.loads(read_text(ctx.outdated_json_path))
    return flutter_pub_outdated_json(ctx)


def _get_ver(obj: dict | None) -> Optional[str]:
    if not obj:
        return None
    return obj.get("version")


def _app_xy(app_version: str) -> tuple[int, int]:
    # app_version: x.y.z[+b]
    core = app_version.split("+", 1)[0]
    x, y, _z = core.split(".")
    return int(x), int(y)


def _ver_xy(v: str) -> tuple[int, int]:
    core = v.split("+", 1)[0]
    x, y, _z = core.split(".")
    return int(x), int(y)


def build_plan(ctx: Context) -> tuple[str, list[PlanItem]]:
    raw = read_text(ctx.pubspec_path)
    app_version = parse_version(raw).format()
    ax, ay = _app_xy(app_version)

    data = _load_outdated(ctx)
    items: list[PlanItem] = []

    for p in data.get("packages", []):
        pkg = p.get("package")
        kind = p.get("kind") or "unknown"
        cur = _get_ver(p.get("current"))
        latest = _get_ver(p.get("latest"))

        if not latest:
            items.append(PlanItem(pkg, kind, cur, latest, "SKIP", "缺少 latest", None))
            continue

        lx, ly = _ver_xy(latest)

        # 先按你 PRD 的“以 app X.Y 为锚点”做框架决策
        if (lx < ax) or (lx == ax and ly <= ay):
            items.append(PlanItem(pkg, kind, cur, latest, "AUTO", f"latest({lx}.{ly}) <= app({ax}.{ay})", latest))
        else:
            items.append(PlanItem(pkg, kind, cur, latest, "PROMPT", f"latest({lx}.{ly}) > app({ax}.{ay})", latest))

    return app_version, items


def print_plan(ctx: Context, app_version: str, items: list[PlanItem]) -> None:
    total = len(items)
    auto = sum(1 for i in items if i.action == "AUTO")
    prompt = sum(1 for i in items if i.action == "PROMPT")
    skip = sum(1 for i in items if i.action == "SKIP")

    ctx.echo(f"应用版本：{app_version}")
    ctx.echo(f"计划统计：TOTAL={total} AUTO={auto} PROMPT={prompt} SKIP={skip}")
    ctx.echo("—— 详细（最多 80 条）——")
    for it in items[:80]:
        ctx.echo(
            f"- {it.package:<28} {it.kind:<10} "
            f"{(it.current or '-'):>12} -> {(it.latest or '-'):>12} "
            f"[{it.action}] {it.reason}"
        )
    if total > 80:
        ctx.echo(f"（省略 {total - 80} 条）")


# ----------------------------
# apply：严格文本级“局部替换”框架（先留钩子）
# 后续我们逐步补齐：
# 1) 普通依赖 `pkg: x.y.z` / `pkg: ^x.y.z` 替换
# 2) hosted 块 `pkg:\n  hosted:...\n  version: x.y.z` 替换其中 version 行
# 绝不重排/重格式化/触碰注释
# ----------------------------

_TOP_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*:\s*$")


def _find_section(lines: list[str], key: str) -> Optional[tuple[int, int]]:
    # 返回 [start, end) 行区间；end 是下一个顶层 key 或 EOF
    pat = re.compile(rf"^{re.escape(key)}:\s*$")
    start = None
    for i, ln in enumerate(lines):
        if start is None and pat.match(ln):
            start = i
            continue
        if start is not None:
            if _TOP_KEY_RE.match(ln) and not ln.startswith(" "):
                return start, i
    if start is not None:
        return start, len(lines)
    return None


def apply_plan(ctx: Context, app_version: str, items: list[PlanItem]) -> int:
    ctx.echo("⚠️ apply 目前是框架：还不会改 pubspec.yaml（只 scan）")
    ctx.echo("下一步我们会实现：只替换命中的依赖行或 hosted.version 行，不改其它任何文本/注释。")
    return 0


def run_menu(ctx: Context) -> int:
    menu = [
        ("scan", "分析 outdated 并生成升级计划（不落盘）"),
        ("apply", "按规则执行升级（会写 pubspec.yaml，仅局部替换）"),
    ]

    while True:
        ctx.echo("\n=== pubspec upgrade ===")
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
        app_version, plan_items = build_plan(ctx)
        print_plan(ctx, app_version, plan_items)

        if cmd == "scan":
            return 0

        if cmd == "apply":
            if ctx.dry_run:
                ctx.echo("（dry-run）不写入 pubspec.yaml")
                return 0
            if ctx.interactive and (not ctx.yes):
                if not ctx.confirm("确认按计划修改 pubspec.yaml？（只会做局部替换）"):
                    ctx.echo("已取消")
                    return 1
            return apply_plan(ctx, app_version, plan_items)

        ctx.echo("未知选择")
        return 1
