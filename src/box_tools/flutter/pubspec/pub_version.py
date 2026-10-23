from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .tool import Context, read_text, write_text_atomic


@dataclass(frozen=True)
class VersionInfo:
    major: int
    minor: int
    patch: int
    build: str | None = None

    def format(self) -> str:
        core = f"{self.major}.{self.minor}.{self.patch}"
        return f"{core}+{self.build}" if self.build else core


# 只匹配一整行 version（保留原文件结构：我们只替换这一行）
_VERSION_LINE_RE = re.compile(
    r"^(?P<indent>\s*)version:(?P<space>\s*)(?P<body>[0-9]+)\.(?P<body2>[0-9]+)\.(?P<body3>[0-9]+)(?:\+(?P<build>[0-9A-Za-z.\-_]+))?(?P<trail>\s*)$"
)


def _find_version_line(lines: list[str]) -> tuple[int, re.Match]:
    for i, ln in enumerate(lines):
        m = _VERSION_LINE_RE.match(ln)
        if m:
            return i, m
    raise ValueError("pubspec.yaml 未找到合法的 version: 行")


def parse_version(raw: str) -> VersionInfo:
    lines = raw.splitlines()
    _, m = _find_version_line(lines)
    return VersionInfo(
        major=int(m.group("body")),
        minor=int(m.group("body2")),
        patch=int(m.group("body3")),
        build=m.group("build"),
    )


def bump_patch(v: VersionInfo) -> VersionInfo:
    return VersionInfo(v.major, v.minor, v.patch + 1, v.build)


def bump_minor(v: VersionInfo) -> VersionInfo:
    return VersionInfo(v.major, v.minor + 1, 0, v.build)


def apply_version_minimal(raw: str, new_version: str) -> str:
    # 只替换 version 行，保留 indent/空格/trailing 空白
    lines = raw.splitlines()
    idx, m = _find_version_line(lines)
    indent = m.group("indent") or ""
    space = m.group("space") or " "
    trail = m.group("trail") or ""
    lines[idx] = f"{indent}version:{space}{new_version}{trail}"
    # 保留原文件是否以换行结尾：splitlines() 会丢失末尾空行标记
    suffix_newline = "\n" if raw.endswith("\n") else ""
    return "\n".join(lines) + suffix_newline


# ----------------------------
# Git helpers
# ----------------------------
def _find_git_root(start: Path) -> Path | None:
    cur = start.resolve()
    if cur.is_file():
        cur = cur.parent
    for p in [cur, *cur.parents]:
        if (p / ".git").exists():
            return p
    return None


def _git(ctx: Context, cwd: Path, args: list[str], check: bool = True) -> subprocess.CompletedProcess:
    # NOTE: 不捕获输出时，git 的交互提示（如 credential helper）仍能正常工作
    cmd = ["git", *args]
    ctx.echo(f"$ {' '.join(cmd)}")
    return subprocess.run(cmd, cwd=str(cwd), text=True, check=check)


def _commit_and_push_pubspec(ctx: Context, pubspec_path: Path, old: str, new: str, mode: str) -> None:
    git_root = _find_git_root(pubspec_path)
    if not git_root:
        ctx.echo("⚠️ 未检测到 git 仓库（找不到 .git），跳过提交/推送")
        return

    rel_pubspec = pubspec_path.resolve().relative_to(git_root)

    # 只 stage pubspec.yaml（避免把其它未预期改动一起提交）
    _git(ctx, git_root, ["add", "--", str(rel_pubspec)])

    # 没有变更则不提交
    r = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "--", str(rel_pubspec)],
        cwd=str(git_root),
        text=True,
        capture_output=True,
    )
    if r.returncode != 0:
        ctx.echo("⚠️ 无法检查 git 暂存区状态，跳过提交/推送")
        return
    if not r.stdout.strip():
        ctx.echo("ℹ️ 暂存区没有 pubspec.yaml 变更，跳过提交/推送")
        return

    # commit note：你说我来决定，那就用稳定、可检索的格式
    # 例：chore(release): bump pubspec 1.2.3 -> 1.2.4 (patch)
    note = f"chore(release): bump pubspec {old} -> {new} ({mode})"

    _git(ctx, git_root, ["commit", "-m", note])

    # 推送到当前分支的默认远端（一般是 origin）
    # 不强行 -u，避免改变用户已有的 upstream 规则
    _git(ctx, git_root, ["push"])


def run(ctx: Context, mode: str) -> int:
    raw = read_text(ctx.pubspec_path)
    v = parse_version(raw)

    if mode == "show":
        ctx.echo(f"当前 version = {v.format()}")
        return 0

    if mode == "patch":
        nv = bump_patch(v)
    elif mode == "minor":
        nv = bump_minor(v)
    else:
        raise ValueError(f"未知 mode：{mode}")

    ctx.echo(f"版本变更：{v.format()} -> {nv.format()}")

    if ctx.dry_run:
        ctx.echo("（dry-run）不写入 pubspec.yaml；也不会 git 提交/推送")
        return 0

    if ctx.interactive and (not ctx.yes):
        if not ctx.confirm("确认写入 pubspec.yaml，并立即提交推送到远端？"):
            ctx.echo("已取消")
            return 1

    new_raw = apply_version_minimal(raw, nv.format())
    write_text_atomic(ctx.pubspec_path, new_raw)
    ctx.echo("✅ version 升级完成（仅修改 version 行，未改动其它结构/注释）")

    # 立即 git commit + push（只提交 pubspec.yaml）
    try:
        _commit_and_push_pubspec(
            ctx=ctx,
            pubspec_path=Path(ctx.pubspec_path),
            old=v.format(),
            new=nv.format(),
            mode=mode,
        )
    except FileNotFoundError:
        ctx.echo("⚠️ 未找到 git 命令（请先安装 git），跳过提交/推送")
    except subprocess.CalledProcessError as e:
        ctx.echo(f"⚠️ git 命令执行失败（returncode={e.returncode}），已停止后续推送")
        return 1

    return 0


def run_menu(ctx: Context) -> int:
    menu = [
        ("show", "查看当前版本"),
        ("patch", "补丁版本升级（patch +1）"),
        ("minor", "小版本升级（minor +1，patch=0）"),
    ]
    while True:
        ctx.echo("\n=== pubspec version ===")
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
        return run(ctx, mode=cmd)
