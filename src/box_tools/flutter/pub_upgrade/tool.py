from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


# =======================
# Helpers
# =======================

def die(msg: str, code: int = 1) -> None:
    print(msg)
    raise SystemExit(code)


def has_cmd(cmd: str) -> bool:
    return shutil.which(cmd) is not None


def is_git_repo(cwd: Path) -> bool:
    if not has_cmd("git"):
        return False
    try:
        p = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=str(cwd),
            check=True,
            capture_output=True,
            text=True,
        )
        return p.stdout.strip().lower() == "true"
    except Exception:
        return False


def run_capture(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, capture_output=True, text=True)


def run_print(cmd: list[str]) -> int:
    p = subprocess.run(cmd)
    return int(p.returncode)


# =======================
# Git
# =======================

def has_remote_branch(branch: str) -> bool:
    r = run_capture(["git", "ls-remote", "--heads", "origin", branch])
    return r.returncode == 0 and bool((r.stdout or "").strip())


def get_current_branch() -> str:
    r = run_capture(["git", "rev-parse", "--abbrev-ref", "HEAD"])
    if r.returncode != 0:
        die(f"❌ 获取当前分支失败：{(r.stderr or '').strip()}", 1)
    return (r.stdout or "").strip()


def git_pull_ff_only(branch: str):
    if not has_remote_branch(branch):
        print("⚠️ 当前分支没有远程分支，跳过拉取。")
        return

    print(f"⬇️ 正在拉取远程分支 {branch}（ff-only）...")
    r = run_capture(["git", "pull", "--ff-only"])
    if r.returncode != 0:
        die(f"❌ git pull 失败：{(r.stderr or '').strip()}", 1)


def git_commit_and_push(branch: str, commit_message: str, summary_lines: list[str]):
    if not summary_lines:
        print("ℹ️ 没有可提交的更新。")
        return

    full_commit_msg = commit_message + "\n\n" + "\n".join(summary_lines)

    try:
        subprocess.run(["git", "add", "pubspec.yaml", "pubspec.lock"], check=True)
        subprocess.run(["git", "commit", "-m", full_commit_msg], check=True)

        if has_remote_branch(branch):
            subprocess.run(["git", "push"], check=True)
            print("✅ 提交并推送成功！")
        else:
            print("✅ 已提交到本地（未推送）。")
    except FileNotFoundError:
        print("⚠️ 未找到 git 命令，已跳过 git 操作。")
    except subprocess.CalledProcessError as e:
        die(f"❌ git 提交/推送失败：{e}", 1)


# =======================
# Version utils
# =======================

_VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:[+-].*)?$")


def _strip_meta(v: str) -> str:
    # ^1.2.3 / 1.2.3+build / 1.2.3-pre -> 1.2.3
    v = (v or "").strip()
    if v.startswith("^"):
        v = v[1:]
    v = v.split("+", 1)[0]
    v = v.split("-", 1)[0]
    return v.strip()


def _parse_core_version(v: str) -> tuple[int, int, int] | None:
    v = _strip_meta(v)
    m = _VERSION_RE.match(v)
    if not m:
        return None
    return int(m.group(1)), int(m.group(2)), int(m.group(3))


def _cmp_version(a: str, b: str) -> int:
    aa = _parse_core_version(a) or (0, 0, 0)
    bb = _parse_core_version(b) or (0, 0, 0)
    return (aa > bb) - (aa < bb)


def _lt_version(a: str, b: str) -> bool:
    return _cmp_version(a, b) < 0


# =======================
# pubspec.yaml parsing
# =======================

def read_pubspec_text() -> str:
    return Path("pubspec.yaml").read_text(encoding="utf-8")


def get_project_version(pubspec_text: str) -> str | None:
    # 排除注释行：# version: ...
    m = re.search(r"(?m)^(?!\s*#)\s*version:\s*([^\s]+)\s*$", pubspec_text)
    return m.group(1).strip() if m else None


def _calc_upper_bound(project_version: str) -> str | None:
    """
    规则：不跨 next minor。
    项目版本 3.45.1 -> upper bound < 3.46.0
    """
    parts = _parse_core_version(project_version)
    if not parts:
        return None
    major, minor, _patch = parts
    return f"{major}.{minor + 1}.0"


def _find_dep_blocks(pubspec_text: str) -> dict[str, list[str]]:
    """
    粗粒度解析 dependencies/dev_dependencies/dependency_overrides 段中的“包块”。
    返回：包名 -> YAML block lines（含缩进）
    """
    lines = pubspec_text.splitlines(keepends=True)
    blocks: dict[str, list[str]] = {}

    in_deps = False
    current_pkg: str | None = None
    current_block: list[str] = []
    current_indent: int | None = None

    def flush():
        nonlocal current_pkg, current_block
        if current_pkg and current_block:
            blocks[current_pkg] = current_block[:]
        current_pkg = None
        current_block = []

    for line in lines:
        if re.match(r"^\s*(dependencies|dev_dependencies|dependency_overrides)\s*:\s*$", line):
            in_deps = True
            flush()
            current_indent = None
            continue

        if in_deps and re.match(r"^\S", line):
            flush()
            in_deps = False
            current_indent = None

        if not in_deps:
            continue

        m = re.match(r"^(\s+)([A-Za-z0-9_]+)\s*:\s*(.*)$", line)
        if m:
            indent, pkg = m.group(1), m.group(2)
            flush()
            current_pkg = pkg
            current_block = [line]
            current_indent = len(indent)
            continue

        if current_pkg is not None:
            if line.strip() == "":
                current_block.append(line)
                continue
            indent_len = len(line) - len(line.lstrip(" "))
            if current_indent is not None and indent_len > current_indent:
                current_block.append(line)
                continue

            flush()

    flush()
    return blocks


def _private_hosted_url(block: list[str]) -> str | None:
    """
    从 hosted 依赖块里提取 url 值（如果存在）
    """
    text = "".join(block)
    if "hosted:" not in text:
        return None
    m = re.search(r"(?m)^\s*url:\s*(.+?)\s*$", text)
    if not m:
        return None
    url = m.group(1).strip().strip('"').strip("'")
    return url


def _is_private_dep(block: list[str], private_host_keywords: tuple[str, ...]) -> bool:
    url = _private_hosted_url(block)
    if not url:
        return False
    if not private_host_keywords:
        return True
    return any(k in url for k in private_host_keywords)


def _extract_constraint(block: list[str]) -> str | None:
    """
    提取当前版本约束：
    - 单行：foo: ^1.2.3
    - 多行：version: ^1.2.3
    """
    text = "".join(block)
    m2 = re.search(r"(?m)^\s*version:\s*([^\s]+)\s*$", text)
    if m2:
        return m2.group(1).strip()

    first = block[0]
    m1 = re.search(r"^\s*[A-Za-z0-9_]+\s*:\s*([^\s]+)\s*$", first)
    if m1:
        v = m1.group(1).strip()
        if v and v not in ("", "{}"):
            return v
    return None


def _apply_prefix_like(original_spec: str, new_version: str) -> str:
    """
    将 new_version 按 original_spec 的“写法”进行包装：
    - 原来有 ^ 则保留 ^
    - 原来有引号则保留引号类型
    """
    s = (original_spec or "").strip()
    quote = ""
    if len(s) >= 2 and s[0] == s[-1] and s[0] in ("'", '"'):
        quote = s[0]
        s_inner = s[1:-1].strip()
    else:
        s_inner = s

    caret = "^" if s_inner.startswith("^") else ""
    out = f"{caret}{new_version}"
    return f"{quote}{out}{quote}" if quote else out


def _replace_constraint(block: list[str], new_version: str) -> tuple[list[str], bool]:
    """
    替换版本约束，并保留原写法：
    - 多行：替换 version: xxx（保留 ^ 与引号）
    - 单行：替换 foo: xxx（保留 ^ 与引号）
    返回 (new_block, changed)
    """
    text = "".join(block)

    if re.search(r"(?m)^\s*version:\s*", text):
        m0 = re.search(r"(?m)^\s*version:\s*(.+?)\s*$", text)
        if not m0:
            return block, False
        old_spec = m0.group(1).strip()
        new_spec = _apply_prefix_like(old_spec, new_version)

        new_text, n = re.subn(
            r"(?m)^(\s*version:\s*)(.+?)\s*$",
            lambda m: f"{m.group(1)}{new_spec}",
            text,
            count=1,
        )
        return new_text.splitlines(keepends=True), n > 0

    first = block[0]
    m = re.match(r"^(\s*[A-Za-z0-9_]+\s*:\s*)(.+?)\s*$", first)
    if m:
        old_spec = m.group(2).strip()
        new_spec = _apply_prefix_like(old_spec, new_version)
        new_first = f"{m.group(1)}{new_spec}\n"
        return [new_first] + block[1:], True

    return block, False


# =======================
# pub outdated
# =======================

@dataclass(frozen=True)
class Outdated:
    name: str
    current: str | None
    upgradable: str | None
    resolvable: str | None
    latest: str | None


def flutter_pub_get():
    cmd = ["flutter", "pub", "get"]
    if run_print(cmd) != 0:
        die("❌ flutter pub get 失败。", 1)


def _pub_outdated_json() -> dict[str, Any]:
    r = run_capture(["flutter", "pub", "outdated", "--json"])
    if r.returncode != 0:
        r = run_capture(["dart", "pub", "outdated", "--json"])
    if r.returncode != 0:
        die(f"❌ pub outdated 失败：{(r.stderr or '').strip()}", 1)
    try:
        return json.loads(r.stdout or "{}")
    except Exception:
        die("❌ pub outdated 输出不是合法 JSON。", 1)
    return {}


def _get_ver(obj: Any) -> str | None:
    """
    兼容两种结构：
    - "latest": {"version": "1.2.3"}
    - "latest": "1.2.3"（少见，但防御）
    - None
    """
    if obj is None:
        return None
    if isinstance(obj, str):
        return obj.strip() or None
    if isinstance(obj, dict):
        v = obj.get("version")
        if isinstance(v, str):
            return v.strip() or None
    return None


def parse_outdated(data: dict[str, Any]) -> dict[str, Outdated]:
    packages = data.get("packages") or []
    out: dict[str, Outdated] = {}

    for item in packages:
        if not isinstance(item, dict):
            continue

        name = str(item.get("package") or "").strip()
        if not name:
            continue

        out[name] = Outdated(
            name=name,
            current=_get_ver(item.get("current")),
            upgradable=_get_ver(item.get("upgradable")),
            resolvable=_get_ver(item.get("resolvable")),
            latest=_get_ver(item.get("latest")),
        )

    return out


def choose_target_version(out: Outdated, upper_bound: str | None) -> str | None:
    """
    选择一个目标版本：
    - 优先 latest（如果不越界）
    - 否则 resolvable
    - 否则 upgradable
    规则：如果给定 upper_bound，则要求 target < upper_bound。
    """
    for v in (out.latest, out.resolvable, out.upgradable):
        if not v:
            continue
        if upper_bound and not _lt_version(v, upper_bound):
            continue
        return v
    return None


def prompt_yes_no() -> bool:
    ans = input("是否执行升级？(y/N): ").strip().lower()
    return ans in ("y", "yes")


def print_plan(plan_lines: list[str]):
    if not plan_lines:
        print("ℹ️ 没有可升级的私有依赖。")
        return
    print("发现以下可升级依赖：")
    for line in plan_lines:
        print("  - " + line)


# =======================
# CLI
# =======================

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="box_pub_upgrade",
        description="升级 Flutter 私有 hosted/url 依赖版本（保留 ^/引号写法；不跨 next minor）",
    )
    p.add_argument("--yes", action="store_true", help="跳过确认，直接执行升级")
    p.add_argument("commit_message", nargs="?", default="up deps", help="Git 提交信息（默认 up deps）")

    p.add_argument("--no-git", dest="no_git", action="store_true", help="只更新依赖与 lock，不执行 git pull/commit/push")
    p.add_argument("--no-commit", dest="no_git", action="store_true", help="兼容旧参数：等同 --no-git")

    p.add_argument(
        "--private-host",
        action="append",
        default=[],
        help="私服 hosted url 关键字（可多次指定）。默认不过滤：任何 hosted/url 都算私有依赖",
    )
    p.add_argument(
        "--skip",
        action="append",
        default=[],
        help="跳过某些包名（可多次指定）。默认不跳过任何包",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    args = build_parser().parse_args(argv)

    pubspec = Path("pubspec.yaml")
    if not pubspec.exists():
        print("❌ 当前目录未找到 pubspec.yaml，请在项目根目录运行。")
        return 1

    private_host_keywords = tuple(args.private_host) if args.private_host else tuple()
    skip_packages = set(args.skip) if args.skip else set()

    git_enabled = (not args.no_git) and is_git_repo(Path.cwd())
    branch = None
    if git_enabled:
        branch = get_current_branch()
        git_pull_ff_only(branch)
    else:
        if not args.no_git:
            print("ℹ️ 当前目录不是 git 仓库或未安装 git，已自动跳过 git 操作（等同 --no-git）")

    flutter_pub_get()

    pubspec_text = read_pubspec_text()
    project_version = get_project_version(pubspec_text)
    upper = _calc_upper_bound(project_version) if project_version else None
    if project_version and upper:
        print(f"📌 项目版本：{project_version}，依赖升级上限：<{upper}（允许升到同 minor 的最新 patch）")
    elif project_version:
        print(f"📌 项目版本：{project_version}，未能计算 next minor 上限，将尽量保守选择版本。")
    else:
        print("📌 未检测到项目 version，将尽量保守选择版本。")

    dep_blocks = _find_dep_blocks(pubspec_text)

    outdated_raw = _pub_outdated_json()
    outdated = parse_outdated(outdated_raw)

    plan: list[tuple[str, str, str]] = []
    plan_lines: list[str] = []
    summary_lines: list[str] = []

    for pkg, block in dep_blocks.items():
        if pkg in skip_packages:
            continue
        if not _is_private_dep(block, private_host_keywords):
            continue

        cur_constraint = _extract_constraint(block)
        if not cur_constraint:
            continue

        out = outdated.get(pkg)
        if not out:
            continue

        target = choose_target_version(out, upper_bound=upper)
        if not target:
            continue

        # 只要 target 的数值版本比当前高就算升级（忽略 ^/引号/metadata）
        if not _lt_version(cur_constraint, target):
            continue

        plan.append((pkg, cur_constraint, target))
        plan_lines.append(f"{pkg}: {cur_constraint} -> {target}")
        summary_lines.append(f"- {pkg}: {cur_constraint} -> {target}")

    print()
    print_plan(plan_lines)

    if not plan:
        return 0

    if not args.yes and not prompt_yes_no():
        print("已取消。")
        return 0

    changed = False
    new_pubspec_text = pubspec_text

    for pkg, _old, target in plan:
        old_block = dep_blocks[pkg]
        new_block, ch = _replace_constraint(old_block, target)
        if not ch:
            continue

        old_block_text = "".join(old_block)
        new_block_text = "".join(new_block)

        if old_block_text != new_block_text:
            new_pubspec_text = new_pubspec_text.replace(old_block_text, new_block_text, 1)
            changed = True

    if not changed:
        print("ℹ️ 没有发生实际修改（可能 pubspec 写法未被匹配到）。")
        return 0

    pubspec.write_text(new_pubspec_text, encoding="utf-8")
    print("✅ pubspec.yaml 已更新。")

    flutter_pub_get()

    if not git_enabled:
        print("✅ 已更新依赖（未执行 git 操作）。")
        return 0

    assert branch is not None
    git_commit_and_push(branch, args.commit_message, summary_lines)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\n已取消。")
        raise SystemExit(130)
