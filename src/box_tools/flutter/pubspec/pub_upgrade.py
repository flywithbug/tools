from __future__ import annotations

import re
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from itertools import cycle
from typing import Optional

from .tool import Context, read_text, write_text_atomic, run_cmd, flutter_pub_outdated_json


# =======================
# Config switches (top-level)
# =======================
# 本阶段只读取 pubspec.yaml 的 dependencies 私有依赖（不含 dev_dependencies / overrides）
PRIVATE_HOST_KEYWORDS: tuple[str, ...] = tuple()  # 为空表示不做域名过滤（只要有 hosted url 就算私有）
SKIP_PACKAGES: set[str] = {"ap_recaptcha"}


# =======================
# Small UI helpers
# =======================
@contextmanager
def step_scope(ctx: Context, idx: int, title: str, msg: str):
    t0 = time.perf_counter()
    ctx.echo(f"\n========== Step {idx}: {title} ==========")
    if msg:
        ctx.echo(msg)
    try:
        yield
        dt = time.perf_counter() - t0
        ctx.echo(f"✅ Step {idx} 完成（{dt:.2f}s）")
    except Exception as e:
        dt = time.perf_counter() - t0
        ctx.echo(f"❌ Step {idx} 失败（{dt:.2f}s）：{e}")
        raise


def _ask_continue(ctx: Context, prompt: str) -> bool:
    """
    返回 True 表示“中断”，False 表示“继续”
    """
    if ctx.yes:
        return False
    return ctx.confirm(prompt)


# =======================
# Git helpers
# =======================
def _git_check_repo(ctx: Context) -> None:
    r = run_cmd(["git", "rev-parse", "--is-inside-work-tree"], cwd=ctx.project_root, capture=True)
    if r.code != 0 or (r.out or "").strip() != "true":
        raise RuntimeError("当前目录不是 git 仓库，请在项目根目录执行。")


def _git_is_dirty(ctx: Context) -> bool:
    r = run_cmd(["git", "status", "--porcelain"], cwd=ctx.project_root, capture=True)
    if r.code != 0:
        raise RuntimeError(f"git status 执行失败：{(r.err or r.out).strip()}")
    return bool((r.out or "").strip())


def _git_pull_ff_only(ctx: Context) -> None:
    r = run_cmd(["git", "pull", "--ff-only"], cwd=ctx.project_root, capture=True)
    if r.code != 0:
        raise RuntimeError(f"git pull --ff-only 失败：{(r.err or r.out).strip()}")


# =======================
# Version compare (resolved versions only)
# =======================
def _strip_meta(v: str) -> str:
    v = (v or "").strip()
    v = v.split("+", 1)[0]
    v = v.split("-", 1)[0]
    return v.strip()


def _parse_nums(v: str) -> list[int]:
    base = _strip_meta(v)
    parts = base.split(".") if base else []
    nums: list[int] = []
    for p in parts:
        try:
            nums.append(int(p))
        except Exception:
            nums.append(0)
    return nums


def compare_versions(a: str, b: str) -> int:
    """
    返回:
      -1: a < b
       0: a == b
      +1: a > b
    仅比较数字段（忽略 -pre / +build）
    """
    na = _parse_nums(a)
    nb = _parse_nums(b)
    n = max(len(na), len(nb))
    na += [0] * (n - len(na))
    nb += [0] * (n - len(nb))
    for x, y in zip(na, nb):
        if x < y:
            return -1
        if x > y:
            return 1
    return 0


# =======================
# Flutter / Pub helpers
# =======================
def flutter_pub_get(ctx: Context, *, with_loading: bool = True) -> None:
    cmd = ["flutter", "pub", "get"]
    if not with_loading:
        r = run_cmd(cmd, cwd=ctx.project_root, capture=True)
        if r.code != 0:
            raise RuntimeError((r.err or r.out or "").strip())
        return

    stop = threading.Event()
    spinner = cycle(["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"])

    def _spin():
        while not stop.is_set():
            ctx.echo(next(spinner), end="\r")
            time.sleep(0.08)

    t = threading.Thread(target=_spin, daemon=True)
    t.start()
    try:
        r = run_cmd(cmd, cwd=ctx.project_root, capture=True)
        if r.code != 0:
            raise RuntimeError((r.err or r.out or "").strip())
    finally:
        stop.set()
        t.join(timeout=0.3)
        ctx.echo(" " * 30, end="\r")


# =======================
# pubspec.yaml parsing (dependencies only, private hosted)
# =======================
_PKG_HEADER_RE = re.compile(r"^  ([A-Za-z0-9_]+):\s*(.*)$")
_SECTION_RE = re.compile(r"^([A-Za-z0-9_]+):\s*(#.*)?$")  # 顶层 key:（0 缩进）
_HOSTED_INLINE_RE = re.compile(r"^\s{4}hosted:\s*([^\s#]+)\s*(#.*)?$")
_KV_RE = re.compile(r"^\s{4}([A-Za-z0-9_]+):\s*(.*?)\s*(#.*)?$")  # 4 缩进 key: value
_URL_KV_RE = re.compile(r"^\s{6}url:\s*(.*?)\s*(#.*)?$")          # 6 缩进 url: value


def _is_top_level_section(line: str) -> Optional[str]:
    """返回顶层 section 名称（如 dependencies / dev_dependencies），否则 None"""
    if line.startswith(" "):
        return None
    m = _SECTION_RE.match(line.rstrip("\n"))
    if not m:
        return None
    return m.group(1)


def _strip_quotes(s: str) -> str:
    s = (s or "").strip()
    if len(s) >= 2 and ((s[0] == s[-1] == '"') or (s[0] == s[-1] == "'")):
        return s[1:-1].strip()
    return s


@dataclass(frozen=True)
class PubspecPrivateDep:
    name: str
    constraint: str  # pubspec 里的版本约束（如 ^0.0.11），可能为空
    hosted_url: str


def read_pubspec_private_dependencies(ctx: Context, private_host_keywords: tuple[str, ...]) -> dict[str, PubspecPrivateDep]:
    """
    读取 pubspec.yaml 的 dependencies 区块，找出 private hosted 依赖：
      - 只要 hosted url 存在就算 hosted
      - 若配置了关键词，则 hosted url 需命中关键词之一
    返回：name -> PubspecPrivateDep
    """
    text = read_text(ctx.pubspec_path)
    lines = text.splitlines(keepends=False)

    in_deps = False
    out: dict[str, PubspecPrivateDep] = {}

    i = 0
    while i < len(lines):
        line = lines[i]

        sec = _is_top_level_section(line)
        if sec is not None:
            in_deps = (sec == "dependencies")
            i += 1
            continue

        if not in_deps:
            i += 1
            continue

        m = _PKG_HEADER_RE.match(line)
        if not m:
            i += 1
            continue

        name = m.group(1)
        if name in SKIP_PACKAGES:
            i += 1
            continue

        remainder = (m.group(2) or "").strip()
        # inline 形式：foo: ^1.2.3（通常不是 hosted，直接跳过）
        if remainder and not remainder.startswith("#"):
            i += 1
            continue

        hosted_url: str | None = None
        constraint: str = ""

        j = i + 1
        while j < len(lines):
            ln = lines[j]

            sec2 = _is_top_level_section(ln)
            if sec2 is not None:
                break
            if _PKG_HEADER_RE.match(ln):
                break

            mh = _HOSTED_INLINE_RE.match(ln)
            if mh and not hosted_url:
                hosted_url = _strip_quotes(mh.group(1))
                j += 1
                continue

            mkv = _KV_RE.match(ln)
            if mkv:
                key = mkv.group(1)
                val = _strip_quotes(mkv.group(2) or "")
                if key == "version" and val:
                    constraint = val
                if key == "hosted":
                    if val and not hosted_url:
                        hosted_url = val
                j += 1
                continue

            mu = _URL_KV_RE.match(ln)
            if mu and not hosted_url:
                hosted_url = _strip_quotes(mu.group(1) or "")
                j += 1
                continue

            j += 1

        if hosted_url:
            if private_host_keywords:
                u = hosted_url.lower()
                if not any(k.lower() in u for k in private_host_keywords):
                    i = j
                    continue
            out[name] = PubspecPrivateDep(name=name, constraint=constraint, hosted_url=hosted_url)

        i = j

    return out


# =======================
# outdated json parsing
# =======================
def _extract_current_version(pkg: dict) -> Optional[str]:
    cur = pkg.get("current")
    if isinstance(cur, dict):
        v = cur.get("version")
        if isinstance(v, str):
            return v.strip()
    return None


def _extract_latest_version(pkg: dict) -> Optional[str]:
    latest = pkg.get("latest")
    if isinstance(latest, dict):
        v = latest.get("version")
        if isinstance(v, str) and v.strip():
            return v.strip()
    res = pkg.get("resolvable")
    if isinstance(res, dict):
        v = res.get("version")
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None


@dataclass(frozen=True)
class UpgradeItem:
    name: str
    pubspec_constraint: str
    resolved_current: str
    target_latest: str


def build_private_upgrade_plan_from_pubspec(
    ctx: Context,
    private_deps: dict[str, PubspecPrivateDep],
) -> list[UpgradeItem]:
    """
    使用 pubspec.yaml 中解析出来的“私有依赖 name 集合”来做判定，
    然后去 flutter pub outdated --json 里找对应包，比较 resolved current vs latest，
    如需要升级则列出。
    """
    data = flutter_pub_outdated_json(ctx)
    pkgs = data.get("packages") or []

    index: dict[str, dict] = {}
    for p in pkgs:
        n = p.get("package")
        if isinstance(n, str) and n:
            index[n] = p

    plan: list[UpgradeItem] = []
    for name, dep in private_deps.items():
        pkg = index.get(name)
        if not pkg:
            continue

        cur = _extract_current_version(pkg) or ""
        latest = _extract_latest_version(pkg) or ""
        if not latest:
            continue

        if cur and compare_versions(cur, latest) >= 0:
            continue

        plan.append(
            UpgradeItem(
                name=name,
                pubspec_constraint=dep.constraint or "(no version field)",
                resolved_current=cur or "(unknown)",
                target_latest=latest,
            )
        )

    plan.sort(key=lambda x: x.name.lower())
    return plan


# =======================
# pubspec.yaml patch (preserve comments/styles)
# =======================
_VERSION_LINE_RE = re.compile(r"^(\s{4}version:\s*)([^#\n]+?)(\s*)(#.*)?$", re.UNICODE)


def _format_constraint_keep_style(old_constraint: str, latest: str) -> str | None:
    """
    把旧约束替换成最新版本，但尽量保留旧的风格（如 ^ 前缀 / 引号）。
    只支持：
      - ^1.2.3
      - ~1.2.3
      - 1.2.3
      - "^1.2.3" / '1.2.3'
    对复杂范围（>=, <, 空格, || 等）返回 None（表示跳过）。
    """
    s = (old_constraint or "").strip()
    if not s:
        return None

    quote = ""
    if len(s) >= 2 and ((s[0] == s[-1] == '"') or (s[0] == s[-1] == "'")):
        quote = s[0]
        inner = s[1:-1].strip()
    else:
        inner = s

    # 复杂约束：含空格、比较符号、逗号等，直接跳过避免改坏
    if any(ch in inner for ch in [" ", ">", "<", "=", ",", "||"]):
        return None

    m = re.match(r"^(\^|~)?\s*(\d+(?:\.\d+){1,3})\s*$", inner)
    if not m:
        return None

    prefix = m.group(1) or ""
    new_inner = f"{prefix}{latest}"
    return f"{quote}{new_inner}{quote}" if quote else new_inner


def apply_upgrades_to_pubspec(ctx: Context, plan: list[UpgradeItem]) -> list[str]:
    """
    把 plan 中的包，在 pubspec.yaml 的 dependencies 区块里更新 version 行。
    目标：保留注释/缩进/其余字段不动。
    返回：变更摘要行（用于日志）。
    """
    if not plan:
        return []

    wanted: dict[str, UpgradeItem] = {u.name: u for u in plan}

    text = read_text(ctx.pubspec_path)
    lines = text.splitlines(keepends=True)

    in_deps = False
    i = 0
    changed: list[str] = []

    while i < len(lines):
        line = lines[i]
        sec = _is_top_level_section(line.rstrip("\n"))
        if sec is not None:
            in_deps = (sec == "dependencies")
            i += 1
            continue

        if not in_deps:
            i += 1
            continue

        m = _PKG_HEADER_RE.match(line.rstrip("\n"))
        if not m:
            i += 1
            continue

        name = m.group(1)
        if name not in wanted:
            i += 1
            continue

        # 进入该包的 block，寻找 version: 行
        u = wanted[name]
        j = i + 1
        updated = False
        while j < len(lines):
            ln = lines[j]
            # 离开 dependencies 或进入下一个包
            sec2 = _is_top_level_section(ln.rstrip("\n"))
            if sec2 is not None:
                break
            if _PKG_HEADER_RE.match(ln.rstrip("\n")):
                break

            mver = _VERSION_LINE_RE.match(ln.rstrip("\n"))
            if mver and not updated:
                prefix, old_val, spaces, comment = mver.group(1), mver.group(2), mver.group(3), mver.group(4)
                new_val = _format_constraint_keep_style(old_val, u.target_latest)
                if new_val is None:
                    # 复杂约束不改，给出提示
                    ctx.echo(f"⚠️ 跳过 {name}：不支持的 version 约束写法：{old_val.strip()}")
                    updated = True  # 视为处理过，避免重复警告
                    break

                new_line = prefix + new_val + (spaces or "") + (comment or "") + ("\n" if ln.endswith("\n") else "")
                if new_line != ln:
                    lines[j] = new_line
                    changed.append(f"{name}: {old_val.strip()} -> {new_val.strip()}")
                updated = True
                break

            j += 1

        if not updated:
            ctx.echo(f"⚠️ 未找到 {name} 的 version: 行，未做修改（请检查 pubspec 写法）。")

        i = j

    if changed:
        write_text_atomic(ctx.pubspec_path, "".join(lines))

    return changed


# =======================
# Entry (Stage 2: apply changes to pubspec)
# =======================
def run(ctx: Context) -> int:
    """
    阶段 2：在阶段 1 的基础上，新增“写回 pubspec.yaml 中待升级依赖的版本号”。

    本阶段包含：
      0) 环境检查（git 仓库）
      1) 检查是否有未提交变更
      2) 同步远端（git pull --ff-only）
      3) 执行 flutter pub get（预检查）
      4) 从 pubspec.yaml 的 dependencies 读取私有依赖（name + constraint + hosted url）
      5) 跟 flutter pub outdated --json 对比，列出需要升级的私有依赖
      6) 修改 pubspec.yaml 中这些依赖的 version: 行（保留注释和样式）

    注意：本阶段仍不执行 pub get/analyze/commit（你下一步再加）。
    """
    total_t0 = time.perf_counter()

    try:
        with step_scope(ctx, 0, "环境检查（git 仓库）", "检查 git 仓库..."):
            _git_check_repo(ctx)

        with step_scope(ctx, 1, "检查是否有未提交变更", "检查工作区状态..."):
            if _git_is_dirty(ctx):
                ctx.echo("⚠️ 检测到未提交变更（working tree dirty）。")
                if _ask_continue(ctx, "检测到未提交变更，是否中断本次执行？"):
                    ctx.echo("已中断。")
                    return 0
                ctx.echo("继续执行（注意：后续步骤可能依赖干净工作区的可重复性）。")
            else:
                ctx.echo("✅ 工作区干净")

        with step_scope(ctx, 2, "同步远端（git pull --ff-only）", "拉取远程更新..."):
            _git_pull_ff_only(ctx)

        with step_scope(ctx, 3, "执行 flutter pub get（预检查）", "正在执行 pub get..."):
            flutter_pub_get(ctx, with_loading=False)
            ctx.echo("✅ pub get 通过")

        with step_scope(ctx, 4, "读取 pubspec.yaml 的私有依赖（dependencies）", "解析 dependencies hosted/url ..."):
            private_deps = read_pubspec_private_dependencies(ctx, PRIVATE_HOST_KEYWORDS)
            if not private_deps:
                ctx.echo("ℹ️ 在 pubspec.yaml 的 dependencies 未发现 private hosted 依赖。") 

        with step_scope(ctx, 5, "对比 flutter pub outdated --json", "生成待升级清单..."):
            plan = build_private_upgrade_plan_from_pubspec(ctx, private_deps)

        if not plan:
            ctx.echo("ℹ️ 未发现需要升级的私有依赖。")
            return 0

        ctx.echo("\n待升级私有依赖清单（pubspec constraint / resolved -> latest）：")
        for u in plan:
            ctx.echo(f"  - {u.name}: {u.pubspec_constraint} / {u.resolved_current} -> {u.target_latest}")

        with step_scope(ctx, 6, "修改 pubspec.yaml（仅更新 version 行）", "写回 dependencies 中待升级依赖版本..."):
            changed = apply_upgrades_to_pubspec(ctx, plan)

        if not changed:
            ctx.echo("ℹ️ 未产生任何文件修改（可能是约束格式不支持或未找到 version 行）。")
            return 0

        ctx.echo("\n已写回 pubspec.yaml，修改摘要：")
        for s in changed:
            ctx.echo(f"  - {s}")

        dt = time.perf_counter() - total_t0
        ctx.echo(f"\n✅ 完成（已修改 pubspec.yaml，尚未执行后续 pub get/analyze/commit）。总耗时 {dt:.2f}s")
        return 0

    except KeyboardInterrupt:
        ctx.echo("\n⛔ 用户中断。")
        return 130
    except Exception as e:
        ctx.echo(f"\n❌ 执行失败：{e}")
        return 1
