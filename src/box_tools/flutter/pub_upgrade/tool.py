from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional


# ============================================================
# Errors / IO
# ============================================================

class AppError(SystemExit):
    def __init__(self, msg: str, code: int = 1) -> None:
        print(msg)
        super().__init__(code)


def die(msg: str, code: int = 1) -> None:
    raise AppError(msg, code)


# ============================================================
# Shell / Git
# ============================================================

class Shell:
    @staticmethod
    def has_cmd(cmd: str) -> bool:
        return shutil.which(cmd) is not None

    @staticmethod
    def run_capture(cmd: list[str], cwd: Optional[Path] = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(cmd, capture_output=True, text=True, cwd=str(cwd) if cwd else None)

    @staticmethod
    def run_print(cmd: list[str], cwd: Optional[Path] = None) -> int:
        p = subprocess.run(cmd, cwd=str(cwd) if cwd else None)
        return int(p.returncode)


class Git:
    @staticmethod
    def is_git_repo(cwd: Path) -> bool:
        if not Shell.has_cmd("git"):
            return False
        try:
            p = Shell.run_capture(["git", "rev-parse", "--is-inside-work-tree"], cwd=cwd)
            return p.returncode == 0 and (p.stdout or "").strip().lower() == "true"
        except Exception:
            return False

    @staticmethod
    def has_remote_branch(branch: str) -> bool:
        r = Shell.run_capture(["git", "ls-remote", "--heads", "origin", branch])
        return r.returncode == 0 and bool((r.stdout or "").strip())

    @staticmethod
    def current_branch() -> str:
        r = Shell.run_capture(["git", "rev-parse", "--abbrev-ref", "HEAD"])
        if r.returncode != 0:
            die(f"❌ 获取当前分支失败：{(r.stderr or '').strip()}", 1)
        return (r.stdout or "").strip()

    @staticmethod
    def pull_ff_only(branch: str) -> None:
        if not Git.has_remote_branch(branch):
            print("⚠️ 当前分支没有远程分支，跳过拉取。")
            return

        print(f"⬇️ 正在拉取远程分支 {branch}（ff-only）...")
        r = Shell.run_capture(["git", "pull", "--ff-only"])
        if r.returncode != 0:
            die(f"❌ git pull 失败：{(r.stderr or '').strip()}", 1)

    @staticmethod
    def commit_and_push(branch: str, commit_message: str, summary_lines: list[str]) -> None:
        if not summary_lines:
            print("ℹ️ 没有可提交的更新。")
            return

        full_commit_msg = commit_message + "\n\n" + "\n".join(summary_lines)

        try:
            subprocess.run(["git", "add", "pubspec.yaml", "pubspec.lock"], check=True)
            subprocess.run(["git", "commit", "-m", full_commit_msg], check=True)

            if Git.has_remote_branch(branch):
                subprocess.run(["git", "push"], check=True)
                print("✅ 提交并推送成功！")
            else:
                print("✅ 已提交到本地（未推送）。")
        except FileNotFoundError:
            print("⚠️ 未找到 git 命令，已跳过 git 操作。")
        except subprocess.CalledProcessError as e:
            die(f"❌ git 提交/推送失败：{e}", 1)


# ============================================================
# Version utils
# ============================================================

class Version:
    _CORE_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:[+-].*)?$")

    @staticmethod
    def strip_meta(v: str) -> str:
        v = (v or "").strip()
        if v.startswith("^"):
            v = v[1:]
        v = v.split("+", 1)[0]
        v = v.split("-", 1)[0]
        return v.strip()

    @staticmethod
    def parse_core(v: str) -> Optional[tuple[int, int, int]]:
        v = Version.strip_meta(v)
        m = Version._CORE_RE.match(v)
        if not m:
            return None
        return int(m.group(1)), int(m.group(2)), int(m.group(3))

    @staticmethod
    def cmp(a: str, b: str) -> int:
        aa = Version.parse_core(a) or (0, 0, 0)
        bb = Version.parse_core(b) or (0, 0, 0)
        return (aa > bb) - (aa < bb)

    @staticmethod
    def lt(a: str, b: str) -> bool:
        return Version.cmp(a, b) < 0


# ============================================================
# Pubspec parsing & editing
# ============================================================

@dataclass(frozen=True)
class DepBlock:
    name: str
    lines: list[str]
    section: str

    def text(self) -> str:
        return "".join(self.lines)


class PubspecEditor:
    @staticmethod
    def read_text(path: Path) -> str:
        return path.read_text(encoding="utf-8")

    @staticmethod
    def write_text(path: Path, text: str) -> None:
        path.write_text(text, encoding="utf-8")

    @staticmethod
    def get_project_version(pubspec_text: str) -> Optional[str]:
        m = re.search(r"(?m)^(?!\s*#)\s*version:\s*([^\s]+)\s*$", pubspec_text)
        return m.group(1).strip() if m else None

    @staticmethod
    def calc_upper_bound(project_version: str) -> Optional[str]:
        """
        允许升级到同 minor 的最高 patch：上限为 < next_minor.0
        3.46.0 -> <3.47.0
        """
        parts = Version.parse_core(project_version)
        if not parts:
            return None
        major, minor, _patch = parts
        return f"{major}.{minor + 1}.0"

    @staticmethod
    def find_dep_blocks(pubspec_text: str) -> dict[str, DepBlock]:
        lines = pubspec_text.splitlines(keepends=True)
        blocks: dict[str, DepBlock] = {}

        current_section: Optional[str] = None
        current_pkg: Optional[str] = None
        current_lines: list[str] = []
        current_indent: Optional[int] = None

        def flush():
            nonlocal current_pkg, current_lines, current_section
            if current_pkg and current_lines and current_section:
                blocks[current_pkg] = DepBlock(name=current_pkg, lines=current_lines[:], section=current_section)
            current_pkg = None
            current_lines = []

        for line in lines:
            m_section = re.match(r"^\s*(dependencies|dev_dependencies|dependency_overrides)\s*:\s*$", line)
            if m_section:
                flush()
                current_section = m_section.group(1)
                current_indent = None
                continue

            if current_section and re.match(r"^\S", line):
                flush()
                current_section = None
                current_indent = None
                continue

            if not current_section:
                continue

            m_pkg = re.match(r"^(\s+)([A-Za-z0-9_]+)\s*:\s*(.*)$", line)
            if m_pkg:
                indent, pkg = m_pkg.group(1), m_pkg.group(2)
                flush()
                current_pkg = pkg
                current_lines = [line]
                current_indent = len(indent)
                continue

            if current_pkg is not None:
                if line.strip() == "":
                    current_lines.append(line)
                    continue
                indent_len = len(line) - len(line.lstrip(" "))
                if current_indent is not None and indent_len > current_indent:
                    current_lines.append(line)
                    continue
                flush()

        flush()
        return blocks

    @staticmethod
    def private_hosted_url(block_text: str) -> Optional[str]:
        # 仅看 hosted: url: ...
        if "hosted:" not in block_text:
            return None
        m = re.search(r"(?m)^\s*url:\s*(.+?)\s*$", block_text)
        if not m:
            return None
        return m.group(1).strip().strip('"').strip("'")

    @staticmethod
    def is_hosted_dep(block: DepBlock) -> bool:
        # “私有依赖”定义：hosted(url=...) 的依赖块
        return PubspecEditor.private_hosted_url(block.text()) is not None

    @staticmethod
    def is_private_dep(block: DepBlock, private_host_keywords: tuple[str, ...]) -> bool:
        url = PubspecEditor.private_hosted_url(block.text())
        if not url:
            return False
        if not private_host_keywords:
            return True
        return any(k in url for k in private_host_keywords)

    @staticmethod
    def extract_constraint(block_text: str) -> Optional[str]:
        m2 = re.search(r"(?m)^\s*version:\s*([^\s]+)\s*$", block_text)
        if m2:
            return m2.group(1).strip()

        first_line = block_text.splitlines(True)[0] if block_text else ""
        m1 = re.search(r"^\s*[A-Za-z0-9_]+\s*:\s*([^\s]+)\s*$", first_line)
        if m1:
            v = m1.group(1).strip()
            if v and v not in ("", "{}"):
                return v
        return None

    @staticmethod
    def apply_prefix_like(original_spec: str, new_version: str) -> str:
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

    @staticmethod
    def replace_constraint(block_text: str, new_version: str) -> tuple[str, bool]:
        if re.search(r"(?m)^\s*version:\s*", block_text):
            m0 = re.search(r"(?m)^\s*version:\s*(.+?)\s*$", block_text)
            if not m0:
                return block_text, False
            old_spec = m0.group(1).strip()
            new_spec = PubspecEditor.apply_prefix_like(old_spec, new_version)

            new_text, n = re.subn(
                r"(?m)^(\s*version:\s*)(.+?)\s*$",
                lambda m: f"{m.group(1)}{new_spec}",
                block_text,
                count=1,
            )
            return new_text, n > 0

        lines = block_text.splitlines(True)
        if not lines:
            return block_text, False

        first = lines[0]
        m = re.match(r"^(\s*[A-Za-z0-9_]+\s*:\s*)(.+?)\s*$", first.rstrip("\n"))
        if not m:
            return block_text, False

        old_spec = (m.group(2) or "").strip()
        new_spec = PubspecEditor.apply_prefix_like(old_spec, new_version)
        lines[0] = f"{m.group(1)}{new_spec}\n"
        return "".join(lines), True


# ============================================================
# pub outdated --json parsing
# ============================================================

@dataclass(frozen=True)
class Outdated:
    name: str
    current: Optional[str]
    upgradable: Optional[str]
    resolvable: Optional[str]
    latest: Optional[str]


class Pub:
    @staticmethod
    def flutter_pub_get() -> None:
        if Shell.run_print(["flutter", "pub", "get"]) != 0:
            die("❌ flutter pub get 失败。", 1)

    @staticmethod
    def pub_outdated_json() -> dict[str, Any]:
        r = Shell.run_capture(["flutter", "pub", "outdated", "--json"])
        if r.returncode != 0:
            r = Shell.run_capture(["dart", "pub", "outdated", "--json"])
        if r.returncode != 0:
            die(f"❌ pub outdated 失败：{(r.stderr or '').strip()}", 1)

        try:
            return json.loads(r.stdout or "{}")
        except Exception:
            die("❌ pub outdated 输出不是合法 JSON。", 1)
        return {}

    @staticmethod
    def _get_ver(obj: Any) -> Optional[str]:
        if obj is None:
            return None
        if isinstance(obj, str):
            s = obj.strip()
            return s or None
        if isinstance(obj, dict):
            v = obj.get("version")
            if isinstance(v, str):
                s = v.strip()
                return s or None
        return None

    @staticmethod
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
                current=Pub._get_ver(item.get("current")),
                upgradable=Pub._get_ver(item.get("upgradable")),
                resolvable=Pub._get_ver(item.get("resolvable")),
                latest=Pub._get_ver(item.get("latest")),
            )

        return out


# ============================================================
# Planning
# ============================================================

@dataclass(frozen=True)
class UpgradeCandidate:
    package: str
    section: str
    is_private: bool
    is_hosted: bool
    old_constraint: str
    target_version: str
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class BlockedLatestItem:
    package: str
    section: str
    current_constraint: str
    latest: str
    reason: str
    is_private: bool
    is_hosted: bool


class Planner:
    @staticmethod
    def choose_target(out: Outdated, upper_bound: Optional[str]) -> Optional[str]:
        """
        选择目标版本：
        - 优先 latest（若不越界）
        - 否则 resolvable
        - 否则 upgradable
        越界：要求 target < upper_bound
        """
        for v in (out.latest, out.resolvable, out.upgradable):
            if not v:
                continue
            if upper_bound and not Version.lt(v, upper_bound):
                continue
            return v
        return None

    @staticmethod
    def build_candidates(
            dep_blocks: dict[str, DepBlock],
            outdated: dict[str, Outdated],
            private_host_keywords: tuple[str, ...],
            skip_packages: set[str],
            upper_bound: Optional[str],
            include_public: bool,
            include_private: bool,
    ) -> tuple[list[UpgradeCandidate], list[BlockedLatestItem]]:
        """
        include_private/include_public：用于控制候选集合范围
        - “私有依赖”：hosted(url=...) 且 url 命中关键字（若未提供关键字则视为全部 hosted 都算私有）
        - “其他依赖”：依赖块存在，但不属于私有依赖
        """
        candidates: list[UpgradeCandidate] = []
        blocked: list[BlockedLatestItem] = []

        for pkg, block in dep_blocks.items():
            if pkg in skip_packages:
                continue

            block_text = block.text()
            constraint = PubspecEditor.extract_constraint(block_text)
            if not constraint:
                continue

            is_hosted = PubspecEditor.is_hosted_dep(block)
            is_private = PubspecEditor.is_private_dep(block, private_host_keywords)

            if is_private and not include_private:
                continue
            if (not is_private) and not include_public:
                continue

            out = outdated.get(pkg)
            if not out:
                continue

            target = Planner.choose_target(out, upper_bound)

            # upper_bound 卡住：提示 latest 超上限
            if not target:
                if upper_bound and out.latest and Version.lt(constraint, out.latest) and not Version.lt(out.latest, upper_bound):
                    blocked.append(
                        BlockedLatestItem(
                            package=pkg,
                            section=block.section,
                            current_constraint=constraint,
                            latest=out.latest,
                            reason=f"latest 超出上限(<{upper_bound})，且无可用版本落在上限内",
                            is_private=is_private,
                            is_hosted=is_hosted,
                        )
                    )
                continue

            # 无需升级，但也可以提示 latest 被挡住（可选）
            if not Version.lt(constraint, target):
                if upper_bound and out.latest and Version.lt(constraint, out.latest) and not Version.lt(out.latest, upper_bound):
                    blocked.append(
                        BlockedLatestItem(
                            package=pkg,
                            section=block.section,
                            current_constraint=constraint,
                            latest=out.latest,
                            reason=f"latest 超出上限(<{upper_bound})；当前已在上限内可达的最高/合适版本",
                            is_private=is_private,
                            is_hosted=is_hosted,
                        )
                    )
                continue

            notes: list[str] = []
            if upper_bound and out.latest and not Version.lt(out.latest, upper_bound):
                notes.append(f"⚠️ latest={out.latest} 超出上限(<{upper_bound})，已按策略跳过跨 minor")

            candidates.append(
                UpgradeCandidate(
                    package=pkg,
                    section=block.section,
                    is_private=is_private,
                    is_hosted=is_hosted,
                    old_constraint=constraint,
                    target_version=target,
                    notes=tuple(notes),
                )
            )

        # 固定顺序：先私有、再其他；各自按 package 名排序（这样 list -> select 稳）
        priv = sorted([c for c in candidates if c.is_private], key=lambda x: x.package.lower())
        pub = sorted([c for c in candidates if not c.is_private], key=lambda x: x.package.lower())
        return priv + pub, blocked


# ============================================================
# Apply
# ============================================================

class Executor:
    @staticmethod
    def prompt_yes_no() -> bool:
        ans = input("是否执行升级？(y/N): ").strip().lower()
        return ans in ("y", "yes")

    @staticmethod
    def print_menu() -> None:
        print("可用功能：")
        print("  1) 根据项目 version 策略自动升级依赖（默认只升级私有 hosted 依赖）")
        print("  2) 列出待升级包（分私有/其他），显示当前约束与目标版本，并给出 index")
        print("  3) 通过 index 选择性升级（例如 1,3,5）")
        print()
        print("提示：运行 `-h` / `list -h` / `auto -h` / `select -h` 查看所有参数。")

    @staticmethod
    def print_candidates(candidates: list[UpgradeCandidate]) -> None:
        if not candidates:
            print("ℹ️ 没有可升级的依赖。")
            return

        priv = [c for c in candidates if c.is_private]
        pub = [c for c in candidates if not c.is_private]

        idx = 1
        if priv:
            print("【私有依赖】")
            for c in priv:
                suffix = ""
                if c.notes:
                    suffix = "  " + "；".join(c.notes)
                print(f"  [{idx}] {c.package} ({c.section}): {c.old_constraint} -> {c.target_version}{suffix}")
                idx += 1

        if pub:
            if priv:
                print()
            print("【其他依赖】")
            for c in pub:
                suffix = ""
                if c.notes:
                    suffix = "  " + "；".join(c.notes)
                print(f"  [{idx}] {c.package} ({c.section}): {c.old_constraint} -> {c.target_version}{suffix}")
                idx += 1

    @staticmethod
    def print_blocked(blocked: list[BlockedLatestItem]) -> None:
        if not blocked:
            return
        print()
        print("⚠️ 以下依赖存在更高 latest，但被当前“同 minor”升级策略挡住：")
        for it in sorted(blocked, key=lambda x: (not x.is_private, x.package.lower())):
            group = "私有" if it.is_private else "其他"
            print(f"  - ({group}) {it.package} ({it.section}): {it.current_constraint} (latest={it.latest})，原因：{it.reason}")

    @staticmethod
    def parse_indexes(indexes: str, max_index: int) -> list[int]:
        """
        支持：
          --indexes "1,3,5"
          --indexes "1 3 5"
          --indexes "1, 3, 5"
        """
        raw = (indexes or "").strip()
        if not raw:
            die("❌ indexes 为空。请用 --indexes \"1,3,5\" 指定要升级的项。", 1)

        parts = re.split(r"[,\s]+", raw)
        out: list[int] = []
        for p in parts:
            if not p:
                continue
            if not p.isdigit():
                die(f"❌ 非法 index：{p}", 1)
            n = int(p)
            if n < 1 or n > max_index:
                die(f"❌ index 超出范围：{n}（有效范围 1..{max_index}）", 1)
            out.append(n)

        # 去重保序
        seen = set()
        uniq: list[int] = []
        for n in out:
            if n not in seen:
                seen.add(n)
                uniq.append(n)
        return uniq

    @staticmethod
    def apply_to_pubspec(pubspec_text: str, dep_blocks: dict[str, DepBlock], selected: list[UpgradeCandidate]) -> tuple[str, bool]:
        new_text = pubspec_text
        changed = False

        for it in selected:
            block = dep_blocks.get(it.package)
            if not block:
                continue

            old_block_text = block.text()
            new_block_text, ch = PubspecEditor.replace_constraint(old_block_text, it.target_version)
            if not ch or old_block_text == new_block_text:
                continue

            new_text = new_text.replace(old_block_text, new_block_text, 1)
            changed = True

        return new_text, changed


# ============================================================
# CLI + Context builder
# ============================================================

@dataclass(frozen=True)
class Context:
    pubspec_path: Path
    pubspec_text: str
    project_version: Optional[str]
    upper_bound: Optional[str]
    dep_blocks: dict[str, DepBlock]
    outdated: dict[str, Outdated]


def load_context(pubspec_path: Path) -> Context:
    if not pubspec_path.exists():
        die("❌ 当前目录未找到 pubspec.yaml，请在项目根目录运行。", 1)

    Pub.flutter_pub_get()

    pubspec_text = PubspecEditor.read_text(pubspec_path)
    project_version = PubspecEditor.get_project_version(pubspec_text)
    upper_bound = PubspecEditor.calc_upper_bound(project_version) if project_version else None

    if project_version and upper_bound:
        print(f"📌 项目版本：{project_version}，依赖升级上限：<{upper_bound}（允许升到同 minor 的最新 patch）")
    elif project_version:
        print(f"📌 项目版本：{project_version}，未能计算 next minor 上限，将尽量保守选择版本。")
    else:
        print("📌 未检测到项目 version，将尽量保守选择版本。")

    dep_blocks = PubspecEditor.find_dep_blocks(pubspec_text)
    outdated_raw = Pub.pub_outdated_json()
    outdated = Pub.parse_outdated(outdated_raw)

    return Context(
        pubspec_path=pubspec_path,
        pubspec_text=pubspec_text,
        project_version=project_version,
        upper_bound=upper_bound,
        dep_blocks=dep_blocks,
        outdated=outdated,
    )


def add_common_flags(p: argparse.ArgumentParser) -> None:
    p.add_argument("--yes", action="store_true", help="跳过确认，直接执行")
    p.add_argument("--no-git", dest="no_git", action="store_true", help="不执行 git pull/commit/push")
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
    p.add_argument("commit_message", nargs="?", default="up deps", help="Git 提交信息（默认 up deps）")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="box_pub_upgrade",
        description="升级 Flutter 依赖（支持：自动升级、列出候选、按 index 选择升级；保留 ^/引号写法；遵循同 minor 上限策略）",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    # menu
    sp_menu = sub.add_parser("menu", help="输出功能列表（1/2/3），用于快速查看可用命令")
    # no extra args

    # list
    sp_list = sub.add_parser("list", help="列出待升级包（分私有/其他），显示 index")
    add_common_flags(sp_list)
    sp_list.add_argument(
        "--include-public",
        action="store_true",
        help="同时把“其他依赖”也纳入候选列表（默认只列私有依赖）",
    )

    # auto
    sp_auto = sub.add_parser("auto", help="根据项目 version 策略自动升级依赖（默认只升级私有依赖）")
    add_common_flags(sp_auto)
    sp_auto.add_argument(
        "--include-public",
        action="store_true",
        help="同时升级“其他依赖”（默认只升级私有依赖）",
    )

    # select
    sp_sel = sub.add_parser("select", help="列出候选后，通过 index 选择性升级（例如 1,3,5）")
    add_common_flags(sp_sel)
    sp_sel.add_argument(
        "--include-public",
        action="store_true",
        help="把“其他依赖”也加入候选集合（否则 index 只来自私有依赖）",
    )
    sp_sel.add_argument(
        "--indexes",
        required=True,
        help="要升级的 index 列表，例如：--indexes \"1,3,5\"",
    )

    return p


# ============================================================
# Command handlers
# ============================================================

def setup_git(no_git: bool) -> tuple[bool, Optional[str]]:
    git_enabled = (not no_git) and Git.is_git_repo(Path.cwd())
    if git_enabled:
        branch = Git.current_branch()
        Git.pull_ff_only(branch)
        return True, branch
    else:
        if not no_git:
            print("ℹ️ 当前目录不是 git 仓库或未安装 git，已自动跳过 git 操作（等同 --no-git）")
        return False, None


def build_candidates_from_args(ctx: Context, args: argparse.Namespace) -> tuple[list[UpgradeCandidate], list[BlockedLatestItem]]:
    private_host_keywords = tuple(args.private_host) if getattr(args, "private_host", None) else tuple()
    skip_packages = set(args.skip) if getattr(args, "skip", None) else set()

    include_public = bool(getattr(args, "include_public", False))
    include_private = True  # 永远至少包含私有（你们最关心）

    candidates, blocked = Planner.build_candidates(
        dep_blocks=ctx.dep_blocks,
        outdated=ctx.outdated,
        private_host_keywords=private_host_keywords,
        skip_packages=skip_packages,
        upper_bound=ctx.upper_bound,
        include_public=include_public,
        include_private=include_private,
    )
    return candidates, blocked


def do_list(ctx: Context, args: argparse.Namespace) -> int:
    candidates, blocked = build_candidates_from_args(ctx, args)
    print()
    Executor.print_candidates(candidates)
    Executor.print_blocked(blocked)
    return 0


def do_auto(ctx: Context, args: argparse.Namespace) -> int:
    git_enabled, branch = setup_git(args.no_git)

    candidates, blocked = build_candidates_from_args(ctx, args)

    print()
    Executor.print_candidates(candidates)
    Executor.print_blocked(blocked)

    if not candidates:
        return 0

    if not args.yes and not Executor.prompt_yes_no():
        print("已取消。")
        return 0

    new_pubspec_text, changed = Executor.apply_to_pubspec(ctx.pubspec_text, ctx.dep_blocks, candidates)
    if not changed:
        print("ℹ️ 没有发生实际修改（可能 pubspec 写法未被匹配到）。")
        return 0

    PubspecEditor.write_text(ctx.pubspec_path, new_pubspec_text)
    print("✅ pubspec.yaml 已更新。")

    Pub.flutter_pub_get()

    summary_lines = [f"- {c.package}: {c.old_constraint} -> {c.target_version}" for c in candidates]

    if not git_enabled:
        print("✅ 已更新依赖（未执行 git 操作）。")
        return 0

    assert branch is not None
    Git.commit_and_push(branch, args.commit_message, summary_lines)
    return 0


def do_select(ctx: Context, args: argparse.Namespace) -> int:
    git_enabled, branch = setup_git(args.no_git)

    candidates, blocked = build_candidates_from_args(ctx, args)

    print()
    Executor.print_candidates(candidates)
    Executor.print_blocked(blocked)

    if not candidates:
        return 0

    max_index = len(candidates)
    idxs = Executor.parse_indexes(args.indexes, max_index=max_index)
    selected = [candidates[i - 1] for i in idxs]

    print()
    print("将升级以下项：")
    for c in selected:
        group = "私有" if c.is_private else "其他"
        suffix = ""
        if c.notes:
            suffix = "  " + "；".join(c.notes)
        print(f"  - ({group}) {c.package} ({c.section}): {c.old_constraint} -> {c.target_version}{suffix}")

    if not args.yes and not Executor.prompt_yes_no():
        print("已取消。")
        return 0

    new_pubspec_text, changed = Executor.apply_to_pubspec(ctx.pubspec_text, ctx.dep_blocks, selected)
    if not changed:
        print("ℹ️ 没有发生实际修改（可能 pubspec 写法未被匹配到）。")
        return 0

    PubspecEditor.write_text(ctx.pubspec_path, new_pubspec_text)
    print("✅ pubspec.yaml 已更新。")

    Pub.flutter_pub_get()

    summary_lines = [f"- {c.package}: {c.old_constraint} -> {c.target_version}" for c in selected]

    if not git_enabled:
        print("✅ 已更新依赖（未执行 git 操作）。")
        return 0

    assert branch is not None
    Git.commit_and_push(branch, args.commit_message, summary_lines)
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    args = build_parser().parse_args(argv)

    if args.cmd == "menu":
        Executor.print_menu()
        return 0

    pubspec_path = Path("pubspec.yaml")
    ctx = load_context(pubspec_path)

    if args.cmd == "list":
        return do_list(ctx, args)
    if args.cmd == "auto":
        return do_auto(ctx, args)
    if args.cmd == "select":
        return do_select(ctx, args)

    die(f"❌ 未知命令：{args.cmd}", 1)
    return 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\n已取消。")
        raise SystemExit(130)
