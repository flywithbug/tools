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
UPGRADE_DEV_DEPENDENCIES = False        # 是否升级 dev_dependencies（默认关闭）
UPGRADE_DEPENDENCY_OVERRIDES = False    # 是否升级 dependency_overrides（默认关闭）


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


def git_pull_ff_only(ctx: Context) -> None:
    r = run_cmd(["git", "pull", "--ff-only"], cwd=ctx.project_root, capture=True)
    if r.code != 0:
        raise RuntimeError(f"git pull --ff-only 失败：{(r.err or r.out).strip()}")


def git_push(ctx: Context) -> None:
    r = run_cmd(["git", "push"], cwd=ctx.project_root, capture=True)
    if r.code != 0:
        raise RuntimeError(f"git push 失败：{(r.err or r.out).strip()}")


def git_current_branch(ctx: Context) -> str:
    r = run_cmd(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=ctx.project_root, capture=True)
    if r.code != 0:
        raise RuntimeError(f"获取当前分支失败：{(r.err or r.out).strip()}")
    return (r.out or "").strip()


def _git_pull_ff_only(ctx: Context) -> None:
    r = run_cmd(["git", "pull", "--ff-only"], cwd=ctx.project_root, capture=True)
    if r.code != 0:
        raise RuntimeError(f"git pull --ff-only 失败：{(r.err or r.out).strip()}")


def _git_current_branch(ctx: Context) -> str:
    r = run_cmd(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=ctx.project_root, capture=True)
    if r.code != 0:
        raise RuntimeError(f"获取当前分支失败：{(r.err or r.out).strip()}")
    return (r.out or "").strip()


def _git_has_remote_branch(ctx: Context, branch: str) -> bool:
    r = run_cmd(["git", "ls-remote", "--heads", "origin", branch], cwd=ctx.project_root, capture=True)
    if r.code != 0:
        raise RuntimeError(f"git ls-remote 失败：{(r.err or r.out).strip()}")
    return bool((r.out or "").strip())


def git_add_commit(ctx: Context, summary_lines: list[str], subject: str = "chore(pub): upgrade private deps") -> None:
    """
    统一提交（用于依赖升级/版本号升级）
    - 明确、可预期：只 add pubspec.yaml / pubspec.lock（若存在）
    - 自动 commit
    - push 失败不静默
    """
    body = "\n".join(summary_lines) if summary_lines else ""
    msg = subject + ("\n\n" + body if body else "")

    paths = ["pubspec.yaml"]
    if (ctx.project_root / "pubspec.lock").exists():
        paths.append("pubspec.lock")

    r = run_cmd(["git", "add", *paths], cwd=ctx.project_root, capture=True)
    if r.code != 0:
        raise RuntimeError(f"git add 失败：{(r.err or r.out).strip()}")

    r = run_cmd(["git", "commit", "-m", msg], cwd=ctx.project_root, capture=True)
    if r.code != 0:
        raise RuntimeError(f"git commit 失败：{(r.err or r.out).strip()}")

    # 如果有 remote 分支，则 push（且 push 失败要抛出）
    br = _git_current_branch(ctx)
    if _git_has_remote_branch(ctx, br):
        r = run_cmd(["git", "push"], cwd=ctx.project_root, capture=True)
        if r.code != 0:
            raise RuntimeError(f"git push 失败：{(r.err or r.out).strip()}")


# =======================
# Version parsing / compare
# =======================
def is_valid_version(v: str) -> bool:
    # 接受 semver（允许 -pre / +build），不做额外“聪明”推断
    return bool(re.match(r"^[0-9]+(?:\.[0-9]+){1,3}(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$", v.strip()))


def _split_caret(token: str) -> tuple[str, str]:
    """把 ^ 从比较逻辑里剥离（按你的要求）：
    - ^1.2.3 -> ('^', '1.2.3')
    - 1.2.3  -> ('',  '1.2.3')
    仅处理 caret（^）；不解析 range（>=、<、~ 等）。
    """
    t = (token or "").strip()
    if t.startswith("^"):
        return "^", t[1:].strip()
    return "", t


def _is_simple_constraint(token: Optional[str]) -> bool:
    """只认两种最常见写法：
    - 1.2.3
    - ^1.2.3
    其它表达式一律不参与比较/替换（避免自作聪明）。"""
    if not token:
        return False
    _, v = _split_caret(token)
    return is_valid_version(v)


def _extract_semver_for_compare(constraint: Optional[str]) -> Optional[str]:
    """从约束里提取可用于比较的版本号：
    - 只支持 1.2.3 或 ^1.2.3
    - 其它情况返回 None（不做“聪明解析”）
    """
    if not _is_simple_constraint(constraint):
        return None
    _, v = _split_caret(constraint or "")
    return v.strip()


def _numeric_parts(v: str) -> Optional[list[int]]:
    """仅用数字段做比较：1.2.3(.4)
    带 -pre / +build 的版本不做额外排序规则（返回 None，交给上层决定是否跳过比较）。"""
    if not re.match(r"^\d+(?:\.\d+){1,3}$", v.strip()):
        return None
    return [int(p) for p in v.strip().split(".")]


def compare_versions(v1: str, v2: str) -> int:
    """仅比较纯数字版本：1.2.3(.4)
    其它（含 -pre / +build）不在这里硬猜，调用方应当在拿不到数字 parts 时跳过比较。"""
    a, b = _numeric_parts(v1), _numeric_parts(v2)
    if a is None or b is None:
        raise ValueError("compare_versions 仅支持纯数字版本（例如 1.2.3）")
    m = max(len(a), len(b))
    a += [0] * (m - len(a))
    b += [0] * (m - len(b))
    return (a > b) - (a < b)


# =======================
# Pub / Flutter helpers
# =======================
def flutter_pub_get(ctx: Context, with_loading: bool = True) -> None:
    if with_loading:
        with loading(ctx, "flutter pub get"):
            r = run_cmd(["flutter", "pub", "get"], cwd=ctx.project_root, capture=True)
    else:
        r = run_cmd(["flutter", "pub", "get"], cwd=ctx.project_root, capture=True)



def flutter_analyze(ctx: Context, with_loading: bool = True) -> None:
    if with_loading:
        with loading(ctx, "flutter analyze"):
            r = run_cmd(["flutter", "analyze"], cwd=ctx.project_root, capture=True)
    else:
        r = run_cmd(["flutter", "analyze"], cwd=ctx.project_root, capture=True)
    if r.code != 0:
        raise RuntimeError((r.err or r.out).strip())
    if r.code != 0:
        raise RuntimeError((r.err or r.out).strip())


def flutter_analyze(ctx: Context) -> None:
    r = run_cmd(["flutter", "analyze"], cwd=ctx.project_root, capture=True)
    if r.code != 0:
        raise RuntimeError((r.err or r.out).strip())


@contextmanager
def loading(ctx: Context, title: str):
    stop = threading.Event()

    def worker():
        for c in cycle("⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"):
            if stop.is_set():
                break
            ctx.echo(f"\r{c} {title}...", end="")
            time.sleep(0.08)

    t = threading.Thread(target=worker, daemon=True)
    t.start()
    try:
        yield
    finally:
        stop.set()
        t.join(timeout=0.2)
        ctx.echo(f"\r✅ {title} 完成           ")


# =======================
# Pubspec parsing / upgrading (保留注释与结构)
# =======================
_SECTION_NAMES = ("dependencies:", "dev_dependencies:", "dependency_overrides:")

_VERSION_LINE_RE = re.compile(
    r"^(\s*)([A-Za-z0-9_]+)\s*:\s*([^\s#]+)\s*(#.*)?$"
)  # 简单行：foo: ^1.2.3  # comment


@dataclass
class UpgradeItem:
    name: str
    current: str
    target: str
    section: str  # dependencies/dev_dependencies/dependency_overrides


def _is_section_header(line: str) -> Optional[str]:
    s = line.strip()
    for k in _SECTION_NAMES:
        if s == k:
            return k[:-1]
    return None


def _block_indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _apply_version_in_block(block_lines: list[str], new_version: str) -> tuple[list[str], bool]:
    """
    尝试在一个依赖 block 里改版本号（支持：
      foo: ^1.2.3
      foo: 1.2.3
      foo:
        version: ^1.2.3
      foo:
        hosted: xxx
        version: ^1.2.3
    ）
    """
    changed = False
    out: list[str] = []
    for raw in block_lines:
        m = _VERSION_LINE_RE.match(raw.rstrip("\n\r"))
        if m:
            indent, name, ver, comment = m.group(1), m.group(2), m.group(3), m.group(4) or ""
            # 只替换 "version:" 行 or 顶层 "foo:" 行里像版本号/约束的 token
            if name == "version":
                if ver != new_version:
                    newline = "\n"
                    if raw.endswith("\r\n"):
                        newline = "\r\n"
                    out.append(
                        f"{indent}version: {new_version}"
                        f"{(' ' if comment and not comment.startswith(' ') else '')}{comment}{newline}"
                    )
                    changed = True
                else:
                    out.append(raw)
            else:
                # 依赖名行：foo: ^1.2.3
                # 允许 token: ^1.2.3 / 1.2.3 / >=1.2.3 <2.0.0 等（这里主要覆盖 caret 场景）
                if re.match(r"^[\^<>=~]?\d+\.\d+(\.\d+)?", ver):
                    if ver != new_version:
                        newline = "\n"
                        if raw.endswith("\r\n"):
                            newline = "\r\n"
                        out.append(
                            f"{indent}{name}: {new_version}"
                            f"{(' ' if comment and not comment.startswith(' ') else '')}{comment}{newline}"
                        )
                        changed = True
                    else:
                        out.append(raw)
                else:
                    out.append(raw)
        else:
            out.append(raw)
    return out, changed


def apply_upgrades_to_pubspec(ctx: Context, upgrades: list[UpgradeItem]) -> tuple[bool, list[str], list[str]]:
    """
    按计划升级 pubspec.yaml：
    - 仅在三个 section 中按 block 替换
    - 保留所有非目标文本（注释/空行/缩进/顺序）
    返回：(changed, summary_lines, errors)
      errors：发现无法替换的包（按你的要求：有问题就抛出）
    """
    lines = read_text(ctx.pubspec_path).splitlines(keepends=True)
    upgrade_map = {u.name: u for u in upgrades}

    new_lines: list[str] = []
    changed = False
    summary_lines: list[str] = []
    errors: list[str] = []

    in_section = False
    current_block: list[str] = []
    current_dep: Optional[str] = None
    current_section: Optional[str] = None
    current_dep_indent: Optional[int] = None

    def flush_block():
        nonlocal current_block, current_dep, changed
        if not current_block:
            return
        if current_dep and current_section and current_dep in upgrade_map:
            u = upgrade_map[current_dep]
            if u.section != current_section:
                new_lines.extend(current_block)
            else:
                out, ch = _apply_version_in_block(current_block, u.target)
                new_lines.extend(out)
                if ch:
                    changed = True
                    summary_lines.append(f"{u.name}: {u.current} -> {u.target}")
        else:
            new_lines.extend(current_block)
        current_block = []
        current_dep = None

    i = 0
    while i < len(lines):
        raw = lines[i]
        header = _is_section_header(raw)
        if header:
            flush_block()
            in_section = True
            current_section = header
            current_dep_indent = None
            new_lines.append(raw)
            i += 1
            continue

        if in_section:
            # section 结束：遇到非空行且缩进为 0 且不是注释/空行
            if raw.strip() and _block_indent(raw) == 0 and not raw.lstrip().startswith("#"):
                flush_block()
                in_section = False
                current_section = None
                current_dep_indent = None
                new_lines.append(raw)
                i += 1
                continue

            # 识别一个依赖的开始：形如 "  foo:"（注意缩进）
            m = re.match(r"^(\s*)([A-Za-z0-9_]+)\s*:\s*(.*)$", raw.rstrip("\n\r"))
            if m and m.group(2) != "version":
                indent = m.group(1)
                dep = m.group(2)
                if current_dep is None:
                    current_dep = dep
                    current_dep_indent = len(indent)
                    current_block = [raw]
                else:
                    this_indent = len(indent)
                    if raw.strip() and this_indent <= (current_dep_indent or 0):
                        flush_block()
                        current_dep = dep
                        current_dep_indent = this_indent
                        current_block = [raw]
                    else:
                        current_block.append(raw)
                i += 1
                continue

            # block 内部或空行/注释
            if current_dep is not None:
                current_block.append(raw)
            else:
                new_lines.append(raw)
            i += 1
            continue

        # 不在 section
        new_lines.append(raw)
        i += 1

    flush_block()

    # 校验：目标包必须都能被处理到（按你的要求：定位不到就报错）
    for u in upgrades:
        if any(s.startswith(f"{u.name}: ") for s in summary_lines):
            continue
        errors.append(f"未能在 pubspec.yaml 中定位并替换依赖：{u.name}（section={u.section}）")

    if changed:
        write_text_atomic(ctx.pubspec_path, "".join(new_lines))

    return changed, summary_lines, errors


# =======================
# Private deps plan builder
# =======================
def _is_private_dep(dep_json: dict, private_host_keywords: tuple[str, ...]) -> bool:
    """
    只要 hosted/url 存在就算私有；如果配置了关键词，则需要命中关键词才算
    """
    hosted = dep_json.get("hosted", {})
    url = hosted.get("url") if isinstance(hosted, dict) else None
    if not url:
        return False
    if not private_host_keywords:
        return True
    return any(k in url for k in private_host_keywords)


def _parse_pub_outdated(ctx: Context) -> dict:
    return flutter_pub_outdated_json(ctx)


def _extract_latest_version(item: dict) -> Optional[str]:
    """
    latest.version 优先；否则 fallback 到 resolvable.version
    """
    latest = item.get("latest")
    if isinstance(latest, dict):
        v = latest.get("version")
        if isinstance(v, str) and is_valid_version(v):
            return v
    res = item.get("resolvable")
    if isinstance(res, dict):
        v = res.get("version")
        if isinstance(v, str) and is_valid_version(v):
            return v
    return None


def _read_current_constraint_from_pubspec_block(block_lines: list[str]) -> Optional[str]:
    """
    从一个依赖 block 中读出当前约束（尽量不“猜”）：
      - foo: ^1.2.3     -> ^1.2.3
      - foo:
          version: ^1.2.3 -> ^1.2.3
    找不到则返回 None
    """
    # case 1: foo: <token>
    m = re.match(r"^\s*[A-Za-z0-9_]+\s*:\s*([^\s#]+)\s*(?:#.*)?$", block_lines[0].rstrip("\n\r"))
    if m:
        tok = m.group(1)
        # 如果是 map block（例如 hosted/path/git），tok 可能是空/或看起来不是版本
        if re.match(r"^[\^<>=~]?\d+\.\d+(\.\d+)?", tok):
            return tok

    # case 2: version: <token> in subsequent lines
    for raw in block_lines[1:]:
        m2 = re.match(r"^\s*version\s*:\s*([^\s#]+)\s*(?:#.*)?$", raw.rstrip("\n\r"))
        if m2:
            tok = m2.group(1)
            if re.match(r"^[\^<>=~]?\d+\.\d+(\.\d+)?", tok):
                return tok
            return tok  # 即便不是简单数字（例如 range），也原样返回，交给替换逻辑做最小改动
    return None


def _collect_dep_blocks_with_sections(pubspec_text: str) -> dict[str, dict]:
    """
    扫描 pubspec.yaml，收集三个 section 中每个 direct 依赖的 block 原文（保留行）
    返回：{dep_name: {"section": section, "lines": [..block..]}}
    """
    lines = pubspec_text.splitlines(keepends=True)
    result: dict[str, dict] = {}

    in_section = False
    current_section: Optional[str] = None
    current_dep: Optional[str] = None
    current_dep_indent: Optional[int] = None
    current_block: list[str] = []

    def flush():
        nonlocal current_dep, current_block
        if current_dep and current_section and current_block:
            result[current_dep] = {"section": current_section, "lines": current_block[:] }
        current_dep = None
        current_block = []

    i = 0
    while i < len(lines):
        raw = lines[i]
        header = _is_section_header(raw)
        if header:
            flush()
            in_section = True
            current_section = header
            current_dep_indent = None
            i += 1
            continue

        if in_section:
            # section end
            if raw.strip() and _block_indent(raw) == 0 and not raw.lstrip().startswith("#"):
                flush()
                in_section = False
                current_section = None
                current_dep_indent = None
                i += 1
                continue

            m = re.match(r"^(\s*)([A-Za-z0-9_]+)\s*:\s*(.*)$", raw.rstrip("\n\r"))
            if m and m.group(2) != "version":
                indent = m.group(1)
                dep = m.group(2)

                if current_dep is None:
                    current_dep = dep
                    current_dep_indent = len(indent)
                    current_block = [raw]
                else:
                    this_indent = len(indent)
                    if raw.strip() and this_indent <= (current_dep_indent or 0):
                        flush()
                        current_dep = dep
                        current_dep_indent = this_indent
                        current_block = [raw]
                    else:
                        current_block.append(raw)
                i += 1
                continue

            if current_dep is not None:
                current_block.append(raw)

            i += 1
            continue

        i += 1

    flush()
    return result


def build_private_upgrade_plan(
    ctx: Context,
    private_host_keywords: tuple[str, ...],
    skip_packages: set[str],
) -> list[UpgradeItem]:
    """
    私有依赖升级策略（优化版）：
      - 对所有私有 hosted（hosted.url 存在）direct/dev/override 依赖：
          统一把 pubspec 中的版本下界 bump 到 latest（写成 ^latest）
      - 不管 lockfile 是否已经解析到新版本
      - 只改 pubspec.yaml 的约束声明
      - transitive 不处理
      - dev/overrides 默认关闭（可通过开关打开）
    """
    pubspec_text = read_text(ctx.pubspec_path)
    blocks = _collect_dep_blocks_with_sections(pubspec_text)

    data = _parse_pub_outdated(ctx)
    pkgs = data.get("packages") or []

    plan: list[UpgradeItem] = []
    for pkg in pkgs:
        name = pkg.get("package")
        if not isinstance(name, str) or not name:
            continue
        if name in skip_packages:
            continue

        kind = pkg.get("kind")  # direct/dev/override/transitive
        if kind == "transitive":
            continue

        dep = pkg.get("dependency") or {}
        if not isinstance(dep, dict):
            continue

        if not _is_private_dep(dep, private_host_keywords):
            continue

        # section gating
        section = "dependencies"
        if kind == "dev":
            section = "dev_dependencies"
            if not UPGRADE_DEV_DEPENDENCIES:
                continue
        if kind == "override":
            section = "dependency_overrides"
            if not UPGRADE_DEPENDENCY_OVERRIDES:
                continue

        # 必须能在 pubspec 中定位到 block，否则按“有问题就抛出”的要求交给后续 errors
        b = blocks.get(name)
        current_constraint = None
        if b and b.get("section") == section:
            current_constraint = _read_current_constraint_from_pubspec_block(b.get("lines") or [])

        latest_v = _extract_latest_version(pkg)
        if not latest_v:
            continue

        target_constraint = f"^{latest_v}"

        # 如果已经是目标值，就不入 plan
        if current_constraint == target_constraint:
            continue

        plan.append(
            UpgradeItem(
                name=name,
                current=current_constraint or "(unknown)",
                target=target_constraint,
                section=section,
            )
        )

    plan.sort(key=lambda x: x.name.lower())
    return plan



# =======================
# Release branch version guard / bump
# =======================
_VERSION_HEADER_RE = re.compile(r"^(\s*version\s*:\s*)([^\s#]+)(\s*(#.*)?)$")


def bump_pubspec_version_if_release(ctx: Context) -> tuple[bool, str, str]:
    """在 release/* 分支上做 version 守门/自动 bump。
    规则（尽量简单、可预期）：
      - 如果是 x.y.z+N：只 bump build（N+1）
      - 否则如果是 x.y.z：bump patch（z+1）
    返回：(changed, old, new)。非 release 分支返回 (False, '', '')。
    """
    branch = git_current_branch(ctx)
    if not branch.startswith("release/"):
        return False, "", ""

    text = read_text(ctx.pubspec_path)
    lines = text.splitlines(keepends=True)
    changed = False
    old_v = ""
    new_v = ""

    for idx, raw in enumerate(lines):
        m = _VERSION_HEADER_RE.match(raw.rstrip("\n\r"))
        if not m:
            continue
        prefix, ver_token, suffix = m.group(1), m.group(2), m.group(3) or ""
        old_v = ver_token

        m2 = re.match(r"^(\d+)\.(\d+)\.(\d+)(?:\+(\d+))?$", ver_token.strip())
        if not m2:
            raise RuntimeError(f"release 分支的 pubspec version 不符合预期格式：{ver_token}")

        major, minor, patch = int(m2.group(1)), int(m2.group(2)), int(m2.group(3))
        build = m2.group(4)

        if build is not None:
            new_v = f"{major}.{minor}.{patch}+{int(build) + 1}"
        else:
            new_v = f"{major}.{minor}.{patch + 1}"

        if new_v != old_v:
            newline = "\n"
            if raw.endswith("\r\n"):
                newline = "\r\n"
            lines[idx] = f"{prefix}{new_v}{suffix}{newline}"
            changed = True
        break

    if not old_v:
        raise RuntimeError("release 分支守门失败：未在 pubspec.yaml 中找到顶层 version: 行")

    if changed:
        write_text_atomic(ctx.pubspec_path, "".join(lines))
    return changed, old_v, new_v

# =======================
# Entry
# =======================
def run(ctx: Context) -> int:
    # 默认：不过滤域名（任何 hosted+url 都算私有），默认 skip
    private_host_keywords: tuple[str, ...] = tuple()
    skip_packages: set[str] = {"ap_recaptcha"}

    total_t0 = time.perf_counter()
    try:
        with step_scope(ctx, 0, "环境检查（git 仓库）", "检查 git 仓库..."):
            _git_check_repo(ctx)

        with step_scope(ctx, 1, "检查是否有未提交变更", "检查工作区状态..."):
            if _git_is_dirty(ctx) and not ctx.yes:
                raise RuntimeError("检测到未提交变更。请先 commit/stash，或使用 --yes 强制继续。")
            ctx.echo("✅ 工作区状态可用于升级")

        with step_scope(ctx, 2, "同步远端（git pull --ff-only）", "正在同步远端..."):
            git_pull_ff_only(ctx)
            ctx.echo("✅ 已同步远端（ff-only）")

        with step_scope(ctx, 3, "执行 flutter pub get（预检查）", "正在执行 pub get..."):
            flutter_pub_get(ctx, with_loading=False)
            ctx.echo("✅ pub get 通过")

        with step_scope(
            ctx,
            4,
            f"分析待升级私有依赖（dev={UPGRADE_DEV_DEPENDENCIES}, overrides={UPGRADE_DEPENDENCY_OVERRIDES}）",
            "分析待升级依赖...",
        ):
            plan = build_private_upgrade_plan(
                ctx=ctx,
                private_host_keywords=private_host_keywords,
                skip_packages=skip_packages,
            )

        if not plan:
            ctx.echo("ℹ️ 未发现需要升级的私有依赖。")
            return 0

        ctx.echo("将升级以下私有依赖（仅更新 pubspec.yaml 的版本号；若原来带 ^ 则保留 ^）：")
        for u in plan:
            ctx.echo(f"  - {u.name}: {u.current} -> {u.target}（section={u.section}）")

        with step_scope(ctx, 5, "执行依赖升级（写入 pubspec.yaml，保留注释与结构）", "写入 pubspec.yaml..."):
            changed, summary, errors = apply_upgrades_to_pubspec(ctx, plan)
            if errors:
                raise RuntimeError("pubspec 依赖替换失败：\n" + "\n".join(errors))
            if not changed:
                ctx.echo("ℹ️ 未发生实际修改。")
                return 0

            ctx.echo("✅ pubspec.yaml 已更新：")
            for s in summary:
                ctx.echo(f"  - {s}")

        with step_scope(ctx, 6, "release 分支守门：自动 bump pubspec version", "检查/更新 pubspec version..."):
            v_changed, old_v, new_v = bump_pubspec_version_if_release(ctx)
            if v_changed:
                summary.append(f"pubspec version: {old_v} -> {new_v}")
                ctx.echo(f"✅ 已 bump version：{old_v} -> {new_v}")
            else:
                ctx.echo("ℹ️ 非 release/* 分支，跳过 version 守门")

        with step_scope(ctx, 7, "执行 flutter pub get（升级后）", "正在执行 pub get..."):
            flutter_pub_get(ctx, with_loading=False)
            ctx.echo("✅ pub get 通过")

        with step_scope(ctx, 8, "执行 flutter analyze", "正在执行 analyze..."):
            flutter_analyze(ctx, with_loading=False)
            ctx.echo("✅ flutter analyze 通过")

        with step_scope(ctx, 9, "自动提交（git add + git commit）", "正在提交代码..."):
            git_add_commit(ctx, summary, subject="chore(pub): upgrade private hosted deps")
            ctx.echo("✅ 已提交")

        with step_scope(ctx, 10, "自动推送（git push）", "正在推送..."):
            git_push(ctx)
            ctx.echo("✅ 已推送")

        total_dt = time.perf_counter() - total_t0
        ctx.echo(f"\n🎉 全流程完成，总耗时 {total_dt:.2f}s")
        return 0

    except KeyboardInterrupt:
        ctx.echo("\n⛔️ 用户中断")
        return 130
    except Exception as e:
        ctx.echo(f"\n❌ 失败：{e}")
        return 1
