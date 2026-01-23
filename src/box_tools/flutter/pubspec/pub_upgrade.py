from __future__ import annotations

import json
import re
import shutil
import threading
import time
from dataclasses import dataclass
from itertools import cycle
from typing import Optional

from .tool import Context, read_text, write_text_atomic, run_cmd, flutter_pub_outdated_json


# =======================
# Data
# =======================
@dataclass(frozen=True)
class UpgradeItem:
    name: str
    current: str
    target: str
    picked_from: str  # latest/resolvable/upgradable


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
_SECTION_RE = re.compile(r"^(dependencies|dev_dependencies|dependency_overrides):\s*$")
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
# Target selection (PRD rules: prefer latest)
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
# 单行依赖：  "  pkg: ^1.2.3   # comment"
_INLINE_DEP_RE = re.compile(r"^(?P<prefix>\s{2}\S+:\s*)(?P<ver>\S+)(?P<suffix>.*)$")
# 多行块 version： "  version: 1.2.3  # comment"（缩进不固定）
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
            target_core = _strip_meta(u.target)  # 写入时去掉 + / - 元信息

            b2, oldv, written, mode = _apply_version_in_block(current_block, target_core)
            new_lines.extend(b2)

            if not oldv or not written or mode == "none":
                errors.append(f"{dep}: 找不到可替换的版本位置（既非单行也无 version: 行）")
            else:
                if compare_versions(_strip_meta(oldv), _strip_meta(written)) < 0:
                    changed = True
                    summary_lines.append(f"🔄 {dep}: {oldv} → {written}")
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
        # dry-run：不写入，但认为“有变更可做”
        return True, summary_lines, []

    write_text_atomic(ctx.pubspec_path, "".join(new_lines))
    return True, summary_lines, []


# =======================
# pub get / analyze / git commit
# =======================
def _clear_line():
    print("\r\033[2K", end="", flush=True)


def _loading_animation(stop_event: threading.Event, label: str):
    spinner = cycle(["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"])
    while not stop_event.is_set():
        print(f"\r{next(spinner)} {label} ", end="", flush=True)
        time.sleep(0.1)
    _clear_line()


def flutter_pub_get(ctx: Context) -> None:
    cmd = None
    if shutil.which("flutter"):
        cmd = ["flutter", "pub", "get"]
    elif shutil.which("dart"):
        cmd = ["dart", "pub", "get"]
    else:
        raise RuntimeError("未找到 flutter/dart 命令，无法执行 pub get")

    stop_event = threading.Event()
    t = threading.Thread(target=_loading_animation, args=(stop_event, "正在执行 pub get..."))
    t.start()
    try:
        r = run_cmd(cmd, cwd=ctx.project_root, capture=True)
        if r.code != 0:
            raise RuntimeError((r.err or r.out).strip() or "pub get 失败")
    finally:
        stop_event.set()
        t.join()
        _clear_line()


def flutter_analyze(ctx: Context) -> None:
    if shutil.which("flutter") is None:
        raise RuntimeError("未找到 flutter 命令，无法执行 flutter analyze")

    r = run_cmd(["flutter", "analyze"], cwd=ctx.project_root, capture=True)
    if r.code != 0:
        raise RuntimeError((r.err or r.out).strip() or "flutter analyze 失败")


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


# =======================
# Entry: default APPLY
# =======================
def run(ctx: Context) -> int:
    # 默认：不过滤域名（任何 hosted+url 都算私有），默认 skip
    private_host_keywords: tuple[str, ...] = tuple()
    skip_packages: set[str] = {"ap_recaptcha"}

    plan = build_private_upgrade_plan(
        ctx=ctx,
        private_host_keywords=private_host_keywords,
        skip_packages=skip_packages,
    )

    # 只列出要修改的部分
    if not plan:
        ctx.echo("ℹ️ 未发现需要升级的私有依赖。")
        return 0

    ctx.echo("将升级以下私有依赖：")
    for u in plan:
        ctx.echo(f"  - {u.name}: {u.current} -> {u.target}")

    if not ctx.yes:
        if not ctx.confirm("是否继续执行升级，并在 pub get / analyze 通过后自动提交？"):
            ctx.echo("ℹ️ 已取消。")
            return 0

    # 应用修改（严格局部替换）
    changed, summary, errors = apply_upgrades_to_pubspec(ctx, plan)
    if errors:
        raise RuntimeError("升级过程中出现不可处理项：\n" + "\n".join(errors))

    if not changed:
        ctx.echo("ℹ️ 没有发生实际修改。")
        return 0

    # 实际修改清单（只列出要修改的部分）
    for s in summary:
        ctx.echo(s)

    if ctx.dry_run:
        ctx.echo("（dry-run）不执行 pub get / analyze / git commit。")
        return 0

    # 后置检查：pub get + analyze，任何失败直接抛出（不提交）
    flutter_pub_get(ctx)
    flutter_analyze(ctx)

    # 自动提交
    git_add_commit(ctx, summary)
    ctx.echo("✅ pub get / analyze 通过，已自动提交。")
    return 0
