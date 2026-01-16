from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from itertools import cycle
from pathlib import Path


BOX_TOOL = {
    "id": "flutter.pub_upgrade",
    "name": "pub_upgrade",
    "category": "flutter",
    "summary": "升级 pubspec.yaml 中的私服 hosted/url 依赖（比对清单 + 确认；release 分支可选跟随 x.y.*）",
    "usage": [
        "pub_upgrade",
        "pub_upgrade --yes",
        "pub_upgrade --no-commit",
        "pub_upgrade --follow-release",
        "pub_upgrade --no-follow-release",
        "pub_upgrade --private-host dart.cloudsmith.io",
        "pub_upgrade --private-host dart.cloudsmith.io --private-host my.private.repo",
    ],
    "options": [
        {"flag": "--yes", "desc": "跳过确认，直接执行升级"},
        {"flag": "--no-commit", "desc": "只更新依赖与 lock，不执行 git commit/push"},
        {"flag": "--follow-release", "desc": "在 release-x.y 分支：仅升级到 x.y.*（并允许从更低版本升上来）"},
        {"flag": "--no-follow-release", "desc": "在 release-x.y 分支：不跟随 x.y.*，走“非 release 分支策略”"},
        {"flag": "--private-host", "desc": "私服 hosted url 关键字（可多次指定）。默认 dart.cloudsmith.io"},
        {"flag": "--skip", "desc": "跳过某些包名（可多次指定）"},
    ],
    "examples": [
        {"cmd": "pub_upgrade", "desc": "默认交互：比对 -> 展示清单 -> 确认升级"},
        {"cmd": "pub_upgrade --yes --no-commit", "desc": "直接升级（不提交）"},
        {"cmd": "pub_upgrade --follow-release", "desc": "release 分支严格跟随 x.y.*"},
    ],
    "docs": "src/box_tools/flutter/pub_upgrade.md",
}


@dataclass(frozen=True)
class UpgradeItem:
    name: str
    current: str
    latest: str


# =======================
# Console helpers
# =======================
def clear_line():
    sys.stdout.write("\r\033[2K")
    sys.stdout.flush()


def run_capture(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def die(msg: str, code: int = 1):
    print(msg)
    raise SystemExit(code)


# =======================
# Git helpers
# =======================
def get_current_branch() -> str:
    r = run_capture(["git", "rev-parse", "--abbrev-ref", "HEAD"])
    if r.returncode != 0:
        die(f"❌ 获取当前分支失败：{(r.stderr or '').strip()}", 1)
    return (r.stdout or "").strip()


def has_remote_branch(branch_name: str) -> bool:
    r = run_capture(["git", "ls-remote", "--heads", "origin", branch_name])
    return bool((r.stdout or "").strip())


def git_pull_ff_only(branch: str):
    if not has_remote_branch(branch):
        print("⚠️ 当前分支没有远程分支，跳过拉取。")
        return

    print(f"⬇️ 正在拉取远程分支 {branch}（ff-only）...")
    r = run_capture(["git", "pull", "--ff-only"])
    clear_line()
    if r.returncode != 0:
        print("❌ 拉取失败（可能存在分叉，需要手动处理 rebase/merge）：")
        print((r.stderr or "").strip())
        raise SystemExit(1)
    print("✅ 拉取成功。")


def git_commit_and_push(branch: str, commit_message: str, summary_lines: list[str]):
    if not summary_lines:
        print("ℹ️ 没有可提交的更新。")
        return

    full_commit_msg = commit_message + "\n\n" + "\n".join(summary_lines)

    subprocess.run(["git", "add", "pubspec.yaml", "pubspec.lock"], check=True)
    subprocess.run(["git", "commit", "-m", full_commit_msg], check=True)

    if has_remote_branch(branch):
        subprocess.run(["git", "push"], check=True)
        print("✅ 提交并推送成功！")
    else:
        print("✅ 已提交到本地（未推送）。")


# =======================
# Version utils
# =======================
def is_valid_version(version) -> bool:
    if not isinstance(version, str):
        return False
    v = version.strip()
    return bool(re.fullmatch(r"^\^?[0-9]+(?:\.[0-9]+)*(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$", v))


def _strip_meta(v: str) -> str:
    v = v.strip().lstrip("^")
    v = v.split("+", 1)[0]
    v = v.split("-", 1)[0]
    return v


def _version_parts(v: str) -> list[int]:
    core = _strip_meta(v)
    parts: list[int] = []
    for p in core.split("."):
        try:
            parts.append(int(p))
        except ValueError:
            break
    return parts or [0]


def compare_versions(v1: str, v2: str) -> int:
    a, b = _version_parts(v1), _version_parts(v2)
    m = max(len(a), len(b))
    a += [0] * (m - len(a))
    b += [0] * (m - len(b))
    return (a > b) - (a < b)


def major_of(v: str) -> int:
    parts = _version_parts(v)
    return parts[0] if parts else 0


def minor_prefix_of_branch(branch_name: str) -> str | None:
    m = re.match(r"release-(\d+)\.(\d+)", branch_name)
    if not m:
        return None
    return f"{m.group(1)}.{m.group(2)}"


# =======================
# pub outdated --json
# =======================
def _run_outdated_json() -> tuple[str, str, int]:
    cmds = [
        ["flutter", "pub", "outdated", "--json"],
        ["dart", "pub", "outdated", "--json"],
    ]
    last_out, last_err, last_code = "", "", 1
    for cmd in cmds:
        if shutil.which(cmd[0]) is None:
            continue
        proc = subprocess.run(cmd, capture_output=True, text=True)
        out = proc.stdout or ""
        err = proc.stderr or ""
        if proc.returncode == 0 and (out.strip() or err.strip()):
            return out, err, proc.returncode
        last_out, last_err, last_code = out, err, proc.returncode
    return last_out, last_err, last_code


def _extract_json_maybe(s: str) -> str | None:
    s = (s or "").strip()
    if not s:
        return None
    i, j = s.find("{"), s.rfind("}")
    if i != -1 and j != -1 and j > i:
        return s[i : j + 1]
    return None


def _parse_outdated_json(stdout: str, stderr: str) -> dict:
    candidates: list[str] = []
    if stdout.strip():
        candidates.append(stdout.strip())
    j1 = _extract_json_maybe(stdout)
    if j1:
        candidates.append(j1)

    if stderr.strip():
        candidates.append(stderr.strip())
    j2 = _extract_json_maybe(stderr)
    if j2:
        candidates.append(j2)

    last_err = None
    for c in candidates:
        try:
            return json.loads(c)
        except json.JSONDecodeError as e:
            last_err = e

    print("❌ 解析 `pub outdated --json` 输出失败。")
    if last_err:
        print("JSONDecodeError:", last_err)
    print("\n===== STDOUT BEGIN =====\n" + stdout.strip() + "\n===== STDOUT END =====")
    print("\n===== STDERR BEGIN =====\n" + stderr.strip() + "\n===== STDERR END =====")
    raise SystemExit(1)


def get_outdated_map() -> dict[str, tuple[str, str]]:
    raw_out, raw_err, code = _run_outdated_json()
    if code != 0:
        print("❌ `pub outdated --json` 执行失败。")
        if raw_err.strip():
            print("\nstderr:\n" + raw_err.strip())
        if raw_out.strip():
            print("\nstdout:\n" + raw_out.strip())
        raise SystemExit(1)

    data = _parse_outdated_json(raw_out, raw_err)

    m: dict[str, tuple[str, str]] = {}
    for pkg_info in data.get("packages", []):
        name = (pkg_info.get("package") or "").strip()
        if not name:
            continue
        current = (pkg_info.get("current") or {}).get("version")
        latest = (pkg_info.get("latest") or {}).get("version")
        if not (is_valid_version(current) and is_valid_version(latest)):
            continue
        m[name] = (str(current), str(latest))
    return m


# =======================
# pubspec helpers (private hosted/url detection + block update)
# =======================
def _extract_dependency_blocks(lines: list[str]) -> list[tuple[str, list[str], str]]:
    blocks: list[tuple[str, list[str], str]] = []
    in_section = False
    section = ""
    block: list[str] = []

    def flush():
        nonlocal block
        if not block:
            return
        m = re.match(r"^\s{2}(\S+):", block[0])
        if m:
            blocks.append((section, block[:], m.group(1)))
        block = []

    for line in lines:
        msec = re.match(r"^(dependencies|dependency_overrides):\s*$", line)
        if msec:
            flush()
            in_section = True
            section = msec.group(1)
            continue

        if not in_section:
            continue

        if line.strip() != "" and not re.match(r"^ {2}", line):
            flush()
            in_section = False
            section = ""
            continue

        if re.match(r"^ {2}\S+:", line):
            flush()
            block.append(line)
            continue

        if block:
            block.append(line)

    flush()
    return blocks


def _private_hosted_url(block: list[str]) -> str | None:
    """
    从 hosted 依赖块里提取 url 值（如果存在）
    """
    text = "".join(block)
    if "hosted:" not in text or "url:" not in text:
        return None
    m = re.search(r"^\s*url:\s*(\S+)\s*$", text, flags=re.MULTILINE)
    return m.group(1) if m else None


def _is_private_hosted_dep(block: list[str], private_host_keywords: tuple[str, ...]) -> bool:
    url = _private_hosted_url(block)
    if not url:
        return False
    # 如果没有关键词过滤，则只要 hosted/url 就算私服
    if not private_host_keywords:
        return True
    return any(kw in url for kw in private_host_keywords if kw)


def _apply_version_in_block(block: list[str], new_version: str) -> tuple[list[str], str | None, str | None]:
    if not block:
        return block, None, None

    # 单行： pkg: ^0.0.3
    m_inline = re.match(r"^(\s{2}\S+:\s*)(\S+)\s*$", block[0].rstrip("\n"))
    if m_inline:
        oldv = m_inline.group(2)
        nv = f"^{new_version}" if oldv.startswith("^") else new_version
        b2 = block[:]
        b2[0] = f"{m_inline.group(1)}{nv}\n"
        return b2, oldv, nv

    # 多行：找 version: 行
    idx = -1
    for i, line in enumerate(block):
        if re.match(r"^\s*version:\s*\S+", line):
            idx = i
            break
    if idx == -1:
        return block, None, None

    m_ver = re.match(r"(\s*version:\s*)(\S+)", block[idx])
    if not m_ver:
        return block, None, None

    oldv = m_ver.group(2)
    nv = f"^{new_version}" if oldv.startswith("^") else new_version

    b2 = block[:]
    b2[idx] = f"{m_ver.group(1)}{nv}\n"
    return b2, oldv, nv


def apply_upgrades_to_pubspec(pubspec_file: str, upgrades: list[UpgradeItem]) -> tuple[bool, list[str]]:
    lines = Path(pubspec_file).read_text(encoding="utf-8").splitlines(keepends=True)
    upgrade_map = {u.name: u for u in upgrades}

    new_lines: list[str] = []
    changed = False
    summary_lines: list[str] = []

    in_section = False
    current_block: list[str] = []
    current_dep: str | None = None

    def flush_block():
        nonlocal current_block, current_dep, changed
        if not current_block:
            return

        dep = current_dep
        if dep and dep in upgrade_map:
            u = upgrade_map[dep]
            target = _strip_meta(u.latest)
            b2, oldv, written = _apply_version_in_block(current_block, target)
            new_lines.extend(b2)

            if oldv and written and compare_versions(oldv, written) < 0:
                changed = True
                summary_lines.append(f"🔄 {dep}: {oldv} → {written}")
        else:
            new_lines.extend(current_block)

        current_block.clear()
        current_dep = None

    for line in lines:
        msec = re.match(r"^(dependencies|dependency_overrides):\s*$", line)
        if msec:
            flush_block()
            in_section = True
            new_lines.append(line)
            continue

        if not in_section:
            new_lines.append(line)
            continue

        if line.strip() != "" and not re.match(r"^ {2}", line):
            flush_block()
            in_section = False
            new_lines.append(line)
            continue

        mdep = re.match(r"^ {2}(\S+):", line)
        if mdep:
            flush_block()
            current_dep = mdep.group(1)
            current_block.append(line)
            continue

        if current_block:
            current_block.append(line)
        else:
            new_lines.append(line)

    flush_block()

    if changed:
        Path(pubspec_file).write_text("".join(new_lines), encoding="utf-8")

    return changed, summary_lines


# =======================
# flutter pub get (spinner)
# =======================
def loading_animation(stop_event: threading.Event, label: str):
    spinner = cycle(["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"])
    while not stop_event.is_set():
        sys.stdout.write(f"\r{next(spinner)} {label} ")
        sys.stdout.flush()
        time.sleep(0.1)
    clear_line()


def flutter_pub_get():
    if shutil.which("flutter") is None:
        die("❌ 未找到 flutter 命令，请确认 Flutter 已安装并在 PATH 中。", 1)

    stop_event = threading.Event()
    t = threading.Thread(target=loading_animation, args=(stop_event, "正在执行 flutter pub get..."))
    t.start()

    proc = subprocess.run(["flutter", "pub", "get"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    stop_event.set()
    t.join()
    clear_line()

    if proc.returncode != 0:
        die(f"❌ flutter pub get 失败：{(proc.stderr or '').strip()}", 1)

    print("✅ flutter pub get 执行成功！")


# =======================
# Planning logic (your rules)
# =======================
def choose_follow_release_interactive(prefix: str) -> bool:
    print(f"检测到当前为 release 分支，目标次版本为：{prefix}.*")
    ans = input(f"是否跟随 {prefix}.* 升级？(y/N): ").strip().lower()
    return ans in ("y", "yes")


def build_private_upgrade_plan(
        *,
        follow_release: bool,
        release_prefix: str | None,
        private_host_keywords: tuple[str, ...],
        skip_packages: set[str],
) -> list[UpgradeItem]:
    """
    只升级“私服 hosted/url 依赖”，规则：
    - release 且 follow_release=True：只允许升级到 release_prefix.*（允许从更低版本升上来）
    - 非 release：只升级小版本，不升级大版本（latest.major > current.major 则跳过）
    """
    pubspec = Path("pubspec.yaml")
    lines = pubspec.read_text(encoding="utf-8").splitlines(keepends=True)
    blocks = _extract_dependency_blocks(lines)

    pubspec_deps: set[str] = set()
    private_deps: set[str] = set()

    for _section, block, dep_name in blocks:
        pubspec_deps.add(dep_name)
        if _is_private_hosted_dep(block, private_host_keywords):
            private_deps.add(dep_name)

    outdated = get_outdated_map()

    plan: list[UpgradeItem] = []
    for name, (cur, lat) in outdated.items():
        if name in skip_packages:
            continue
        if name not in pubspec_deps:
            continue
        if name not in private_deps:
            continue
        if compare_versions(cur, lat) >= 0:
            continue

        if follow_release and release_prefix:
            if not _strip_meta(lat).startswith(release_prefix + "."):
                continue

        if not (follow_release and release_prefix):
            if major_of(lat) > major_of(cur):
                continue

        plan.append(UpgradeItem(name=name, current=cur, latest=lat))

    plan.sort(key=lambda x: x.name)
    return plan


def print_plan(plan: list[UpgradeItem]):
    if not plan:
        print("ℹ️ 未发现可升级依赖。")
        return
    print("发现以下可升级依赖：")
    for u in plan:
        print(f"  - {u.name}: {u.current} -> {u.latest}")


def confirm_apply() -> bool:
    ans = input("是否执行升级？(y/N): ").strip().lower()
    return ans in ("y", "yes")


# =======================
# CLI
# =======================
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="pub_upgrade",
        description="升级 Flutter 私服 hosted/url 依赖版本（比对清单 + 确认；release 分支可选跟随 x.y.*）",
    )
    p.add_argument("--yes", action="store_true", help="跳过确认，直接执行升级")
    p.add_argument("commit_message", nargs="?", default="up deps", help="Git 提交信息（默认 up deps）")
    p.add_argument("--no-commit", action="store_true", help="只更新依赖但不提交到 Git")
    grp = p.add_mutually_exclusive_group()
    grp.add_argument("--follow-release", action="store_true", help="release 分支：跟随 release 次版本号（x.y.*）")
    grp.add_argument("--no-follow-release", action="store_true", help="release 分支：不跟随（走非 release 策略）")

    p.add_argument(
        "--private-host",
        action="append",
        default=[],
        help="私服 hosted url 关键字（可多次指定）。默认 dart.cloudsmith.io",
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

    if not Path("pubspec.yaml").exists():
        print("❌ 当前目录未找到 pubspec.yaml，请在项目根目录运行。")
        return 1

    private_host_keywords = tuple(args.private_host) if args.private_host else ("dart.cloudsmith.io",)
    skip_packages = set(args.skip) if args.skip else {"ap_recaptcha"}

    branch = get_current_branch()
    git_pull_ff_only(branch)

    flutter_pub_get()

    release_prefix = minor_prefix_of_branch(branch)
    follow_release = False

    if release_prefix:
        if args.follow_release:
            follow_release = True
        elif args.no_follow_release:
            follow_release = False
        else:
            follow_release = choose_follow_release_interactive(release_prefix)

        if follow_release:
            print(f"📦 follow-release: 仅升级到 {release_prefix}.*（允许从更低版本升上来）")
        else:
            print("📦 release 分支但不跟随：按非 release 策略（不升级大版本）")

    plan = build_private_upgrade_plan(
        follow_release=follow_release,
        release_prefix=release_prefix,
        private_host_keywords=private_host_keywords,
        skip_packages=skip_packages,
    )

    print()
    print_plan(plan)
    print()

    if not plan:
        return 0

    if not args.yes:
        if not confirm_apply():
            print("ℹ️ 已取消，不进行任何修改。")
            return 0

    changed, summary_lines = apply_upgrades_to_pubspec("pubspec.yaml", plan)
    if not changed:
        print("ℹ️ 没有发生实际修改（可能 pubspec 中版本写法不匹配或无需更新）。")
        return 0

    flutter_pub_get()

    if args.no_commit:
        print("✅ 已更新依赖（未提交到 Git：--no-commit）。")
        return 0

    git_commit_and_push(branch, args.commit_message, summary_lines)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
