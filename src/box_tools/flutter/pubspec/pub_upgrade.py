from __future__ import annotations

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


def git_add_commit(ctx: Context, summary_lines: list[str]) -> None:
    """
    统一提交（用于依赖升级/版本号升级）
    """
    subject = "chore(pub): upgrade private deps"
    body = "\n".join(summary_lines) if summary_lines else ""
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

    # 如果有 remote 分支，则 push（沿用原逻辑）
    try:
        br = _git_current_branch(ctx)
        if _git_has_remote_branch(ctx, br):
            r = run_cmd(["git", "push"], cwd=ctx.project_root, capture=True)
            if r.code != 0:
                raise RuntimeError(f"git push 失败：{(r.err or r.out).strip()}")
    except Exception:
        # push 失败不应静默吞掉：抛出给上层
        raise


# =======================
# Version parsing / compare
# =======================
def is_valid_version(v: str) -> bool:
    return bool(re.match(r"^[0-9]+(?:\.[0-9]+){1,3}(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$", v.strip()))


def _strip_meta(v: str) -> str:
    """
    比较用：去掉 -pre / +build 等元信息，只保留数字段
    """
    v = v.strip()
    v = v.split("+", 1)[0]
    v = v.split("-", 1)[0]
    return v


def _version_parts(v: str) -> list[int]:
    v = _strip_meta(v)
    parts = [p for p in v.split(".") if p.strip() != ""]
    out: list[int] = []
    for p in parts:
        try:
            out.append(int(p))
        except Exception:
            out.append(0)
    return out


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


def write_pubspec_app_version(pubspec_text: str, new_version: str) -> tuple[str, bool]:
    """
    将 pubspec.yaml 顶层 version: 改为 new_version（只改第一处匹配行，保留行尾注释）。
    返回：(new_text, changed)
    """
    if not is_valid_version(new_version):
        raise ValueError(f"非法版本号：{new_version}")

    lines = pubspec_text.splitlines(keepends=True)
    changed = False
    out: list[str] = []

    # 允许：version: 1.2.3 / version: 1.2.3+4 / version: 1.2.3-pre  # comment
    pat = re.compile(
        r"^(\s*version\s*:\s*)([0-9]+(?:\.[0-9]+){1,3}(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?)(\s*(?:#.*)?)\s*$"
    )

    replaced = False
    for raw in lines:
        if not replaced:
            m = pat.match(raw.rstrip("\n\r"))
            if m:
                prefix, old_v, suffix = m.group(1), m.group(2), m.group(3) or ""
                if old_v != new_version:
                    # 保留原始换行符风格
                    newline = "\n"
                    if raw.endswith("\r\n"):
                        newline = "\r\n"
                    out.append(f"{prefix}{new_version}{suffix}{newline}")
                    changed = True
                else:
                    out.append(raw)
                replaced = True
                continue
        out.append(raw)

    if not replaced:
        raise RuntimeError("pubspec.yaml 未找到顶层 version: 字段，无法在 release 分支做版本校验/修正。")

    return "".join(out), changed


def _parse_release_branch_version(branch: str) -> Optional[str]:
    """
    从 release-* 分支名中解析版本号。
    例：release-3.46.0 -> 3.46.0
    """
    m = re.match(r"^release-([0-9]+(?:\.[0-9]+){2})$", branch.strip())
    return m.group(1) if m else None


def ensure_release_branch_version_guard(ctx: Context) -> None:
    """
    upgrade 执行时的 release 分支版本守门：
    - 非 release-* 分支：跳过
    - pubspec version < release 版本：提示用户 y/yes 则改版本并自动 git commit；n 则不改继续
    - pubspec version > release 版本：抛异常中断
    """
    branch = _git_current_branch(ctx)
    release_v = _parse_release_branch_version(branch)
    if not release_v:
        return  # 非 release 分支，直接放行

    pubspec_text = read_text(ctx.pubspec_path)
    current_v = read_pubspec_app_version(pubspec_text)
    if not current_v:
        raise RuntimeError("在 release 分支上未能读取 pubspec.yaml 顶层 version:，请先补齐后再执行 upgrade。")

    # 比较时忽略 +meta / -pre 等
    cmp = compare_versions(_strip_meta(current_v), _strip_meta(release_v))
    if cmp == 0:
        ctx.echo(f"✅ release 分支版本校验通过：{branch} 与 pubspec version={current_v} 一致")
        return

    if cmp > 0:
        raise RuntimeError(
            f"❌ 版本不一致：当前分支 {branch}（{release_v}）"
            f" 但 pubspec.yaml version={current_v} 更高。请切到正确的 release 分支或修正 version 后再升级。"
        )

    # cmp < 0 ：pubspec 低于 release 版本
    ctx.echo(f"⚠️ 检测到 release 分支 {branch}（{release_v}），但 pubspec.yaml version={current_v} 更低。")
    if ctx.yes:
        do_upgrade = True
    else:
        # 这里语义是：y/yes => 升级并提交；n/no => 不改继续
        do_upgrade = ctx.confirm(
            f"是否将 version 升级到 {release_v} 并自动提交到 git？（y/yes 提交；n/no 跳过修改继续）"
        )

    if not do_upgrade:
        ctx.echo("选择不修改 version，继续原 upgrade 流程。")
        return

    new_text, changed = write_pubspec_app_version(pubspec_text, release_v)
    if not changed:
        ctx.echo("version 已是目标值，无需修改。")
        return

    write_text_atomic(ctx.pubspec_path, new_text)
    ctx.echo(f"✅ 已更新 pubspec.yaml version：{current_v} -> {release_v}")

    # 自动提交
    git_add_commit(ctx, [f"🔼 bump app version: {current_v} -> {release_v}"])
    ctx.echo("✅ 已自动提交版本号变更，继续原 upgrade 流程。")


def upper_bound_of_minor(app_version: str) -> Optional[str]:
    """
    app_version=3.46.0 -> 3.47.0 （用于 pub constraints 生成上界）
    """
    if not app_version:
        return None
    parts = _version_parts(app_version)
    if len(parts) < 2:
        return None
    parts[1] += 1
    for i in range(2, len(parts)):
        parts[i] = 0
    return ".".join(str(x) for x in parts[:3])


# =======================
# Pub / Flutter helpers
# =======================
def flutter_pub_get(ctx: Context, with_loading: bool = True) -> None:
    if with_loading:
        with loading(ctx, "flutter pub get"):
            r = run_cmd(["flutter", "pub", "get"], cwd=ctx.project_root, capture=True)
    else:
        r = run_cmd(["flutter", "pub", "get"], cwd=ctx.project_root, capture=True)

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

    规则（按你的要求“别自作聪明”）：
      - 只升级到 latest（传入的 new_version 必须是裸版本号，如 3.46.0）
      - 如果原 token 以 '^' 开头，则保留 '^'：^old -> ^new
      - 如果原 token 没有 '^'，则写成裸版本：old -> new
      - 对复杂约束（如 ">=... <..."）不做推导，保持不改
    """

    def _match_simple_token(tok: str) -> Optional[bool]:
        """
        返回：
          - True  : tok 是 '^<semver>' 形式
          - False : tok 是 '<semver>' 形式
          - None  : 其它复杂形式（不改）
        """
        tok = tok.strip()
        # 允许 semver 的 -pre / +build
        semver = r"\d+\.\d+(?:\.\d+)?(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?"
        if re.match(rf"^\^{semver}$", tok):
            return True
        if re.match(rf"^{semver}$", tok):
            return False
        return None

    changed = False
    out: list[str] = []
    bare_new = new_version.lstrip("^").strip()

    for raw in block_lines:
        m = _VERSION_LINE_RE.match(raw.rstrip("\n\r"))
        if not m:
            out.append(raw)
            continue

        indent, name, ver, comment = m.group(1), m.group(2), m.group(3), m.group(4) or ""

        def _emit_replaced(line_key: str, old_tok: str) -> str:
            keep_caret = _match_simple_token(old_tok)
            if keep_caret is None:
                # 复杂约束，不改
                return raw
            new_tok = ("^" + bare_new) if keep_caret else bare_new
            if old_tok == new_tok:
                return raw

            newline = "\n"
            if raw.endswith("\r\n"):
                newline = "\r\n"
            spacer = (" " if comment and not comment.startswith(" ") else "")
            if line_key == "version":
                return f"{indent}version: {new_tok}{spacer}{comment}{newline}"
            return f"{indent}{name}: {new_tok}{spacer}{comment}{newline}"

        if name == "version":
            replaced = _emit_replaced("version", ver)
            if replaced is not raw:
                changed = True
            out.append(replaced)
        else:
            # 依赖名行：foo: <token>
            # 只处理简单 token（^semver / semver），避免碰到 map / git / path / 复杂 range
            if _match_simple_token(ver) is None:
                out.append(raw)
            else:
                replaced = _emit_replaced("dep", ver)
                if replaced is not raw:
                    changed = True
                out.append(replaced)

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
                # 不是目标 section，直接原样输出
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
            # flush 前一个 block
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
                    # 是否进入下一个依赖 block？
                    # 新行缩进 <= 当前 dep 缩进 且不是空行/注释，视为新 block
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

    # flush last
    flush_block()

    # 校验：目标包必须都能被处理到（如果完全没改到，且确实存在升级目标，也认为是错误）
    for u in upgrades:
        if any(s.startswith(f"{u.name}: ") for s in summary_lines):
            continue
        # 如果这个包本身不在 pubspec 里（比如被删了），这里不强制报错
        # 但你原需求是“有问题就抛出”，所以这里保守记 error
        errors.append(f"未能在 pubspec.yaml 中定位并替换依赖：{u.name}（section={u.section}）")

    # 写回
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


def _extract_current_version(item: dict) -> Optional[str]:
    cur = item.get("current")
    if isinstance(cur, dict):
        v = cur.get("version")
        return v if isinstance(v, str) else None
    return None


def _extract_target_version(item: dict) -> Optional[str]:
    # 优先 latest.version
    latest = item.get("latest")
    if isinstance(latest, dict):
        v = latest.get("version")
        if isinstance(v, str):
            return v
    # fallback resolvable
    res = item.get("resolvable")
    if isinstance(res, dict):
        v = res.get("version")
        if isinstance(v, str):
            return v
    return None


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


def build_private_upgrade_plan(
    ctx: Context,
    private_host_keywords: tuple[str, ...],
    skip_packages: set[str],
) -> list[UpgradeItem]:
    """
    私有依赖升级策略（去掉“自作聪明”版）：

    在 release / upgrade 时：
      - 仅处理“私有 hosted 依赖”（dependency.hosted.url 存在，且可选命中关键词）
      - 版本一律升级到 pub outdated 的 latest.version（fallback resolvable.version）
      - 不扫描 pubspec.yaml 推导写回形式
      - 是否保留 '^' 由写回时根据原 token 决定：
          有 '^' -> 保留 '^'，仅替换版本号
          无 '^' -> 写成裸版本号
    """
    data = _parse_pub_outdated(ctx)
    pkgs = data.get("packages") or []
    plan: list[UpgradeItem] = []

    for pkg in pkgs:
        name = pkg.get("package")
        print(pkg)
        if not isinstance(name, str) or not name:
            continue
        if name in skip_packages:
            continue
        print('name: ', name)
        kind = pkg.get("kind")  # direct/dev/override/transitive
        if kind == "transitive":
            continue
        print('kind: ', kind)
        dep = pkg.get("dependency") or {}
        print('dependency: ', dep)
        if not isinstance(dep, dict):
            continue
        if not _is_private_dep(dep, private_host_keywords):
            continue
        print('name: ', name)
        section = "dependencies"
        if kind == "dev":
            section = "dev_dependencies"
            if not UPGRADE_DEV_DEPENDENCIES:
                continue
        if kind == "override":
            section = "dependency_overrides"
            if not UPGRADE_DEPENDENCY_OVERRIDES:
                continue

        current_v = _extract_current_version(pkg) or ""
        latest_v = _extract_latest_version(pkg) or ""
        if not latest_v:
            continue
        if current_v and compare_versions(_strip_meta(current_v), _strip_meta(latest_v)) >= 0:
            continue

        plan.append(UpgradeItem(name=name, current=current_v or "(unknown)", target=latest_v, section=section))

    plan.sort(key=lambda x: x.name.lower())
    return plan


# =======================
# Entry
# =======================

def run(ctx: Context) -> int:
    """
    版本依赖升级工具（阶段 1：只做到“环境检查 + pub get 预检查 + 分析私有依赖待升级清单”）

    当前阶段包含的步骤：
      0) 环境检查（git 仓库）
      1) 检查是否有未提交变更
      2) 同步远端（git pull --ff-only）
      3) 执行 flutter pub get（预检查）
      4) 分析待升级私有依赖，列出 package: current -> latest

    注意：本阶段不会修改 pubspec.yaml / 不会执行升级 / 不会提交代码。
    """
    # 默认：不过滤域名（任何 hosted+url 都算私有），默认 skip
    private_host_keywords: tuple[str, ...] = tuple()
    skip_packages: set[str] = {"ap_recaptcha"}

    total_t0 = time.perf_counter()
    try:
        with step_scope(ctx, 0, "环境检查（git 仓库）", "检查 git 仓库..."):
            _git_check_repo(ctx)

        with step_scope(ctx, 1, "检查是否有未提交变更", "检查工作区状态..."):
            if _git_is_dirty(ctx):
                ctx.echo("⚠️ 检测到未提交变更（working tree dirty）。")
                # 这里不擅自决定要不要继续：默认询问；--yes 则自动继续
                if _ask_continue(ctx, "检测到未提交变更，是否中断本次执行？"):
                    ctx.echo("已中断。")
                    return 0
                ctx.echo("继续执行（注意：后续步骤可能依赖干净工作区的可重复性）。")
            else:
                ctx.echo("✅ 工作区干净")

        with step_scope(ctx, 2, "同步远端（git pull --ff-only）", "拉取远程更新..."):
            _git_pull_ff_only(ctx)

        with step_scope(ctx, 3, "执行 flutter pub get（预检查）", "正在执行 pub get..."):
            flutter_pub_get(ctx, with_loading=False)
            ctx.echo("✅ pub get 通过")

        with step_scope(
            ctx,
            4,
            f"分析待升级私有依赖（dev={UPGRADE_DEV_DEPENDENCIES}, overrides={UPGRADE_DEPENDENCY_OVERRIDES})",
            "解析 flutter pub outdated --json ...",
        ):
            plan = build_private_upgrade_plan(
                ctx=ctx,
                private_host_keywords=private_host_keywords,
                skip_packages=skip_packages,
            )

        if not plan:
            ctx.echo("ℹ️ 未发现需要升级的私有依赖。")
            return 0

        ctx.echo("\n待升级私有依赖清单（current -> latest）：")
        for u in plan:
            ctx.echo(f"  - {u.name}: {u.current} -> {u.target}")

        dt = time.perf_counter() - total_t0
        ctx.echo(f"\n✅ 完成（仅分析，不做升级与提交）。总耗时 {dt:.2f}s")
        return 0

    except KeyboardInterrupt:
        ctx.echo("\n⛔ 用户中断。")
        return 130
    except Exception as e:
        ctx.echo(f"\n❌ 执行失败：{e}")
        return 1
