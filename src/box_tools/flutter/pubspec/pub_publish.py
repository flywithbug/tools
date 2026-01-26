from __future__ import annotations

import re
from itertools import cycle
import threading
import time
import shutil
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from .tool import Context, read_text, write_text_atomic, run_cmd


# =======================
# Config (top-level)
# =======================
REQUIRE_CHANGELOG = True
CHANGELOG_NAME = "CHANGELOG.md"

DEFAULT_NOTE = "publish"

MAX_SHOW_ERRORS = 10
# 你要求 warning 打印前四五条：这里取 5
MAX_SHOW_WARNINGS = 5
# 你要求 info 打印前两三条：这里取 3
MAX_SHOW_INFO = 3

# analyze 失败但解析不出 issue 时，兜底最多打印多少行原始输出（避免刷屏）
MAX_SHOW_RAW_ANALYZE_LINES = 120


# =======================
# Helpers
# =======================
def _step(ctx: Context, n: int, title: str) -> None:
    ctx.echo(f"\n[{n}] {title}")


def _now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _clear_line() -> None:
    print("\r\033[2K", end="", flush=True)


def _loading_animation(stop_event: threading.Event, label: str, t0: float) -> None:
    """loading 动画 + 实时耗时（秒）"""
    spinner = cycle(["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"])
    while not stop_event.is_set():
        elapsed = time.perf_counter() - t0
        print(f"{next(spinner)} {label}  (elapsed: {elapsed:6.1f}s) ", end="", flush=True)
        time.sleep(0.1)
        _clear_line()


def _run_or_die(ctx: Context, cmd: list[str], *, title: str, cwd: Optional[str] = None, loading: bool = False) -> None:
    """执行命令；可选 loading 动画，避免长时间无输出像卡死。"""
    stop_event: Optional[threading.Event] = None
    t: Optional[threading.Thread] = None
    if loading:
        stop_event = threading.Event()
        _load_t0 = time.perf_counter()
        t = threading.Thread(target=_loading_animation, args=(stop_event, title, _load_t0))
        t.start()
    try:
        r = run_cmd(cmd, cwd=cwd or ctx.project_root, capture=True)
    finally:
        if stop_event:
            stop_event.set()
        if t:
            t.join()
            _clear_line()

    if r.code != 0:
        msg = (r.err or r.out).strip()
        raise RuntimeError(f"{title} 失败：{msg or 'unknown error'}")


def _step_begin(ctx: Context, n: int, title: str) -> float:
    ctx.echo(f"\n[{n}] {title}  (start: {_now_str()})")
    return time.perf_counter()


def _step_end(ctx: Context, n: int, t0: float) -> None:
    cost = time.perf_counter() - t0
    ctx.echo(f"[{n}] done  (cost: {cost:.2f}s)")


def _confirm_or_abort(ctx: Context, prompt: str) -> None:
    """
    需要用户确认才能继续；否则直接中断。
    - ctx.yes：默认继续
    - 非交互：默认中断（除非 ctx.yes）
    """
    if ctx.yes:
        return
    if not getattr(ctx, "interactive", True):
        raise RuntimeError(f"{prompt}（非交互模式默认中断；如需继续请使用 --yes）")
    if not ctx.confirm(prompt):
        raise RuntimeError("已取消。")


def _ask_continue(ctx: Context, prompt: str) -> bool:
    """返回 True=中断，False=继续"""
    if ctx.yes:
        return False
    if not getattr(ctx, "interactive", True):
        return True
    return ctx.confirm(prompt)


# =======================
# Git helpers
# =======================
def _git_check_repo(ctx: Context) -> None:
    r = run_cmd(["git", "rev-parse", "--is-inside-work-tree"], cwd=ctx.project_root, capture=True)
    if r.code != 0 or (r.out or "").strip() != "true":
        raise RuntimeError("当前目录不是 git 仓库，无法执行拉取与自动提交。")


def _git_is_dirty(ctx: Context) -> bool:
    r = run_cmd(["git", "status", "--porcelain"], cwd=ctx.project_root, capture=True)
    if r.code != 0:
        raise RuntimeError(f"git status 失败：{(r.err or r.out).strip()}")
    return bool((r.out or "").strip())


def _git_current_branch(ctx: Context) -> str:
    r = run_cmd(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=ctx.project_root, capture=True)
    if r.code != 0:
        raise RuntimeError(f"获取当前分支失败：{(r.err or r.out).strip()}")
    return (r.out or "").strip()


def _git_has_remote_branch(ctx: Context, branch: str) -> bool:
    r = run_cmd(["git", "ls-remote", "--heads", "origin", branch], cwd=ctx.project_root, capture=True)
    if r.code != 0:
        return False
    return bool((r.out or "").strip())


def _git_pull_ff_only(ctx: Context) -> None:
    branch = _git_current_branch(ctx)
    if not _git_has_remote_branch(ctx, branch):
        ctx.echo("⚠️ 当前分支没有远程分支，跳过 git pull。")
        return
    ctx.echo(f"⬇️ 拉取远程分支 {branch}（ff-only）...")
    stop_event = threading.Event()
    _load_t0 = time.perf_counter()
    t = threading.Thread(target=_loading_animation, args=(stop_event, "git pull --ff-only", _load_t0))
    t.start()
    try:
        r = run_cmd(["git", "pull", "--ff-only"], cwd=ctx.project_root, capture=True)
    finally:
        stop_event.set()
        t.join()
        _clear_line()
    if r.code != 0:
        raise RuntimeError(
            "git pull 失败（可能存在分叉，需要手动 rebase/merge）：\n" + (r.err or r.out).strip()
        )
    ctx.echo("✅ git pull 完成")


def _git_add_commit_push(ctx: Context, *, new_version: str, old_version: str, note: str) -> None:
    pkg = _read_pubspec_name(read_text(ctx.pubspec_path)) or "(unknown)"
    subject = f"build: {pkg} + {new_version}"
    body = f"- version: {old_version} -> {new_version}\n- note: {note}"
    msg = subject + "\n\n" + body

    paths = ["pubspec.yaml", CHANGELOG_NAME]
    if (ctx.project_root / "pubspec.lock").exists():
        paths.append("pubspec.lock")

    r = run_cmd(["git", "add", *paths], cwd=ctx.project_root, capture=True)
    if r.code != 0:
        raise RuntimeError(f"git add 失败：{(r.err or r.out).strip()}")

    r = run_cmd(["git", "commit", "-m", msg], cwd=ctx.project_root, capture=True)
    if r.code != 0:
        raise RuntimeError(f"git commit 失败：{(r.err or r.out).strip()}")

    branch = _git_current_branch(ctx)
    if not _git_has_remote_branch(ctx, branch):
        ctx.echo("⚠️ 当前分支没有远程分支，跳过 git push。")
        return

    stop_event = threading.Event()
    _load_t0 = time.perf_counter()
    t = threading.Thread(target=_loading_animation, args=(stop_event, "git push", _load_t0))
    t.start()
    try:
        r = run_cmd(["git", "push"], cwd=ctx.project_root, capture=True)
    finally:
        stop_event.set()
        t.join()
        _clear_line()
    if r.code != 0:
        raise RuntimeError(f"git push 失败：{(r.err or r.out).strip()}")


# =======================
# pubspec / version helpers (text-level, keep structure)
# =======================
_VERSION_LINE_RE = re.compile(r"^(?P<prefix>\s*version:\s*)(?P<ver>\S+)(?P<suffix>\s*(?:#.*)?)$")
_SEMVER_CORE_RE = re.compile(r"^(?P<core>\d+(?:\.\d+){1,3})(?P<meta>.*)$")


def _read_pubspec_name(pubspec_text: str) -> Optional[str]:
    """读取 pubspec.yaml 顶层 name: 字段（容忍前导空格）"""
    for raw in pubspec_text.splitlines():
        m = re.match(r"^\s*name:\s*(\S+)\s*$", raw)
        if m:
            return m.group(1).strip()
    return None


def _read_pubspec_version(pubspec_text: str) -> Optional[str]:
    for raw in pubspec_text.splitlines():
        m = _VERSION_LINE_RE.match(raw)
        if m:
            return m.group("ver").strip()
    return None


def _bump_semver(version: str, mode: str) -> str:
    """
    mode: patch | minor
    保留 meta（+build / -pre / -pre+build），只 bump core 数字段。
    """
    m = _SEMVER_CORE_RE.match(version.strip())
    if not m:
        raise RuntimeError(f"无法解析 version：{version}")
    core = m.group("core")
    meta = m.group("meta") or ""
    nums = [int(x) for x in core.split(".")]
    while len(nums) < 3:
        nums.append(0)

    major, minor, patch = nums[0], nums[1], nums[2]
    if mode == "patch":
        patch += 1
    elif mode == "minor":
        minor += 1
        patch = 0
    else:
        raise RuntimeError(f"未知 bump mode：{mode}")

    return f"{major}.{minor}.{patch}{meta}"


def _parse_semver_3(version: str) -> tuple[tuple[int, int, int], str]:
    """解析 version 的前三段 core（x.y.z）并返回 (core_tuple, meta)。"""
    m = _SEMVER_CORE_RE.match(version.strip())
    if not m:
        raise RuntimeError(f"无法解析 version：{version}")
    core = m.group("core")
    meta = m.group("meta") or ""
    nums = [int(x) for x in core.split(".")]
    while len(nums) < 3:
        nums.append(0)
    return (nums[0], nums[1], nums[2]), meta


def _apply_pubspec_version(pubspec_text: str, new_version: str) -> tuple[str, str]:
    """
    只替换 version 行，不改其它内容/注释/结构。
    返回 (new_text, old_version)
    """
    lines = pubspec_text.splitlines(keepends=True)
    old_v: Optional[str] = None
    out: list[str] = []

    for line in lines:
        m = _VERSION_LINE_RE.match(line.rstrip("\n"))
        if not m or old_v is not None:
            out.append(line)
            continue

        old_v = m.group("ver").strip()
        out.append(f"{m.group('prefix')}{new_version}{m.group('suffix')}\n")

    if old_v is None:
        raise RuntimeError("pubspec.yaml 未找到 version: 行")
    return "".join(out), old_v


# =======================
# CHANGELOG helpers
# =======================
def _ensure_required_files(ctx: Context) -> None:
    if not ctx.pubspec_path.exists():
        raise RuntimeError(f"未找到 pubspec.yaml：{ctx.pubspec_path}")

    if REQUIRE_CHANGELOG:
        p = ctx.project_root / CHANGELOG_NAME
        if not p.exists():
            raise RuntimeError(f"未找到 {CHANGELOG_NAME}：{p}（发布要求必须存在）")


def _prepend_changelog_block(changelog_text: str, new_version: str, note: str, now_str: str) -> str:
    """
    示例格式：

    ## 3.45.12

    - 2026-01-23 18:27
    - jsb
    """
    block = f"## {new_version}\n\n- {now_str}\n- {note}\n\n"
    if not changelog_text:
        return block
    if changelog_text.startswith("\n"):
        return block + changelog_text.lstrip("\n")
    return block + changelog_text


# =======================
# flutter pub get / analyze / publish
# =======================
def flutter_pub_get(ctx: Context) -> None:
    cmd = (
        ["flutter", "pub", "get"]
        if shutil.which("flutter")
        else (["dart", "pub", "get"] if shutil.which("dart") else None)
    )
    if not cmd:
        raise RuntimeError("未找到 flutter/dart 命令，无法执行 pub get")
    _run_or_die(ctx, cmd, title="pub get", loading=True)


@dataclass(frozen=True)
class AnalyzeIssue:
    level: str  # error/warning/info
    text: str


def _parse_analyze_issues(output: str) -> list[AnalyzeIssue]:
    """
    匹配 analyze 常见格式：
      info • xxx at lib/a.dart:1:2 • (rule)
      warning • xxx ...
      error • xxx ...
    """
    issues: list[AnalyzeIssue] = []
    for raw in (output or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        m = re.match(r"^(info|warning|error)\s+•\s+(.*)$", line, flags=re.IGNORECASE)
        if m:
            issues.append(AnalyzeIssue(level=m.group(1).lower(), text=m.group(2).strip()))
    return issues


def _echo_raw_analyze_output(ctx: Context, out: str) -> None:
    """兜底输出 analyze 原始内容（截断，避免刷屏）。"""
    lines = [x.rstrip("\n") for x in (out or "").splitlines()]
    if not lines:
        ctx.echo("  | （无输出）")
        return
    show = lines[:MAX_SHOW_RAW_ANALYZE_LINES]
    for line in show:
        ctx.echo(f"  | {line}")
    if len(lines) > MAX_SHOW_RAW_ANALYZE_LINES:
        ctx.echo(f"  | ...（已截断，剩余 {len(lines) - MAX_SHOW_RAW_ANALYZE_LINES} 行未显示）")


def flutter_analyze_gate(ctx: Context) -> None:
    stop_event = threading.Event()
    _load_t0 = time.perf_counter()
    t = threading.Thread(target=_loading_animation, args=(stop_event, "flutter analyze", _load_t0))
    t.start()
    try:
        r = run_cmd(["flutter", "analyze"], cwd=ctx.project_root, capture=True)
    finally:
        stop_event.set()
        t.join()
        _clear_line()

    out = (r.out or "") + ("\n" + r.err if (r.err or "").strip() else "")
    issues = _parse_analyze_issues(out)

    errors = [x for x in issues if x.level == "error"]
    warnings = [x for x in issues if x.level == "warning"]
    infos = [x for x in issues if x.level == "info"]

    # 规则 1：error 一律中断（无论 exit code）
    if errors:
        ctx.echo(f"❌ flutter analyze error：共 {len(errors)} 条（将中断发布）")
        for it in errors[:MAX_SHOW_ERRORS]:
            ctx.echo(f"  - {it.text}")
        if len(errors) > MAX_SHOW_ERRORS:
            ctx.echo(f"  ... 还有 {len(errors) - MAX_SHOW_ERRORS} 条 error")
        raise RuntimeError("flutter analyze error，已中断发布。")

    # 如果 analyze 本身返回失败码，但没解析到 error，兜底打印原始输出后中断
    if r.code != 0:
        ctx.echo("❌ flutter analyze 返回失败码，但未解析到标准 error 行；原始输出如下（用于定位真实 issue）：")
        _echo_raw_analyze_output(ctx, out)
        raise RuntimeError("flutter analyze failed（unparsed output），已中断发布。")

    # 规则 2：warning 需要提示并询问是否继续
    if warnings:
        ctx.echo(f"⚠️ flutter analyze warning：共 {len(warnings)} 条")
        for it in warnings[:MAX_SHOW_WARNINGS]:
            ctx.echo(f"  - {it.text}")
        if len(warnings) > MAX_SHOW_WARNINGS:
            ctx.echo(f"  ... 还有 {len(warnings) - MAX_SHOW_WARNINGS} 条 warning")
        _confirm_or_abort(ctx, "存在 warning，是否继续发布？")

    # 规则 3：info 只提示数量 + 打印前两三条，然后继续（不确认）
    if infos:
        ctx.echo(f"ℹ️ flutter analyze info：共 {len(infos)} 条（仅提示，不阻断发布）")
        for it in infos[:MAX_SHOW_INFO]:
            ctx.echo(f"  - {it.text}")
        if len(infos) > MAX_SHOW_INFO:
            ctx.echo(f"  ...（仅展示前 {MAX_SHOW_INFO} 条）")


def flutter_pub_publish(ctx: Context, *, dry_run: bool) -> None:
    if shutil.which("flutter") is None:
        raise RuntimeError("未找到 flutter 命令，无法执行 pub publish")

    cmd = ["flutter", "pub", "publish"]
    if dry_run:
        cmd.append("--dry-run")
    else:
        cmd.append("--force")

    stop_event = threading.Event()
    _load_t0 = time.perf_counter()
    t = threading.Thread(
        target=_loading_animation,
        args=(stop_event, "flutter pub publish" if not dry_run else "flutter pub publish --dry-run", _load_t0),
    )
    t.start()
    try:
        r = run_cmd(cmd, cwd=ctx.project_root, capture=True)
    finally:
        stop_event.set()
        t.join()
        _clear_line()
    if r.code != 0:
        raise RuntimeError((r.err or r.out).strip() or "flutter pub publish 失败")


# =======================
# User interaction
# =======================
def _ask_note(ctx: Context) -> str:
    if ctx.yes or not getattr(ctx, "interactive", True):
        return DEFAULT_NOTE
    ctx.echo(f"请输入本次发布 note（直接回车使用默认：{DEFAULT_NOTE}）：")
    s = input("> ").strip()
    return s if s else DEFAULT_NOTE


# =======================
# Flows
# =======================
def publish(ctx: Context) -> int:
    _total_start_dt = datetime.now()
    _total_t0 = time.perf_counter()
    ctx.echo(f"⏱️ publish start: {_total_start_dt.strftime('%Y-%m-%d %H:%M:%S')}")

    # ✅ [1] 先输入 note，免去用户等待
    _step(ctx, 1, "输入发布说明 note")
    note = _ask_note(ctx)

    # [2] 检查 git 状态
    _step(ctx, 2, "检查是否有未提交变更")
    _git_check_repo(ctx)
    if _git_is_dirty(ctx):
        ctx.echo("⚠️ 检测到未提交变更（working tree dirty）。")
        if _ask_continue(ctx, "是否中断本次发布？"):
            ctx.echo("已中断。")
            return 1
        ctx.echo("继续执行（注意：可能把无关变更一起提交，建议先处理干净）。")
    else:
        ctx.echo("✅ 工作区干净")

    # [3] 拉取最新代码
    _t3 = _step_begin(ctx, 3, "拉取最新代码（git pull --ff-only）")
    _git_pull_ff_only(ctx)
    _step_end(ctx, 3, _t3)

    # [4] 必要文件检查
    _step(ctx, 4, "检查必要文件（pubspec.yaml / CHANGELOG.md）")
    _ensure_required_files(ctx)

    pubspec_text = read_text(ctx.pubspec_path)
    old_version = _read_pubspec_version(pubspec_text)
    if not old_version:
        raise RuntimeError("pubspec.yaml 未找到 version: 行，无法发布")

    # [5] 版本自增
    _step(ctx, 5, "版本自增")
    branch = _git_current_branch(ctx)
    mrel = re.match(r"^release-(\d+)\.(\d+)\.(\d+)$", branch)
    new_version: str
    if mrel:
        # release 分支：优先使用分支名中的版本号。
        # 例如：release-4.45.0
        rel_core = (int(mrel.group(1)), int(mrel.group(2)), int(mrel.group(3)))
        cur_core, meta = _parse_semver_3(old_version)

        # 若 release 版本号高于 pubspec.yaml 中的版本号，则直接更新到 release 版本。
        # 否则（相等或更低）一律走补丁版本自增。
        if rel_core > cur_core:
            new_version = f"{rel_core[0]}.{rel_core[1]}.{rel_core[2]}{meta}"
        else:
            new_version = _bump_semver(old_version, "patch")
    else:
        # 非 release 分支：一律补丁版本自增（x.y.z -> x.y.(z+1)）。
        new_version = _bump_semver(old_version, "patch")

    if new_version == old_version:
        raise RuntimeError(f"版本未变化：{old_version}")

    new_pubspec_text, old_version2 = _apply_pubspec_version(pubspec_text, new_version)
    if ctx.dry_run:
        ctx.echo(f"（dry-run）将把 version 从 {old_version2} 升级为 {new_version}")
    else:
        write_text_atomic(ctx.pubspec_path, new_pubspec_text)
        ctx.echo(f"✅ pubspec version: {old_version2} -> {new_version}")

    # [6] 更新 changelog（这里直接使用前面拿到的 note）
    _step(ctx, 6, "更新 CHANGELOG.md（按模板插入顶部）")
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    changelog_path = ctx.project_root / CHANGELOG_NAME
    changelog_text = read_text(changelog_path)
    new_changelog_text = _prepend_changelog_block(changelog_text, new_version, note, now_str)

    if ctx.dry_run:
        ctx.echo(f"（dry-run）将写入 changelog：## {new_version} / {now_str} / {note}")
    else:
        write_text_atomic(changelog_path, new_changelog_text)
        ctx.echo("✅ CHANGELOG 已更新")

    # [7] pub get
    _t7 = _step_begin(ctx, 7, "执行 flutter pub get")
    flutter_pub_get(ctx)
    ctx.echo("✅ pub get 通过")
    _step_end(ctx, 7, _t7)

    # [8] analyze gate
    _t8 = _step_begin(ctx, 8, "执行 flutter analyze（质量闸门）")
    flutter_analyze_gate(ctx)
    ctx.echo("✅ flutter analyze 通过（或已确认继续）")
    _step_end(ctx, 8, _t8)

    if ctx.dry_run:
        ctx.echo("（dry-run）跳过 git commit/push 与 publish。")
        return 0

    # [9] commit/push（仍用同一个 note）
    _t9 = _step_begin(ctx, 9, "提交代码（git add/commit/push）")
    _git_add_commit_push(ctx, new_version=new_version, old_version=old_version2, note=note)
    ctx.echo("✅ 已提交并推送（如有远程分支）")
    _step_end(ctx, 9, _t9)

    # [10] publish
    _step(ctx, 10, "执行 flutter pub publish")
    flutter_pub_publish(ctx, dry_run=False)
    ctx.echo("✅ 发布完成")

    _total_cost = time.perf_counter() - _total_t0
    _total_end_dt = datetime.now()
    ctx.echo(f"⏱️ end: {_total_end_dt.strftime('%Y-%m-%d %H:%M:%S')}  total: {_total_cost:.2f}s")
    return 0


def dry_run(ctx: Context) -> int:
    _total_start_dt = datetime.now()
    _total_t0 = time.perf_counter()
    ctx.echo(f"⏱️ dry_run start: {_total_start_dt.strftime('%Y-%m-%d %H:%M:%S')}")
    _step(ctx, 1, "检查必要文件（pubspec.yaml / CHANGELOG.md）")
    _ensure_required_files(ctx)

    _t2 = _step_begin(ctx, 2, "执行 flutter pub get")
    flutter_pub_get(ctx)
    ctx.echo("✅ pub get 通过")
    _step_end(ctx, 2, _t2)

    _t3 = _step_begin(ctx, 3, "执行 flutter analyze（质量闸门）")
    flutter_analyze_gate(ctx)
    ctx.echo("✅ flutter analyze 通过（或已确认继续）")
    _step_end(ctx, 3, _t3)

    _t4 = _step_begin(ctx, 4, "执行 flutter pub publish --dry-run")
    flutter_pub_publish(ctx, dry_run=True)
    ctx.echo("✅ dry-run 完成")
    _step_end(ctx, 4, _t4)
    return 0


def check(ctx: Context) -> int:
    ok = True
    messages: list[str] = []

    if not ctx.pubspec_path.exists():
        messages.append(f"❌ 未找到 pubspec.yaml：{ctx.pubspec_path}")
        ok = False
    else:
        t = read_text(ctx.pubspec_path)

        if not re.search(r"^\s*name:\s*\S+", t, flags=re.MULTILINE):
            messages.append("❌ pubspec.yaml 缺少 name:（package 发布必需）")
            ok = False

        if not re.search(r"^\s*version:\s*\S+", t, flags=re.MULTILINE):
            messages.append("❌ pubspec.yaml 缺少 version:")
            ok = False

        if re.search(r"^\s*publish_to:\s*none\s*$", t, flags=re.MULTILINE):
            messages.append("❌ pubspec.yaml 设置了 publish_to: none（禁止发布）")
            ok = False

    if REQUIRE_CHANGELOG:
        p = ctx.project_root / CHANGELOG_NAME
        if not p.exists():
            messages.append(f"❌ 未找到 {CHANGELOG_NAME}：{p}")
            ok = False

    if ok:
        ctx.echo("✅ check 通过：必要文件与 pubspec 基础字段已验证。")
        return 0

    for m in messages:
        ctx.echo(m)
    ctx.echo("❌ check 未通过。")
    return 1


def run_menu(ctx: Context) -> int:
    return publish(ctx)
