from __future__ import annotations

import json
import re
import time
import threading
from itertools import cycle
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .tool import Context, read_text, write_text_atomic, run_cmd

# =======================
# Behavior switches
# =======================
UPGRADE_DEV_DEPENDENCIES = False        # 本次仍只处理 dependencies（你后续要扩展再开）
UPGRADE_DEPENDENCY_OVERRIDES = False

# Analyze 输出：最多展示前 N 条 info/warning
MAX_SHOW_INFOS = 3
MAX_SHOW_WARNINGS = 3
MAX_SHOW_ERRORS = 20  # 错误展示上限（避免刷屏）

# 默认跳过的包（即使是私有 hosted 也不参与升级/写回）
DEFAULT_SKIP_PACKAGES: set[str] = {"ap_recaptcha"}

# =======================
# Step UI
# =======================
@contextmanager
def step_scope(ctx: Context, idx: int, title: str, msg: str = ""):
    t0 = time.perf_counter()
    ctx.echo(f"\n========== Step {idx}: {title} ==========")
    if msg:
        ctx.echo(msg)
    try:
        yield
        ctx.echo(f"✅ Step {idx} 完成（{time.perf_counter() - t0:.2f}s）")
    except Exception as e:
        ctx.echo(f"❌ Step {idx} 失败（{time.perf_counter() - t0:.2f}s）：{e}")
        raise


def _ask_abort(ctx: Context, prompt: str) -> bool:
    """
    返回 True 表示中断
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

    r = run_cmd_with_loading(ctx, "git pull --ff-only", ["git", "pull", "--ff-only"], cwd=ctx.project_root)
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


def _git_add_commit_push(ctx: Context, summary_lines: list[str]) -> None:
    subject = "chore(pub): upgrade private deps"
    body = "\n".join(summary_lines) if summary_lines else ""
    msg = subject + ("\n\n" + body if body else "")

    paths = ["pubspec.yaml"]
    if (ctx.project_root / "pubspec.lock").exists():
        paths.append("pubspec.lock")

    # git add 通常很快，但也可能触发 hooks/扫描；统一加 loading
    r = run_cmd_with_loading(ctx, "git add", ["git", "add", *paths], cwd=ctx.project_root)
    if r.code != 0:
        raise RuntimeError(f"git add 失败：{(r.err or r.out).strip()}")

    # commit 可能触发 hooks，可能等待较久
    r = run_cmd_with_loading(ctx, "git commit", ["git", "commit", "-m", msg], cwd=ctx.project_root)
    if r.code != 0:
        raise RuntimeError(f"git commit 失败：{(r.err or r.out).strip()}")

    # push 可能等待网络
    br = _git_current_branch(ctx)
    if _git_has_remote_branch(ctx, br):
        r = run_cmd_with_loading(ctx, "git push", ["git", "push"], cwd=ctx.project_root)
        if r.code != 0:
            raise RuntimeError(f"git push 失败：{(r.err or r.out).strip()}")
    else:
        ctx.echo(f"ℹ️ 远端不存在 origin/{br}，跳过 push。")


# =======================
# Pubspec private deps (dependencies only)
# =======================
@dataclass(frozen=True)
class PubspecPrivateDep:
    name: str
    constraint: str
    hosted_url: str


def _is_section_header(line: str, section: str) -> bool:
    return bool(re.match(rf"^\s*{re.escape(section)}\s*:\s*(#.*)?$", line))


def _indent(s: str) -> int:
    return len(s) - len(s.lstrip(" "))


def read_pubspec_private_dependencies(pubspec_text: str) -> dict[str, PubspecPrivateDep]:
    """
    只读取 dependencies 区块内的私有 hosted 依赖。
    通过文本扫描尽量保留样式，不使用 YAML parser。
    识别形态（示例）：
      foo:
        hosted:
          url: https://...
          name: foo
        version: ^0.0.11
    也兼容 hosted: https://... 这种简写（如果存在的话）。
    """
    lines = pubspec_text.splitlines(keepends=False)
    in_deps = False
    deps_indent = None  # type: Optional[int]

    result: dict[str, PubspecPrivateDep] = {}

    i = 0
    while i < len(lines):
        line = lines[i]
        if _is_section_header(line, "dependencies"):
            in_deps = True
            deps_indent = _indent(line)
            i += 1
            continue

        # 离开 dependencies 区块：遇到同级 header（缩进<=deps_indent 且像 "xxx:"）
        if in_deps:
            if line.strip() and deps_indent is not None:
                if _indent(line) <= deps_indent and re.match(r"^\s*[A-Za-z0-9_]+\s*:\s*(#.*)?$", line):
                    in_deps = False
                    deps_indent = None
                    continue

            # 识别包名起点：两空格缩进 + name:
            m = re.match(r"^(\s*)([A-Za-z0-9_]+)\s*:\s*(#.*)?$", line)
            if m:
                name_indent = len(m.group(1))
                name = m.group(2)

                # 默认跳过：不参与私有依赖识别/升级
                if name in DEFAULT_SKIP_PACKAGES:
                    j = i + 1
                    while j < len(lines):
                        l = lines[j]
                        if l.strip() and _indent(l) <= name_indent:
                            break
                        j += 1
                    i = j
                    continue

                # block 可能是单行版本：  foo: ^1.2.3
                # 但这种写法没有 hosted url，不算“私有 hosted”，所以这里只处理 block
                # 扫描 block 直到缩进回退 <= name_indent
                hosted_url: Optional[str] = None
                constraint: Optional[str] = None

                j = i + 1
                while j < len(lines):
                    l = lines[j]
                    if l.strip() and _indent(l) <= name_indent:
                        break

                    # hosted 简写： hosted: https://...
                    m_hosted_short = re.match(r"^\s*hosted\s*:\s*([^\s#]+)\s*(#.*)?$", l)
                    if m_hosted_short and not hosted_url:
                        hosted_url = m_hosted_short.group(1).strip()

                    # hosted: 下的 url:
                    m_url = re.match(r"^\s*url\s*:\s*([^\s#]+)\s*(#.*)?$", l)
                    # 注意：url: 可能出现在别处，但通常在 hosted block 内；这里用“就近”策略
                    if m_url and not hosted_url:
                        hosted_url = m_url.group(1).strip()

                    # version:
                    m_ver = re.match(r"^\s*version\s*:\s*(.+?)\s*(#.*)?$", l)
                    if m_ver:
                        raw = m_ver.group(1).strip()
                        # 去掉包裹引号
                        if (raw.startswith('"') and raw.endswith('"')) or (raw.startswith("'") and raw.endswith("'")):
                            raw = raw[1:-1].strip()
                        constraint = raw

                    j += 1

                if hosted_url and constraint:
                    result[name] = PubspecPrivateDep(name=name, constraint=constraint, hosted_url=hosted_url)

                i = j
                continue

        i += 1

    return result


# =======================
# Outdated (show-all) + plan
# =======================
def flutter_pub_outdated_show_all_json(ctx: Context) -> dict:

    r = run_cmd_with_loading(
        ctx,
        "flutter pub outdated --show-all --json",
        ["flutter", "pub", "outdated", "--show-all", "--json"],
        cwd=ctx.project_root,
    )
    if r.code != 0:
        raise RuntimeError((r.err or r.out or "").strip() or "flutter pub outdated 失败")
    try:
        return json.loads(r.out or "{}")
    except Exception as e:
        raise RuntimeError(f"解析 outdated json 失败：{e}")

def _strip_meta(v: str) -> str:
    v = v.strip()
    v = v.split("+", 1)[0]
    v = v.split("-", 1)[0]
    return v.strip()


def _parse_nums(v: str) -> list[int]:
    base = _strip_meta(v)
    parts = base.split(".")
    nums: list[int] = []
    for p in parts:
        try:
            nums.append(int(p))
        except Exception:
            nums.append(0)
    return nums


def compare_versions(a: str, b: str) -> int:
    na = _parse_nums(a)
    nb = _parse_nums(b)
    n = max(len(na), len(nb))
    na += [0] * (n - len(na))
    nb += [0] * (n - len(nb))
    for x, y in zip(na, nb):
        if x < y:
            return -1
        if x > y:
            return 1
    return 0


def _extract_current_version(pkg: dict) -> Optional[str]:
    cur = pkg.get("current")
    if isinstance(cur, dict):
        v = cur.get("version")
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None


def _extract_latest_version(pkg: dict) -> Optional[str]:
    latest = pkg.get("latest")
    if isinstance(latest, dict):
        v = latest.get("version")
        if isinstance(v, str) and v.strip():
            return v.strip()
    res = pkg.get("resolvable")
    if isinstance(res, dict):
        v = res.get("version")
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None


@dataclass(frozen=True)
class UpgradeItem:
    name: str
    pubspec_constraint: str
    resolved_current: str
    latest: str


def build_private_upgrade_plan_from_pubspec(
    ctx: Context,
    pubspec_privates: dict[str, PubspecPrivateDep],
) -> list[UpgradeItem]:
    data = flutter_pub_outdated_show_all_json(ctx)
    pkgs = data.get("packages") or []
    idx: dict[str, dict] = {}
    for pkg in pkgs:
        name = pkg.get("package")
        if isinstance(name, str) and name:
            idx[name] = pkg

    plan: list[UpgradeItem] = []
    for name, dep in pubspec_privates.items():
        pkg = idx.get(name)
        if not pkg:
            # outdated 里没有（可能没解析出来/被 override 影响），跳过但提示
            ctx.echo(f"⚠️ outdated 输出中找不到包 {name}，跳过比对。")
            continue

        cur = _extract_current_version(pkg) or "(unknown)"
        latest = _extract_latest_version(pkg)
        if not latest:
            continue

        # 只要 latest > current 才算需要升级
        if cur != "(unknown)" and compare_versions(cur, latest) >= 0:
            continue

        plan.append(
            UpgradeItem(
                name=name,
                pubspec_constraint=dep.constraint,
                resolved_current=cur,
                latest=latest,
            )
        )

    plan.sort(key=lambda x: x.name.lower())
    return plan


# =======================
# Apply upgrades to pubspec (preserve style)
# =======================
_SIMPLE_CONSTRAINT_RE = re.compile(
    r"""^\s*
    (?P<quote>['"]?)               # optional quote
    (?P<prefix>\^|~)?              # optional prefix
    (?P<ver>\d+(?:\.\d+){1,3})     # x.y or x.y.z(.w)
    (?P<quote2>['"]?)              # optional closing quote
    \s*$""",
    re.VERBOSE,
)


def _is_complex_constraint(s: str) -> bool:
    s = s.strip()
    # 粗略判定复杂范围：含空格、比较符、逻辑或/and 等
    return any(tok in s for tok in [">", "<", "=", "||", "&&", " - ", " "])


def apply_upgrades_to_pubspec(
    ctx: Context,
    pubspec_path: Path,
    plan: list[UpgradeItem],
) -> tuple[list[str], list[str]]:
    """
    只在 dependencies 区块里按包名定位 block，替换 block 内第一条 version: 行。
    返回：(applied_summaries, skipped_summaries)
    """
    if not plan:
        return ([], [])

    content = read_text(pubspec_path)
    lines = content.splitlines(keepends=True)

    # 建索引：name -> UpgradeItem
    plan_map = {u.name: u for u in plan}

    in_deps = False
    deps_indent: Optional[int] = None

    applied: list[str] = []
    skipped: list[str] = []

    i = 0
    while i < len(lines):
        line = lines[i]

        if _is_section_header(line, "dependencies"):
            in_deps = True
            deps_indent = _indent(line)
            i += 1
            continue

        if in_deps:
            # 离开区块：同级 header
            if line.strip() and deps_indent is not None:
                if _indent(line) <= deps_indent and re.match(r"^\s*[A-Za-z0-9_]+\s*:\s*(#.*)?$", line):
                    in_deps = False
                    deps_indent = None
                    continue

            # 包 block 起点
            m = re.match(r"^(\s*)([A-Za-z0-9_]+)\s*:\s*(#.*)?$", line)
            if m:
                name_indent = len(m.group(1))
                name = m.group(2)
                u = plan_map.get(name)
                if not u:
                    i += 1
                    continue

                # 在该 block 内找 version:
                j = i + 1
                replaced = False
                while j < len(lines):
                    l = lines[j]
                    if l.strip() and _indent(l) <= name_indent:
                        break

                    m_ver = re.match(r"^(\s*version\s*:\s*)(.+?)(\s*(#.*)?)\r?\n?$", l)
                    if m_ver and not replaced:
                        prefix = m_ver.group(1)
                        raw_val = (m_ver.group(2) or "").strip()
                        suffix = m_ver.group(3) or ""

                        # 去引号后判断复杂度
                        val = raw_val
                        if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
                            val_unquoted = val[1:-1].strip()
                        else:
                            val_unquoted = val

                        if _is_complex_constraint(val_unquoted):
                            skipped.append(f"{name}: 复杂约束 '{val_unquoted}'，跳过修改（latest={u.latest}）")
                            replaced = True  # 标记为处理过，避免重复提示
                            break

                        m_simple = _SIMPLE_CONSTRAINT_RE.match(val_unquoted)
                        if not m_simple:
                            skipped.append(f"{name}: 无法识别约束 '{val_unquoted}'，跳过修改（latest={u.latest}）")
                            replaced = True
                            break

                        # 保留前缀符号 ^ 或 ~
                        keep_prefix = m_simple.group("prefix") or ""
                        new_val_unquoted = f"{keep_prefix}{u.latest}"

                        # 保留原引号样式
                        if raw_val.startswith('"') and raw_val.endswith('"'):
                            new_val = f"\"{new_val_unquoted}\""
                        elif raw_val.startswith("'") and raw_val.endswith("'"):
                            new_val = f"'{new_val_unquoted}'"
                        else:
                            new_val = new_val_unquoted

                        # 写回该行，保留行尾注释
                        newline = "\n" if l.endswith("\n") else ""
                        lines[j] = f"{prefix}{new_val}{suffix}{newline}"
                        applied.append(f"{name}: {u.pubspec_constraint} -> {keep_prefix}{u.latest}")
                        replaced = True
                        break

                    j += 1

                if not replaced:
                    skipped.append(f"{name}: 未找到 version: 行，跳过（latest={u.latest}）")

                i = j
                continue

        i += 1

    new_content = "".join(lines)
    if new_content != content:
        write_text_atomic(pubspec_path, new_content)
    return (applied, skipped)


# =======================
# Flutter commands (post-apply)
# =======================
def flutter_pub_get(ctx: Context) -> None:

    r = run_cmd_with_loading(ctx, "flutter pub get", ["flutter", "pub", "get"], cwd=ctx.project_root)
    if r.code != 0:
        raise RuntimeError((r.err or r.out).strip() or "flutter pub get 失败")

class AnalyzeResult:
    ok: bool
    errors: list[str]
    warnings: list[str]
    infos: list[str]
    raw_exit_code: int


_ANALYZE_DIAG_RE = re.compile(r"^\s*(info|warning|error)\s*•\s*(.+?)\s*•\s*(.+?)\s*•\s*(.+?)\s*$", re.IGNORECASE)


def flutter_analyze(ctx: Context) -> AnalyzeResult:

    """
    你的规则：
      - 有 info / warning：列出前两三条，然后继续
      - 有 error：列出来，中断（不提交）
    """
    cmd = ["flutter", "analyze", "--no-fatal-warnings", "--no-fatal-infos"]
    r = run_cmd_with_loading(ctx, "flutter analyze", cmd, cwd=ctx.project_root)

    out = (r.out or "") + ("\n" + r.err if r.err else "")
    errors: list[str] = []
    warnings: list[str] = []
    infos: list[str] = []

    for line in out.splitlines():
        m = _ANALYZE_DIAG_RE.match(line)
        if not m:
            continue
        level = m.group(1).lower().strip()
        msg = m.group(2).strip()
        file_ = m.group(3).strip()
        hint = m.group(4).strip()
        formatted = f"{level.upper()}: {msg} ({file_}) {hint}"
        if level == "error":
            errors.append(formatted)
        elif level == "warning":
            warnings.append(formatted)
        else:
            infos.append(formatted)

    ok = (len(errors) == 0)
    return AnalyzeResult(ok=ok, errors=errors, warnings=warnings, infos=infos, raw_exit_code=r.code)

def run(ctx: Context) -> int:
    """
    阶段 3：写回升级 + pub get + analyze + git commit + push
    规则：
      - analyze 有 warning/info：展示前 2~3 条，继续
      - analyze 有 error：展示并中断（不提交）
    """
    t0 = time.perf_counter()
    try:
        with step_scope(ctx, 0, "环境检查（git 仓库）", "检查 git 仓库..."):
            _git_check_repo(ctx)

        with step_scope(ctx, 1, "检查是否有未提交变更", "检查工作区状态..."):
            if _git_is_dirty(ctx):
                ctx.echo("⚠️ 检测到未提交变更（working tree dirty）。")
                if _ask_abort(ctx, "检测到未提交变更，是否中断本次执行？"):
                    ctx.echo("已中断。")
                    return 0
                ctx.echo("继续执行。")
            else:
                ctx.echo("✅ 工作区干净")

        with step_scope(ctx, 2, "同步远端（git pull --ff-only）", "拉取远程更新..."):
            _git_pull_ff_only(ctx)

        with step_scope(ctx, 3, "执行 flutter pub get（预检查）", "正在执行 pub get..."):
            flutter_pub_get(ctx)
            ctx.echo("✅ pub get 通过")

        with step_scope(ctx, 4, "读取 pubspec.yaml 私有依赖（dependencies）", "扫描 dependencies 区块中的 hosted 私有依赖..."):
            pubspec_text = read_text(ctx.pubspec_path)
            privates = read_pubspec_private_dependencies(pubspec_text)
            if not privates:
                ctx.echo("ℹ️ dependencies 中未发现 hosted 私有依赖。")
            else:
                ctx.echo(f"✅ 发现 {len(privates)} 个私有依赖（dependencies）")

        with step_scope(ctx, 5, "分析待升级私有依赖", "执行 flutter pub outdated --show-all --json 并比对..."):
            plan = build_private_upgrade_plan_from_pubspec(ctx, privates)

            if not plan:
                ctx.echo("ℹ️ 未发现需要升级的私有依赖。")
                return 0

            ctx.echo("\n待升级私有依赖清单（resolved_current -> latest）：")
            for u in plan:
                ctx.echo(f"  - {u.name}: {u.resolved_current} -> {u.latest}   (pubspec: {u.pubspec_constraint})")

        with step_scope(ctx, 6, "写回 pubspec.yaml（只改 version，保留样式/注释）", "应用升级计划到 pubspec.yaml ..."):
            applied, skipped = apply_upgrades_to_pubspec(ctx, ctx.pubspec_path, plan)
            if applied:
                ctx.echo("✅ 已应用：")
                for s in applied:
                    ctx.echo(f"  - {s}")
            if skipped:
                ctx.echo("⚠️ 已跳过：")
                for s in skipped[:20]:
                    ctx.echo(f"  - {s}")
                if len(skipped) > 20:
                    ctx.echo(f"  ... 另有 {len(skipped) - 20} 条跳过原因未展示")

            if not applied:
                ctx.echo("ℹ️ 没有可写回的改动（可能都是复杂约束或没找到 version 行）。停止后续步骤。")
                return 0

        with step_scope(ctx, 7, "执行 flutter pub get", "更新 lockfile ..."):
            flutter_pub_get(ctx)

        with step_scope(ctx, 8, "执行 flutter analyze", "进行静态检查..."):
            ar = flutter_analyze(ctx)

            # 展示 info / warning（前几条）
            if ar.infos:
                ctx.echo(f"ℹ️ analyze info 共 {len(ar.infos)} 条，展示前 {min(MAX_SHOW_INFOS, len(ar.infos))} 条：")
                for s in ar.infos[:MAX_SHOW_INFOS]:
                    ctx.echo(f"  - {s}")

            if ar.warnings:
                ctx.echo(f"⚠️ analyze warning 共 {len(ar.warnings)} 条，展示前 {min(MAX_SHOW_WARNINGS, len(ar.warnings))} 条：")
                for s in ar.warnings[:MAX_SHOW_WARNINGS]:
                    ctx.echo(f"  - {s}")

            if not ar.ok:
                ctx.echo(f"❌ analyze error 共 {len(ar.errors)} 条，展示前 {min(MAX_SHOW_ERRORS, len(ar.errors))} 条：")
                for s in ar.errors[:MAX_SHOW_ERRORS]:
                    ctx.echo(f"  - {s}")
                ctx.echo("⛔ 存在 error，按规则中断，不提交。")
                return 1

            ctx.echo("✅ analyze 无 error（info/warning 不阻断）")

        with step_scope(ctx, 9, "提交到 git 并推送到远端", "git add/commit/push ..."):
            summary_lines = [f"{u.name}: {u.resolved_current} -> {u.latest}" for u in plan]
            _git_add_commit_push(ctx, summary_lines)

        ctx.echo(f"\n✅ 全流程完成，总耗时 {time.perf_counter() - t0:.2f}s")
        return 0

    except KeyboardInterrupt:
        ctx.echo("\n⛔ 用户中断。")
        return 130
    except Exception as e:
        ctx.echo(f"\n❌ 执行失败：{e}")
        return
class _Loader:
    """简单 spinner + 计时器：用于长命令执行时显示 loading 与耗时。

    注意：ctx.echo 在 box_tools 的实现里不一定支持 end= 参数，因此这里直接写 stdout。
    如果 stdout 不可用，则退化为每隔一段时间打印一行（不会崩）。
    """

    def __init__(self, ctx: Context, label: str, interval: float = 0.1):
        self.ctx = ctx
        self.label = label
        self.interval = interval
        self._stop = threading.Event()
        self._t: Optional[threading.Thread] = None
        self._t0 = 0.0
        self._spinner = cycle(["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"])
        self._last_fallback_print = 0.0

    def _write_stdout(self, s: str) -> bool:
        try:
            sys.stdout.write(s)
            sys.stdout.flush()
            return True
        except Exception:
            return False

    def start(self) -> None:
        self._t0 = time.perf_counter()

        def _run():
            while not self._stop.is_set():
                elapsed = time.perf_counter() - self._t0
                msg = f"{next(self._spinner)} {self.label}… {elapsed:0.1f}s"
                # 优先用 stdout 做单行刷新；否则退化为定期 ctx.echo（避免刷屏）
                if self._write_stdout("\r" + msg):
                    time.sleep(self.interval)
                    continue

                now = time.perf_counter()
                if now - self._last_fallback_print >= 1.0:
                    self._last_fallback_print = now
                    self.ctx.echo(msg)
                time.sleep(self.interval)

        self._t = threading.Thread(target=_run, daemon=True)
        self._t.start()

    def stop(self) -> float:
        self._stop.set()
        if self._t:
            self._t.join(timeout=0.3)
        elapsed = time.perf_counter() - self._t0
        # 清掉 stdout 上残留的单行
        self._write_stdout("\r" + (" " * 120) + "\r")
        return elapsed


def run_cmd_with_loading(ctx: Context, label: str, cmd: list[str], cwd: Path):

    loader = _Loader(ctx, label)
    loader.start()
    try:
        r = run_cmd(cmd, cwd=cwd, capture=True)
    finally:
        elapsed = loader.stop()
        ctx.echo(f"✅ {label} 完成（{elapsed:.2f}s）")
    return r
