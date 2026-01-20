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
    "summary": "升级 pubspec.yaml 中的私有 hosted/url 依赖（比对清单 + 确认；升级不跨 next minor，例如 3.45.* 只能升级到 < 3.46.0）",
    "usage": [
        "pub_upgrade",
        "pub_upgrade --yes",
        "pub_upgrade --no-commit",
        "pub_upgrade --private-host dart.cloudsmith.io",
        "pub_upgrade --private-host dart.cloudsmith.io --private-host my.private.repo",
        "pub_upgrade --skip ap_recaptcha --skip some_pkg",
    ],
    "options": [
        {"flag": "--yes", "desc": "跳过确认，直接执行升级"},
        {"flag": "--no-commit", "desc": "只更新依赖与 lock，不执行 git commit/push"},
        {
            "flag": "--private-host",
            "desc": "私服 hosted url 关键字（可多次指定）。默认不过滤：任何 hosted/url 都算私有依赖",
        },
        {"flag": "--skip", "desc": "跳过某些包名（可多次指定）"},
    ],
    "examples": [
        {"cmd": "pub_upgrade", "desc": "默认交互：比对 -> 展示清单 -> 确认升级"},
        {"cmd": "pub_upgrade --yes --no-commit", "desc": "直接升级（不提交）"},
        {"cmd": "pub_upgrade --private-host my.private.repo", "desc": "仅升级 url 含关键词的 hosted 私有依赖"},
    ],
    "docs": "src/box_tools/flutter/pub_upgrade.md",
}


@dataclass(frozen=True)
class UpgradeItem:
    name: str
    current: str
    latest: str  # 这里表示“选定的目标版本”，不一定是 pub 的 latest


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
    # 允许：^1.2.3、1.2.3、1.2.3+build、1.2.3-pre、1.2.3-pre+build
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


def read_pubspec_app_version(pubspec_path: str = "pubspec.yaml") -> str | None:
    """
    读取 pubspec.yaml 顶层 version: 字段
    支持：
      version: 3.45.0+2026011900
      version: 3.45.3
    """
    p = Path(pubspec_path)
    if not p.exists():
        return None

    for raw in p.read_text(encoding="utf-8").splitlines():
        # 顶层 version 一般无缩进；这里容忍前导空格
        m = re.match(
            r"^\s*version:\s*([0-9]+(?:\.[0-9]+){1,3}(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?)\s*$",
            raw,
        )
        if m:
            v = m.group(1).strip()
            return v if is_valid_version(v) else None
    return None


def upper_bound_of_minor(app_version: str) -> str | None:
    """
    给定 app_version（例如 3.45.1 或 3.45.0+xxxx）
    返回严格上界：下一 minor 的 0（例如 3.46.0）
    规则：依赖允许升级到 < upper_bound（不能等于或超过）
    """
    if not is_valid_version(app_version):
        return None
    parts = _version_parts(app_version)
    if len(parts) < 2:
        return None
    major, minor = parts[0], parts[1]
    return f"{major}.{minor + 1}.0"


def version_lt(v: str, upper: str) -> bool:
    return compare_versions(_strip_meta(v), _strip_meta(upper)) < 0


def pick_best_below_upper(candidates: list[str], upper: str) -> str | None:
    ok = [c for c in candidates if c and is_valid_version(c) and version_lt(c, upper)]
    if not ok:
        return None
    ok.sort(key=lambda x: _version_parts(x))
    return ok[-1]


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
        # 有些环境把 json 打 stderr；所以只要有内容就尝试解析
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


def get_outdated_map() -> dict[str, dict[str, str]]:
    """
    返回：
      {
        "pkg": {
           "current": "...",
           "upgradable": "...",
           "resolvable": "...",
           "latest": "..."
        }
      }

    说明：
    - 为了满足“< next minor (exclusive)”的需求，不能只看 latest；
      需要在 upgradable/resolvable/latest 里选一个最优且满足上限的目标。
    """
    raw_out, raw_err, code = _run_outdated_json()
    if code != 0:
        print("❌ `pub outdated --json` 执行失败。")
        if raw_err.strip():
            print("\nstderr:\n" + raw_err.strip())
        if raw_out.strip():
            print("\nstdout:\n" + raw_out.strip())
        raise SystemExit(1)

    data = _parse_outdated_json(raw_out, raw_err)

    def norm(x) -> str:
        return str(x).strip() if is_valid_version(x) else ""

    m: dict[str, dict[str, str]] = {}
    for pkg_info in data.get("packages", []):
        name = (pkg_info.get("package") or "").strip()
        if not name:
            continue

        cur = norm((pkg_info.get("current") or {}).get("version"))
        if not cur:
            continue

        upg = norm((pkg_info.get("upgradable") or {}).get("version"))
        res = norm((pkg_info.get("resolvable") or {}).get("version"))
        lat = norm((pkg_info.get("latest") or {}).get("version"))

        m[name] = {"current": cur, "upgradable": upg, "resolvable": res, "latest": lat}

    return m


# =======================
# pubspec helpers (private hosted/url detection + block update)
# =======================
def _extract_dependency_blocks(lines: list[str]) -> list[tuple[str, list[str], str]]:
    """
    提取 dependencies / dev_dependencies / dependency_overrides 三个 section 下的“每个依赖块”
    """
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
        msec = re.match(r"^(dependencies|dev_dependencies|dependency_overrides):\s*$", line)
        if msec:
            flush()
            in_section = True
            section = msec.group(1)
            continue

        if not in_section:
            continue

        # section 结束：遇到非空且不以两个空格缩进的行
        if line.strip() != "" and not re.match(r"^ {2}", line):
            flush()
            in_section = False
            section = ""
            continue

        # 新依赖块开始
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
    """
    私有组件定义：
    - 只要是 hosted + url，就认为是“私有 hosted/url 依赖”
    - 若用户传了 --private-host 关键词，则需 url 命中任一关键词
    """
    url = _private_hosted_url(block)
    if not url:
        return False
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
            # u.latest 是选定目标；写入时去掉 + / - 元信息
            target = _strip_meta(u.latest)
            b2, oldv, written = _apply_version_in_block(current_block, target)
            new_lines.extend(b2)

            if oldv and written and compare_versions(_strip_meta(oldv), _strip_meta(written)) < 0:
                changed = True
                summary_lines.append(f"🔄 {dep}: {oldv} → {written}")
        else:
            new_lines.extend(current_block)

        current_block.clear()
        current_dep = None

    for line in lines:
        msec = re.match(r"^(dependencies|dev_dependencies|dependency_overrides):\s*$", line)
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
# Planning logic (new rules)
# =======================
def build_private_upgrade_plan(
        *,
        private_host_keywords: tuple[str, ...],
        skip_packages: set[str],
        upper_bound: str | None,
) -> list[UpgradeItem]:
    """
    只升级“私有 hosted/url 依赖”，规则：
    - upper_bound 存在：只允许升级到 < upper_bound（例如 app 3.45.* 则 < 3.46.0）
      允许依赖版本高于 app version（例如 app 3.45.1，依赖可升到 3.45.10）
    - upper_bound 不存在：退化为“不升级依赖大版本”
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
    for name, info in outdated.items():
        if name in skip_packages:
            continue
        if name not in pubspec_deps:
            continue
        if name not in private_deps:
            continue

        cur = info.get("current", "")
        if not cur:
            continue

        # 候选：优先可达版本（upgradable/resolvable），latest 做兜底
        candidates = [
            info.get("upgradable", ""),
            info.get("resolvable", ""),
            info.get("latest", ""),
        ]

        if upper_bound:
            target = pick_best_below_upper(candidates, upper_bound)
            if not target:
                continue
        else:
            target = ""
            for c in candidates:
                if not c:
                    continue
                if major_of(c) > major_of(cur):
                    continue
                if (not target) or compare_versions(target, c) < 0:
                    target = c
            if not target:
                continue

        if compare_versions(cur, target) >= 0:
            continue

        plan.append(UpgradeItem(name=name, current=cur, latest=target))

    plan.sort(key=lambda x: x.name)
    return plan


def print_plan(plan: list[UpgradeItem]):
    if not plan:
        print("ℹ️ 未发现可升级依赖。")
        return
    print("发现以下可升级依赖（latest 表示选定目标版本）：")
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
        description="升级 Flutter 私有 hosted/url 依赖版本（比对清单 + 确认；依赖升级不跨 next minor，例如 3.45.* 只能升级到 < 3.46.0）",
    )
    p.add_argument("--yes", action="store_true", help="跳过确认，直接执行升级")
    p.add_argument("commit_message", nargs="?", default="up deps", help="Git 提交信息（默认 up deps）")
    p.add_argument("--no-commit", action="store_true", help="只更新依赖但不提交到 Git")

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

    if not Path("pubspec.yaml").exists():
        print("❌ 当前目录未找到 pubspec.yaml，请在项目根目录运行。")
        return 1

    # 默认不过滤域名：只要 hosted + url 就算私有组件
    private_host_keywords = tuple(args.private_host) if args.private_host else tuple()
    skip_packages = set(args.skip) if args.skip else {"ap_recaptcha"}

    branch = get_current_branch()
    git_pull_ff_only(branch)

    flutter_pub_get()

    app_version = read_pubspec_app_version("pubspec.yaml")
    upper = upper_bound_of_minor(app_version) if app_version else None

    if app_version and upper:
        print(f"📌 项目版本：{app_version}，依赖升级上限：<{upper}（允许升到同 minor 的最新 patch）")
    else:
        print("⚠️ 未能解析 pubspec.yaml 的 version，将退化为：不升级依赖大版本。")

    plan = build_private_upgrade_plan(
        private_host_keywords=private_host_keywords,
        skip_packages=skip_packages,
        upper_bound=upper,
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
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        # Ctrl+C：优雅退出，不打印 traceback
        print("\n已取消。")
        raise SystemExit(130)  # 130 = SIGINT 的惯例退出码
