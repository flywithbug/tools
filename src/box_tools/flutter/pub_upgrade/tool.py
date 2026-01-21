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


BOX_TOOL = {
    "id": "flutter.box_pub_upgrade",
    "name": "box_pub_upgrade",
    "category": "flutter",
    "summary": "升级 pubspec.yaml 中的私有 hosted/url 依赖（比对清单 + 确认；升级不跨 next minor，例如 3.45.* 只能升级到 < 3.46.0）",
    "usage": [
        "box_pub_upgrade",
        "box_pub_upgrade --yes",
        "box_pub_upgrade --no-git",
        "box_pub_upgrade --private-host dart.cloudsmith.io",
        "box_pub_upgrade --private-host dart.cloudsmith.io --private-host my.private.repo",
        "box_pub_upgrade --skip ap_recaptcha --skip some_pkg",
    ],
    "options": [
        {"flag": "--yes", "desc": "跳过确认，直接执行升级"},
        {"flag": "--no-git", "desc": "只更新依赖与 lock，不执行 git pull/commit/push（兼容 --no-commit）"},
        {
            "flag": "--private-host",
            "desc": "私服 hosted url 关键字（可多次指定）。默认不过滤：任何 hosted/url 都算私有依赖",
        },
        {"flag": "--skip", "desc": "跳过某些包名（可多次指定）"},
    ],
    "examples": [
        {"cmd": "box_pub_upgrade", "desc": "默认交互：比对 -> 展示清单 -> 确认升级"},
        {"cmd": "box_pub_upgrade --yes --no-git", "desc": "直接升级（不提交/不拉取）"},
        {"cmd": "box_pub_upgrade --private-host my.private.repo", "desc": "仅升级 url 含关键词的 hosted 私有依赖"},
    ],
    # ✅ 新项目规范：工具目录内 README.md
    "docs": "README.md",
}


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


def _parse_core_version(v: str) -> tuple[int, int, int] | None:
    v = (v or "").strip()
    m = _VERSION_RE.match(v)
    if not m:
        return None
    return int(m.group(1)), int(m.group(2)), int(m.group(3))


def _strip_meta(v: str) -> str:
    # ^1.2.3 / 1.2.3+build / 1.2.3-pre -> 1.2.3
    v = (v or "").strip()
    if v.startswith("^"):
        v = v[1:]
    v = v.split("+", 1)[0]
    v = v.split("-", 1)[0]
    return v.strip()


def _cmp_version(a: str, b: str) -> int:
    aa = _parse_core_version(_strip_meta(a)) or (0, 0, 0)
    bb = _parse_core_version(_strip_meta(b)) or (0, 0, 0)
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
    core = _strip_meta(project_version)
    parts = _parse_core_version(core)
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
    current_pkg = None
    current_block: list[str] = []
    current_indent = None

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
            # 新顶层段落开始
            flush()
            in_deps = False
            current_indent = None

        if not in_deps:
            continue

        # 包名行：两个空格起，形如 "  foo:"
        m = re.match(r"^(\s+)([A-Za-z0-9_]+)\s*:\s*(.*)$", line)
        if m:
            indent, pkg, tail = m.group(1), m.group(2), m.group(3)

            # 新包出现
            flush()
            current_pkg = pkg
            current_block = [line]
            current_indent = len(indent)
            # 单行依赖：  foo: ^1.2.3
            # 多行依赖：  foo:\n    hosted:...\n
            continue

        # 属于当前包块：缩进更深（或空行）
        if current_pkg is not None:
            if line.strip() == "":
                current_block.append(line)
                continue
            # 只要缩进 > current_indent 就认为属于块（保守）
            indent_len = len(line) - len(line.lstrip(" "))
            if current_indent is not None and indent_len > current_indent:
                current_block.append(line)
                continue

            # 否则块结束（但这行仍可能是另一个包名行，交给下一轮处理）
            flush()
            # 这一行会在下一轮被匹配到包名行或忽略；为简单起见不回退

    flush()
    return blocks


def _private_hosted_url(block: list[str]) -> str | None:
    """
    从 hosted 依赖块里提取 url 值（如果存在）
    """
    text = "".join(block)
    if "hosted:" not in text or "url:" not in text:
        return None
    m = re.search(r"^\s*url:\s*(.+?)\s*$", text, flags=re.MULTILINE)
    if not m:
        return None
    url = m.group(1).strip()
    # ✅ 兼容带引号的 YAML 写法
    url = url.strip('"').strip("'")
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
    first = block[0]
    m = re.search(r":\s*([^\s]+)\s*$", first)
    if m and m.group(1) and m.group(1) != "":
        tail = m.group(1).strip()
        # 单行依赖时 tail 可能是空或像 "{...}" 之类，这里做最小过滤
        if tail not in ("", "|", ">", ">", "{}", "[]"):
            if tail != "":
                # 如果是多行依赖，首行 tail 往往为空
                if tail != "":
                    # 多行时经常是空字符串（已被 regex 捕获为 ''），这里防御
                    pass
    # 多行：version:
    text = "".join(block)
    m2 = re.search(r"(?m)^\s*version:\s*([^\s]+)\s*$", text)
    if m2:
        return m2.group(1).strip()

    # 单行：foo: ^1.2.3
    m1 = re.search(r"^\s*[A-Za-z0-9_]+\s*:\s*([^\s]+)\s*$", first)
    if m1:
        v = m1.group(1).strip()
        if v and v not in ("", "{}"):
            return v
    return None


def _replace_constraint(block: list[str], new_constraint: str) -> tuple[list[str], bool]:
    """
    替换版本约束：
    - 多行：替换 version: xxx
    - 单行：替换 foo: xxx
    返回 (new_block, changed)
    """
    text = "".join(block)

    # 多行：version: ...
    if re.search(r"(?m)^\s*version:\s*", text):
        new_text, n = re.subn(
            r"(?m)^(\s*version:\s*)([^\s]+)\s*$",
            lambda m: f"{m.group(1)}{new_constraint}",
            text,
            count=1,
        )
        return new_text.splitlines(keepends=True), n > 0

    # 单行：foo: ^1.2.3
    first = block[0]
    m = re.match(r"^(\s*[A-Za-z0-9_]+\s*:\s*)([^\s]+)\s*$", first)
    if m:
        new_first = f"{m.group(1)}{new_constraint}\n"
        new_block = [new_first] + block[1:]
        return new_block, True

    return block, False


# =======================
# pub outdated
# =======================

@dataclass(frozen=True)
class Outdated:
    name: str
    current: str
    upgradable: str | None
    resolvable: str | None
    latest: str | None


def flutter_pub_get():
    cmd = ["flutter", "pub", "get"]
    if run_print(cmd) != 0:
        die("❌ flutter pub get 失败。", 1)


def _pub_outdated_json() -> dict[str, Any]:
    # flutter pub outdated --json
    r = run_capture(["flutter", "pub", "outdated", "--json"])
    if r.returncode != 0:
        # fallback: dart pub outdated --json
        r = run_capture(["dart", "pub", "outdated", "--json"])
    if r.returncode != 0:
        die(f"❌ pub outdated 失败：{(r.stderr or '').strip()}", 1)
    try:
        return json.loads(r.stdout or "{}")
    except Exception:
        die("❌ pub outdated 输出不是合法 JSON。", 1)
    return {}


def parse_outdated(data: dict[str, Any]) -> dict[str, Outdated]:
    """
    兼容 `pub outdated --json` 输出结构差异。
    """
    packages = (data.get("packages") or data.get("package")) or []
    out: dict[str, Outdated] = {}

    for item in packages:
        if not isinstance(item, dict):
            continue
        name = str(item.get("package") or item.get("name") or "").strip()
        if not name:
            continue

        current = str(item.get("current") or "").strip()
        upgradable = item.get("upgradable")
        resolvable = item.get("resolvable")
        latest = item.get("latest")

        out[name] = Outdated(
            name=name,
            current=current,
            upgradable=str(upgradable).strip() if upgradable else None,
            resolvable=str(resolvable).strip() if resolvable else None,
            latest=str(latest).strip() if latest else None,
        )

    return out


def choose_target_version(out: Outdated, upper_bound: str | None) -> str | None:
    """
    选择一个目标版本：优先 latest（如果不越界），否则退到 resolvable/upgradable。
    规则：如果给定 upper_bound，则要求 target < upper_bound。
    """
    candidates = [out.latest, out.resolvable, out.upgradable]
    for v in candidates:
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
    print("发现以下可升级依赖（latest 表示选定目标版本）：")
    for line in plan_lines:
        print("  - " + line)


# =======================
# CLI
# =======================

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="box_pub_upgrade",
        description="升级 Flutter 私有 hosted/url 依赖版本（比对清单 + 确认；依赖升级不跨 next minor，例如 3.45.* 只能升级到 < 3.46.0）",
    )
    p.add_argument("--yes", action="store_true", help="跳过确认，直接执行升级")
    p.add_argument("commit_message", nargs="?", default="up deps", help="Git 提交信息（默认 up deps）")

    # ✅ 统一参数命名：--no-git 为主，--no-commit 兼容旧用法
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
        help="跳过某些包名（可多次指定）。默认 ap_recaptcha",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    args = build_parser().parse_args(argv)

    pubspec = Path("pubspec.yaml")
    if not pubspec.exists():
        print("❌ 当前目录未找到 pubspec.yaml，请在项目根目录运行。")
        return 1

    # 默认不过滤域名：只要 hosted + url 就算私有组件
    private_host_keywords = tuple(args.private_host) if args.private_host else tuple()
    skip_packages = set(args.skip) if args.skip else {"ap_recaptcha"}

    # ✅ git 自动降级：非 git 仓库 / 无 git 命令时，视为 --no-git
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

    plan: list[tuple[str, str, str]] = []  # (pkg, old_constraint, new_version)
    plan_lines: list[str] = []             # for display
    summary_lines: list[str] = []          # for commit body

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

        # 如果目标不比当前高（数字层面），跳过
        if not _lt_version(cur_constraint, target):
            continue

        plan.append((pkg, cur_constraint, target))
        plan_lines.append(f"{pkg}: {cur_constraint} -> {target}")
        summary_lines.append(f"- {pkg}: {cur_constraint} -> {target}")

    print()
    print_plan(plan_lines)

    if not plan:
        return 0

    if not args.yes:
        if not prompt_yes_no():
            print("已取消。")
            return 0

    # 写回 pubspec.yaml（逐包替换对应块）
    changed = False
    new_pubspec_text = pubspec_text
    for pkg, old_constraint, target in plan:
        block = dep_blocks[pkg]
        new_block, ch = _replace_constraint(block, target)
        if not ch:
            continue

        # 用原块文本替换为新块文本（保守：以完整块字符串替换）
        old_block_text = "".join(block)
        new_block_text = "".join(new_block)
        if old_block_text != new_block_text:
            new_pubspec_text = new_pubspec_text.replace(old_block_text, new_block_text, 1)
            changed = True

    if changed:
        pubspec.write_text(new_pubspec_text, encoding="utf-8")
        print("✅ pubspec.yaml 已更新。")
    else:
        print("ℹ️ 没有发生实际修改（可能 pubspec 中版本写法不匹配或无需更新）。")
        return 0

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
        raise SystemExit(130)  # 130 = SIGINT 的惯例退出码
