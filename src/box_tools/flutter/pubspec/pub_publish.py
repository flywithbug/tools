from __future__ import annotations

import re
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
MAX_SHOW_WARNINGS = 20
MAX_SHOW_INFO = 3


# =======================
# Helpers
# =======================
def _step(ctx: Context, n: int, title: str) -> None:
    ctx.echo(f"\n[{n}] {title}")


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


def _run_or_die(ctx: Context, cmd: list[str], *, title: str, cwd: Optional[str] = None) -> None:
    r = run_cmd(cmd, cwd=cwd or ctx.project_root, capture=True)
    if r.code != 0:
        msg = (r.err or r.out).strip()
        raise RuntimeError(f"{title} 失败：{msg or 'unknown error'}")


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
    r = run_cmd(["git", "pull", "--ff-only"], cwd=ctx.project_root, capture=True)
    if r.code != 0:
        raise RuntimeError(
            "git pull 失败（可能存在分叉，需要手动 rebase/merge）：\n" + (r.err or r.out).strip()
        )
    ctx.echo("✅ git pull 完成")


def _git_add_commit_push(ctx: Context, *, new_version: str, old_version: str, note: str) -> None:
    subject = f"release {new_version}"
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

    r = run_cmd(["git", "push"], cwd=ctx.project_root, capture=True)
    if r.code != 0:
        raise RuntimeError(f"git push 失败：{(r.err or r.out).strip()}")


# =======================
# pubspec / version helpers (text-level, keep structure)
# =======================
_VERSION_LINE_RE = re.compile(r"^(?P<prefix>\s*version:\s*)(?P<ver>\S+)(?P<suffix>\s*(?:#.*)?)$")
_SEMVER_CORE_RE = re.compile(r"^(?P<core>\d+(?:\.\d+){1,3})(?P<meta>.*)$")


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
    cmd = ["flutter", "pub", "get"] if shutil.which("flutter") else (["dart", "pub", "get"] if shutil.which("dart") else None)
    if not cmd:
        raise RuntimeError("未找到 flutter/dart 命令，无法执行 pub get")
    _run_or_die(ctx, cmd, title="pub get")


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


def flutter_analyze_gate(ctx: Context) -> None:
    r = run_cmd(["flutter", "analyze"], cwd=ctx.project_root, capture=True)
    out = (r.out or "") + ("\n" + r.err if (r.err or "").strip() else "")
    issues = _parse_analyze_issues(out)

    errors = [x for x in issues if x.level == "error"]
    warnings = [x for x in issues if x.level == "warning"]
    infos = [x for x in issues if x.level == "info"]

    if r.code != 0:
        if errors:
            ctx.echo("❌ flutter analyze 存在错误（error）：")
            for it in errors[:MAX_SHOW_ERRORS]:
                ctx.echo(f"  - {it.text}")
            if len(errors) > MAX_SHOW_ERRORS:
                ctx.echo(f"  ... 还有 {len(errors) - MAX_SHOW_ERRORS} 条 error")
            raise RuntimeError("flutter analyze error，已中断发布。")
        raise RuntimeError((r.err or r.out).strip() or "flutter analyze 失败")

    if warnings:
        ctx.echo("⚠️ flutter analyze 存在 warning：")
        for it in warnings[:MAX_SHOW_WARNINGS]:
            ctx.echo(f"  - {it.text}")
        if len(warnings) > MAX_SHOW_WARNINGS:
            ctx.echo(f"  ... 还有 {len(warnings) - MAX_SHOW_WARNINGS} 条 warning")
        _confirm_or_abort(ctx, "存在 warning，是否继续发布？")

    if infos:
        ctx.echo(f"ℹ️ flutter analyze 存在 info：共 {len(infos)} 条")
        for it in infos[:MAX_SHOW_INFO]:
            ctx.echo(f"  - {it.text}")
        if len(infos) > MAX_SHOW_INFO:
            ctx.echo(f"  ...（仅展示前 {MAX_SHOW_INFO} 条）")
        _confirm_or_abort(ctx, "存在 info issue，是否继续发布？")


def flutter_pub_publish(ctx: Context, *, dry_run: bool) -> None:
    if shutil.which("flutter") is None:
        raise RuntimeError("未找到 flutter 命令，无法执行 pub publish")

    cmd = ["flutter", "pub", "publish"]
    if dry_run:
        cmd.append("--dry-run")

    # 非交互或 --yes：避免 flutter 自己的确认卡住
    if ctx.yes or not getattr(ctx, "interactive", True):
        cmd.append("--force")

    r = run_cmd(cmd, cwd=ctx.project_root, capture=True)
    if r.code != 0:
        raise RuntimeError((r.err or r.out).strip() or "flutter pub publish 失败")


# =======================
# User interaction
# =======================
def _ask_bump_mode(ctx: Context) -> str:
    if ctx.yes or not getattr(ctx, "interactive", True):
        return "patch"

    ctx.echo("版本升级方式：")
    ctx.echo("  1) patch（默认，x.y.z -> x.y.(z+1)）")
    ctx.echo("  2) minor（x.y.z -> x.(y+1).0）")
    ctx.echo("  3) custom（手动输入目标版本）")
    s = input("> ").strip().lower()
    if s in ("", "1", "p", "patch"):
        return "patch"
    if s in ("2", "m", "minor"):
        return "minor"
    if s in ("3", "c", "custom"):
        return "custom"
    ctx.echo("无效输入，使用默认 patch")
    return "patch"


def _ask_custom_version(ctx: Context) -> str:
    ctx.echo("请输入目标 version（例如 3.45.13 或 3.45.13+2026012301）：")
    v = input("> ").strip()
    if not v:
        raise RuntimeError("目标 version 不能为空")
    return v


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
    _step(ctx, 1, "检查是否有未提交变更")
    _git_check_repo(ctx)
    if _git_is_dirty(ctx):
        ctx.echo("⚠️ 检测到未提交变更（working tree dirty）。")
        if _ask_continue(ctx, "是否中断本次发布？"):
            ctx.echo("已中断。")
            return 1
        ctx.echo("继续执行（注意：可能把无关变更一起提交，建议先处理干净）。")
    else:
        ctx.echo("✅ 工作区干净")

    _step(ctx, 2, "拉取最新代码（git pull --ff-only）")
    _git_pull_ff_only(ctx)

    _step(ctx, 3, "检查必要文件（pubspec.yaml / CHANGELOG.md）")
    _ensure_required_files(ctx)

    pubspec_text = read_text(ctx.pubspec_path)
    old_version = _read_pubspec_version(pubspec_text)
    if not old_version:
        raise RuntimeError("pubspec.yaml 未找到 version: 行，无法发布")

    _step(ctx, 4, "版本自增")
    mode = _ask_bump_mode(ctx)
    if mode == "custom":
        new_version = _ask_custom_version(ctx)
    else:
        new_version = _bump_semver(old_version, mode)

    if new_version == old_version:
        raise RuntimeError(f"版本未变化：{old_version}")

    new_pubspec_text, old_version2 = _apply_pubspec_version(pubspec_text, new_version)
    if ctx.dry_run:
        ctx.echo(f"（dry-run）将把 version 从 {old_version2} 升级为 {new_version}")
    else:
        write_text_atomic(ctx.pubspec_path, new_pubspec_text)
        ctx.echo(f"✅ pubspec version: {old_version2} -> {new_version}")

    _step(ctx, 5, "输入发布说明 note")
    note = _ask_note(ctx)

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

    _step(ctx, 7, "执行 flutter pub get")
    flutter_pub_get(ctx)
    ctx.echo("✅ pub get 通过")

    _step(ctx, 8, "执行 flutter analyze（质量闸门）")
    flutter_analyze_gate(ctx)
    ctx.echo("✅ flutter analyze 通过（或已确认继续）")

    if ctx.dry_run:
        ctx.echo("（dry-run）跳过 git commit/push 与 publish。")
        return 0

    _step(ctx, 9, "提交代码（git add/commit/push）")
    _git_add_commit_push(ctx, new_version=new_version, old_version=old_version2, note=note)
    ctx.echo("✅ 已提交并推送（如有远程分支）")

    _step(ctx, 10, "执行 flutter pub publish")
    flutter_pub_publish(ctx, dry_run=False)
    ctx.echo("✅ 发布完成")
    return 0


def dry_run(ctx: Context) -> int:
    _step(ctx, 1, "检查必要文件（pubspec.yaml / CHANGELOG.md）")
    _ensure_required_files(ctx)

    _step(ctx, 2, "执行 flutter pub get")
    flutter_pub_get(ctx)
    ctx.echo("✅ pub get 通过")

    _step(ctx, 3, "执行 flutter analyze（质量闸门）")
    flutter_analyze_gate(ctx)
    ctx.echo("✅ flutter analyze 通过（或已确认继续）")

    _step(ctx, 4, "执行 flutter pub publish --dry-run")
    flutter_pub_publish(ctx, dry_run=True)
    ctx.echo("✅ dry-run 完成")
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
    menu = [
        ("publish", "发布（版本自增 + changelog + analyze + commit/push + publish）"),
        ("dry-run", "发布预演（analyze + pub publish --dry-run）"),
        ("check", "检查必要文件与 pubspec 基础字段"),
    ]
    while True:
        ctx.echo("\n=== pub publish ===")
        for i, (cmd, label) in enumerate(menu, start=1):
            ctx.echo(f"{i}. {cmd:<10} {label}")
        ctx.echo("0. back       返回")

        choice = input("> ").strip()
        if choice == "0":
            return 0
        if choice == "1":
            return publish(ctx)
        if choice == "2":
            return dry_run(ctx)
        if choice == "3":
            return check(ctx)
        ctx.echo("无效选择")
