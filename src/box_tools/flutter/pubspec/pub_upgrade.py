from __future__ import annotations

import json
import re
import shutil
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
UPGRADE_DEPENDENCY_OVERRIDES = True     # 是否升级 dependency_overrides（默认开启/关闭你自己定）

# =======================
# Data
# =======================
@dataclass(frozen=True)
class UpgradeItem:
    name: str
    current: str
    target: str
    picked_from: str  # latest/resolvable/upgradable

@dataclass(frozen=True)
class AnalyzeReport:
    errors: list[str]
    warnings: list[str]
    infos: list[str]
    raw: str



# =======================
# Step helpers
# =======================
def _step(ctx: Context, n: int, title: str) -> None:
    ctx.echo(f"\n[{n}] {title}")

def _format_duration(seconds: float) -> str:
    if seconds < 1:
        return f"{seconds*1000:.0f}ms"
    if seconds < 60:
        return f"{seconds:.1f}s"
    m = int(seconds // 60)
    s = seconds - m * 60
    return f"{m}m{s:.0f}s"


@contextmanager
def step_scope(ctx: Context, n: int, title: str, loading_label: str | None = None):
    """
    每个步骤统一：
    - 打印步骤标题
    - 每一步前面显示 loading（带实时计时器）
    - 步骤结束输出：done + cost
    """
    _step(ctx, n, title)
    label = loading_label or title

    t0 = time.perf_counter()
    stop_event = threading.Event()
    th = threading.Thread(target=_loading_animation, args=(stop_event, label, t0))
    th.daemon = True
    th.start()

    try:
        yield
    finally:
        stop_event.set()
        th.join()
        _clear_line()
        cost = time.perf_counter() - t0
        ctx.echo(f"[{n}] done  (cost: {cost:.2f}s)")


def _ask_continue(ctx: Context, prompt: str) -> bool:
    """
    返回 True 表示“中断”，False 表示“继续”
    - ctx.yes：默认不询问，继续（不中断）
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


def git_add_commit(ctx: Context, summary_lines: list[str]) -> None:
    if not summary_lines:
        return

    subject = "up deps"
    body = "\n".join(summary_lines)
    msg = subject + "\n\n" + body

    paths = ["pubspec.yaml"]
    if (ctx.project_root / "pubspec.lock").exists():
        paths.append("pubspec.lock")

    r = run_cmd(["git", "add", *paths], cwd=ctx.project_root, capture=True)
    if r.code != 0:
        raise RuntimeError(f"git add 失败：{(r.err or r.out).strip()}")

    r = run_cmd(["git", "commit", "-m", msg], cwd=ctx.project_root, capture=True)
    if r.code != 0:
        raise RuntimeError(f"git commit 失败：{(r.err or r.out).strip()}")

    # --- 新增：自动 push ---
    branch = _git_current_branch(ctx)

    # 没有远程分支：按你 pull 的逻辑，直接跳过 push（不算失败）
    if not _git_has_remote_branch(ctx, branch):
        ctx.echo("⚠️ 当前分支没有远程分支，跳过 git push。")
        return

    r = run_cmd(["git", "push"], cwd=ctx.project_root, capture=True)
    if r.code != 0:
        raise RuntimeError(f"git push 失败：{(r.err or r.out).strip()}")

# =======================
# Version utils
# =======================
def is_valid_version(version) -> bool:
    if not isinstance(version, str):
        return False
    v = version.strip()
    # 允许：^1.2.3、1.2.3、1.2.3+build、1.2.3-pre、1.2.3-pre+build
    return bool(
        re.fullmatch(r"^\^?[0-9]+(?:\.[0-9]+)*(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$", v)
    )


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


def read_pubspec_app_version(pubspec_text: str) -> Optional[str]:
    """
    读取 pubspec.yaml 顶层 version: 字段（容忍前导空格）
    """
    for raw in pubspec_text.splitlines():
        m = re.match(
            r"^\s*version:\s*([0-9]+(?:\.[0-9]+){1,3}(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?)\s*$",
            raw,
        )
        if m:
            v = m.group(1).strip()
            return v if is_valid_version(v) else None
    return None


def upper_bound_of_minor(app_version: str) -> Optional[str]:
    """
    app_version=3.45.1 或 3.45.0+xxx -> upper=3.46.0
    依赖目标必须满足 < upper（exclusive）
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


# =======================
# outdated json
# =======================
def _load_outdated(ctx: Context) -> dict:
    if ctx.outdated_json_path:
        return json.loads(read_text(ctx.outdated_json_path))
    return flutter_pub_outdated_json(ctx)


def get_outdated_map(ctx: Context) -> dict[str, dict[str, str]]:
    """
    返回：
      {
        "pkg": {"current": "...", "upgradable": "...", "resolvable": "...", "latest": "..."}
      }
    """
    data = _load_outdated(ctx)

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
# pubspec block parsing (text-level, keep structure)
# =======================
def _build_section_re() -> re.Pattern:
    sections = ["dependencies"]
    if UPGRADE_DEV_DEPENDENCIES:
        sections.append("dev_dependencies")
    if UPGRADE_DEPENDENCY_OVERRIDES:
        sections.append("dependency_overrides")
    # e.g. ^(dependencies|dev_dependencies|dependency_overrides):\s*$
    return re.compile(r"^(" + "|".join(sections) + r"):\s*$")


_SECTION_RE = _build_section_re()


_DEP_START_RE = re.compile(r"^ {2}(\S+):")  # 2-space indent dependency start


def _extract_dependency_blocks(lines: list[str]) -> list[tuple[str, list[str], str]]:
    """
    提取 dependencies / dev_dependencies / dependency_overrides 三个 section 下的“每个依赖块”
    返回：(section, block_lines, dep_name)
    - 保留换行符（keepends）
    - 不解析 YAML，仅用缩进与结构识别块边界
    """
    blocks: list[tuple[str, list[str], str]] = []
    in_section = False
    section = ""
    block: list[str] = []

    def flush():
        nonlocal block
        if not block:
            return
        m = _DEP_START_RE.match(block[0])
        if m:
            blocks.append((section, block[:], m.group(1)))
        block = []

    for line in lines:
        msec = _SECTION_RE.match(line)
        if msec:
            flush()
            in_section = True
            section = msec.group(1)
            continue

        if not in_section:
            continue

        # section 结束：遇到非空且不以两个空格缩进的行
        if line.strip() != "" and not line.startswith("  "):
            flush()
            in_section = False
            section = ""
            continue

        # 新依赖块开始
        if _DEP_START_RE.match(line):
            flush()
            block.append(line)
            continue

        if block:
            block.append(line)

    flush()
    return blocks


def _private_hosted_url(block: list[str]) -> Optional[str]:
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
    - 只要 hosted + url，就认为是“私有 hosted/url 依赖”
    - 若用户传了关键词，则需 url 命中任一关键词
    """
    url = _private_hosted_url(block)
    if not url:
        return False
    if not private_host_keywords:
        return True
    return any(kw in url for kw in private_host_keywords if kw)


# =======================
# Target selection (prefer latest)
# =======================
def _pick_target_with_upper(info: dict[str, str], upper: str) -> tuple[Optional[str], str]:
    """
    upper 存在时（不跨 next minor）：
      1) latest < upper -> latest
      2) resolvable < upper -> resolvable
      3) upgradable < upper -> upgradable
    """
    lat = info.get("latest", "")
    res = info.get("resolvable", "")
    upg = info.get("upgradable", "")

    if lat and is_valid_version(lat) and version_lt(lat, upper):
        return lat, "latest"
    if res and is_valid_version(res) and version_lt(res, upper):
        return res, "resolvable"
    if upg and is_valid_version(upg) and version_lt(upg, upper):
        return upg, "upgradable"
    return None, ""


def _pick_target_without_upper(info: dict[str, str], current: str) -> tuple[Optional[str], str]:
    """
    upper 不存在时：退化策略（不升 major），并仍优先 latest -> resolvable -> upgradable
    """
    cur_major = major_of(current)
    for key in ("latest", "resolvable", "upgradable"):
        v = info.get(key, "")
        if not v or not is_valid_version(v):
            continue
        if major_of(v) <= cur_major:
            return v, key
    return None, ""


def build_private_upgrade_plan(
        *,
        ctx: Context,
        private_host_keywords: tuple[str, ...],
        skip_packages: set[str],
) -> list[UpgradeItem]:
    """
    只升级“私有 hosted/url 依赖”
    - upper 存在：只允许 target < upper（允许从低 minor 升到项目 minor 内）
    - upper 不存在：退化为不升 major
    - 目标版本优先 latest.version（不满足上限则回退）
    """
    pubspec_text = read_text(ctx.pubspec_path)
    app_version = read_pubspec_app_version(pubspec_text)
    upper = upper_bound_of_minor(app_version) if app_version else None

    lines = pubspec_text.splitlines(keepends=True)
    blocks = _extract_dependency_blocks(lines)

    pubspec_deps: set[str] = set()
    private_deps: set[str] = set()

    for _section, block, dep_name in blocks:
        pubspec_deps.add(dep_name)
        if _is_private_hosted_dep(block, private_host_keywords):
            private_deps.add(dep_name)

    outdated = get_outdated_map(ctx)

    plan: list[UpgradeItem] = []
    for name, info in outdated.items():
        if name in skip_packages:
            continue
        if name not in pubspec_deps:
            continue
        if name not in private_deps:
            continue

        cur = info.get("current", "")
        if not cur or not is_valid_version(cur):
            continue

        if upper:
            target, src = _pick_target_with_upper(info, upper)
        else:
            target, src = _pick_target_without_upper(info, cur)

        if not target:
            continue

        if compare_versions(cur, target) >= 0:
            continue

        plan.append(UpgradeItem(name=name, current=cur, target=target, picked_from=src))

    plan.sort(key=lambda x: x.name)
    return plan


# =======================
# Apply (text-level minimal replacement)
# =======================
_INLINE_DEP_RE = re.compile(r"^(?P<prefix>\s{2}\S+:\s*)(?P<ver>\S+)(?P<suffix>.*)$")
_VERSION_LINE_RE = re.compile(r"^(?P<prefix>\s*version:\s*)(?P<ver>\S+)(?P<suffix>.*)$")


def _apply_version_in_block(block: list[str], new_version: str) -> tuple[list[str], Optional[str], Optional[str], str]:
    """
    只改 block 内版本 token，保留原注释/空格/结构。
    返回：(new_block, old_version, written_version, mode)
      mode: inline | version_line | none
    """
    if not block:
        return block, None, None, "none"

    m_inline = _INLINE_DEP_RE.match(block[0].rstrip("\n"))
    if m_inline:
        oldv = m_inline.group("ver").strip()
        keep_caret = oldv.startswith("^")
        nv = f"^{new_version}" if keep_caret else new_version
        b2 = block[:]
        b2[0] = f"{m_inline.group('prefix')}{nv}{m_inline.group('suffix')}\n"
        return b2, oldv, nv, "inline"

    idx = -1
    for i, line in enumerate(block):
        if _VERSION_LINE_RE.match(line.rstrip("\n")):
            idx = i
            break
    if idx == -1:
        return block, None, None, "none"

    m_ver = _VERSION_LINE_RE.match(block[idx].rstrip("\n"))
    if not m_ver:
        return block, None, None, "none"

    oldv = m_ver.group("ver").strip()
    keep_caret = oldv.startswith("^")
    nv = f"^{new_version}" if keep_caret else new_version

    b2 = block[:]
    b2[idx] = f"{m_ver.group('prefix')}{nv}{m_ver.group('suffix')}\n"
    return b2, oldv, nv, "version_line"


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

    def flush_block():
        nonlocal current_block, current_dep, changed
        if not current_block:
            return

        dep = current_dep
        if dep and dep in upgrade_map:
            u = upgrade_map[dep]
            ctx.echo(f"  • 处理 {dep} ...")

            target_core = _strip_meta(u.target)  # 写入时去掉 + / - 元信息

            b2, oldv, written, mode = _apply_version_in_block(current_block, target_core)
            new_lines.extend(b2)

            if not oldv or not written or mode == "none":
                errors.append(f"{dep}: 找不到可替换的版本位置（既非单行也无 version: 行）")
            else:
                if compare_versions(_strip_meta(oldv), _strip_meta(written)) < 0:
                    changed = True
                    summary_lines.append(f"🔄 {dep}: {oldv} → {written}")
                    ctx.echo(f"    ✅ {dep} 已写入")
        else:
            new_lines.extend(current_block)

        current_block.clear()
        current_dep = None

    for line in lines:
        msec = _SECTION_RE.match(line)
        if msec:
            flush_block()
            in_section = True
            new_lines.append(line)
            continue

        if not in_section:
            new_lines.append(line)
            continue

        if line.strip() != "" and not line.startswith("  "):
            flush_block()
            in_section = False
            new_lines.append(line)
            continue

        mdep = _DEP_START_RE.match(line)
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

    if errors:
        return False, summary_lines, errors

    if not changed:
        return False, summary_lines, []

    if ctx.dry_run:
        return True, summary_lines, []

    write_text_atomic(ctx.pubspec_path, "".join(new_lines))
    return True, summary_lines, []


# =======================
# pub get / analyze
# =======================
def _clear_line():
    print("\r\033[2K", end="", flush=True)


def _loading_animation(stop_event: threading.Event, label: str, t0: float) -> None:
    """loading 动画 + 实时耗时（秒）"""
    spinner = cycle(["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"])
    while not stop_event.is_set():
        elapsed = time.perf_counter() - t0
        print(f"{next(spinner)} {label}  (elapsed: {elapsed:6.1f}s) ", end="", flush=True)
        time.sleep(0.1)
        _clear_line()


def flutter_pub_get(ctx: Context, with_loading: bool = False) -> None:
    cmd = None
    if shutil.which("flutter"):
        cmd = ["flutter", "pub", "get"]
    elif shutil.which("dart"):
        cmd = ["dart", "pub", "get"]
    else:
        raise RuntimeError("未找到 flutter/dart 命令，无法执行 pub get")


    stop_event: threading.Event | None = None
    th: threading.Thread | None = None
    if with_loading:
        t0 = time.perf_counter()
        stop_event = threading.Event()
        th = threading.Thread(target=_loading_animation, args=(stop_event, "正在执行 pub get...", t0))
        th.daemon = True
        th.start()
    try:
        r = run_cmd(cmd, cwd=ctx.project_root, capture=True)
        if r.code != 0:
            raise RuntimeError((r.err or r.out).strip() or "pub get 失败")
    finally:
        if stop_event is not None and th is not None:
            stop_event.set()
            th.join()
            _clear_line()


# =======================
# analyze parsing
# =======================
_ANALYZE_ISSUE_RE = re.compile(
    r"^(?P<level>info|warning|error)\s*(?:•|\-)\s*(?P<msg>.+)$",
    flags=re.IGNORECASE,
)


def _parse_flutter_analyze_output(raw: str) -> AnalyzeReport:
    """
    解析 flutter analyze 输出，把 issue 分为 info / warning / error。
    常见格式示例：
      info • Unused import • lib/a.dart:1:1 • ...
      warning • ... • ...
      error • ... • ...
    也兼容 "info - xxx" 这种。
    """
    errors: list[str] = []
    warnings: list[str] = []
    infos: list[str] = []

    for line in (raw or "").splitlines():
        s = line.strip()
        if not s:
            continue
        m = _ANALYZE_ISSUE_RE.match(s)
        if not m:
            continue
        lvl = (m.group("level") or "").lower()
        if lvl == "error":
            errors.append(s)
        elif lvl == "warning":
            warnings.append(s)
        elif lvl == "info":
            infos.append(s)

    return AnalyzeReport(errors=errors, warnings=warnings, infos=infos, raw=raw or "")


def flutter_analyze(ctx: Context) -> AnalyzeReport:
    """
    规则（按你的需求）：
    - 只有 info：打印总数 + 列 2~3 条示例，允许继续
    - 有 warning：warning 全列出来，允许继续
    - 有 error：error 列出来并中断（抛异常）
    注意：flutter analyze 在「只有 info/warning」时也可能返回非 0，
    所以不能仅靠返回码判断。
    """
    if shutil.which("flutter") is None:
        raise RuntimeError("未找到 flutter 命令，无法执行 flutter analyze")

    r = run_cmd(["flutter", "analyze"], cwd=ctx.project_root, capture=True)
    raw = (r.out or "") + ("\n" + r.err if r.err else "")
    rep = _parse_flutter_analyze_output(raw)

    # ---- 按级别输出 ----
    if rep.errors:
        ctx.echo(f"❌ flutter analyze: {len(rep.errors)} error(s)")
        # 全列出来（数量太多时做保护性截断）
        max_lines = 50
        for i, line in enumerate(rep.errors[:max_lines], 1):
            ctx.echo(f"  {i}. {line}")
        if len(rep.errors) > max_lines:
            ctx.echo(f"  ...（已截断，剩余 {len(rep.errors) - max_lines} 条）")
        raise RuntimeError("flutter analyze 存在 error，已中断。")

    if rep.warnings:
        ctx.echo(f"⚠️ flutter analyze: {len(rep.warnings)} warning(s)")
        max_lines = 200
        for i, line in enumerate(rep.warnings[:max_lines], 1):
            ctx.echo(f"  {i}. {line}")
        if len(rep.warnings) > max_lines:
            ctx.echo(f"  ...（已截断，剩余 {len(rep.warnings) - max_lines} 条）")

    if rep.infos:
        ctx.echo(f"ℹ️ flutter analyze: {len(rep.infos)} info(s)")
        # 只列 2~3 条即可
        show_n = 3 if len(rep.infos) >= 3 else len(rep.infos)
        for i, line in enumerate(rep.infos[:show_n], 1):
            ctx.echo(f"  {i}. {line}")

    # 既没有 issue，也当通过
    return rep

# =======================
# Entry: default APPLY with full steps
# =======================
def run(ctx: Context) -> int:
    # 默认：不过滤域名（任何 hosted+url 都算私有），默认 skip
    private_host_keywords: tuple[str, ...] = tuple()
    skip_packages: set[str] = {"ap_recaptcha"}

    with step_scope(ctx, 0, "环境检查（git 仓库）", "检查 git 仓库..."):
        _git_check_repo(ctx)

    with step_scope(ctx, 1, "检查是否有未提交变更", "检查工作区状态..."):
        if _git_is_dirty(ctx):
            ctx.echo("⚠️ 检测到未提交变更（working tree dirty）。")
            if _ask_continue(ctx, "是否中断本次升级？"):
                ctx.echo("已中断。")
                return 0
            ctx.echo("继续执行（注意：可能把无关变更一起 commit，建议先处理干净）。")
        else:
            ctx.echo("✅ 工作区干净")

    with step_scope(ctx, 2, "拉取最新代码（git pull --ff-only）", "拉取远程更新..."):
        _git_pull_ff_only(ctx)

    with step_scope(ctx, 3, "执行 flutter pub get（预检查）", "正在执行 pub get..."):
        try:
            flutter_pub_get(ctx, with_loading=False)
            ctx.echo("✅ pub get 通过")
        except Exception as e:
            ctx.echo(f"❌ pub get 失败：{e}")
            if _ask_continue(ctx, "是否中断本次升级？"):
                return 1
            ctx.echo("选择继续执行（不推荐）。")

    with step_scope(
            ctx,
            4,
            f"分析待升级私有依赖（优先 latest.version；dev={UPGRADE_DEV_DEPENDENCIES}, overrides={UPGRADE_DEPENDENCY_OVERRIDES})",
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

    ctx.echo("将升级以下私有依赖：")
    for u in plan:
        ctx.echo(f"  - {u.name}: {u.current} -> {u.target}")

    with step_scope(ctx, 5, "执行依赖升级（写入 pubspec.yaml，保留注释与结构）", "写入 pubspec.yaml..."):
        changed, summary, errors = apply_upgrades_to_pubspec(ctx, plan)

        if errors:
            raise RuntimeError("升级过程中出现不可处理项：\n" + "\n".join(errors))

        if not changed:
            ctx.echo("ℹ️ 没有发生实际修改。")
            return 0

        ctx.echo("✅ pubspec.yaml 已更新：")
        for s in summary:
            ctx.echo(f"  {s}")

    if ctx.dry_run:
        ctx.echo("（dry-run）不执行后续 pub get / analyze / git commit。")
        return 0

    with step_scope(ctx, 6, "执行 flutter pub get（升级后）", "正在执行 pub get..."):
        flutter_pub_get(ctx, with_loading=False)
        ctx.echo("✅ pub get 完成")

    with step_scope(ctx, 7, "执行 flutter analyze", "正在执行 flutter analyze..."):
        flutter_analyze(ctx)
        ctx.echo("✅ flutter analyze 完成（info/warning 可继续，error 会中断）")

    with step_scope(ctx, 8, "自动提交（git add + git commit）", "正在提交代码..."):
        git_add_commit(ctx, summary)
        ctx.echo("✅ 已自动提交完成")

    return 0
