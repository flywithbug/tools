from __future__ import annotations

from dataclasses import dataclass, asdict, field
from typing import Any, Dict, List, Mapping, Optional, Sequence, Union


# ----------------------------
# 主结构：ToolSpec
# ----------------------------

@dataclass(frozen=True)
class ToolSpec:
    id: str
    name: str
    category: str
    summary: str

    usage: List[str] = field(default_factory=list)

    # ✅ 直接用 dict 作为结构，和最终 BOX_TOOL 输出一致
    options: List[Dict[str, str]] = field(default_factory=list)   # [{"flag": "...", "desc": "..."}]
    examples: List[Dict[str, str]] = field(default_factory=list)  # [{"cmd": "...", "desc": "..."}]

    dependencies: List[str] = field(default_factory=list)

    # ✅ 约定：所有工具 docs 默认 README.md
    docs: str = "README.md"

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d.setdefault("usage", [])
        d.setdefault("options", [])
        d.setdefault("examples", [])
        d.setdefault("dependencies", [])
        d.setdefault("docs", "README.md")
        return d


# ----------------------------
# 便捷构造器：opt / ex / tool
# ----------------------------

def opt(flag: str, desc: str) -> Dict[str, str]:
    return {"flag": flag, "desc": desc}


def ex(cmd: str, desc: str) -> Dict[str, str]:
    return {"cmd": cmd, "desc": desc}


def tool(
        *,
        id: str,
        name: str,
        category: str,
        summary: str,
        usage: Optional[Sequence[str]] = None,
        options: Optional[Sequence[Mapping[str, Any]]] = None,
        examples: Optional[Sequence[Mapping[str, Any]]] = None,
        dependencies: Optional[Sequence[str]] = None,
        docs: Optional[str] = None,
) -> Dict[str, Any]:
    """
    用最短方式生成标准 BOX_TOOL dict（保持模块导出 BOX_TOOL = dict）。
    """
    spec = ToolSpec(
        id=id,
        name=name,
        category=category,
        summary=summary,
        usage=list(usage or []),
        options=_coerce_options(options or []),
        examples=_coerce_examples(examples or []),
        dependencies=list(dependencies or []),
        docs=(docs or "README.md"),
    )
    return spec.to_dict()


# ----------------------------
# normalize + validate
# ----------------------------

ToolLike = Union[ToolSpec, Mapping[str, Any]]


def normalize_tool(obj: ToolLike) -> Dict[str, Any]:
    """
    把 ToolSpec 或 dict 统一为标准 dict，并补齐默认字段。
    """
    if isinstance(obj, ToolSpec):
        d = obj.to_dict()
        _ = validate_tool(d)
        return d

    if not isinstance(obj, Mapping):
        raise TypeError(f"BOX_TOOL 必须是 dict/Mapping 或 ToolSpec，实际是：{type(obj).__name__}")

    d = dict(obj)

    d.setdefault("usage", [])
    d.setdefault("options", [])
    d.setdefault("examples", [])
    d.setdefault("dependencies", [])
    d.setdefault("docs", "README.md")

    d["options"] = _coerce_options(d.get("options") or [])
    d["examples"] = _coerce_examples(d.get("examples") or [])

    return d


def validate_tool(d: Mapping[str, Any]) -> List[str]:
    """
    返回错误列表（空列表表示通过）。不直接 raise，方便 box tools --full 聚合展示。
    """
    errors: List[str] = []

    def _req_str(key: str) -> None:
        v = d.get(key)
        if not isinstance(v, str) or not v.strip():
            errors.append(f"缺少或非法字段：{key}（必须为非空字符串）")

    _req_str("id")
    _req_str("name")
    _req_str("category")
    _req_str("summary")

    usage = d.get("usage")
    if not isinstance(usage, list) or any(not isinstance(x, str) or not x.strip() for x in usage):
        errors.append("字段 usage 必须是字符串列表（list[str]），且每项非空")

    docs = d.get("docs")
    if not isinstance(docs, str) or not docs.strip():
        errors.append("字段 docs 必须为非空字符串（例如 README.md）")

    deps = d.get("dependencies")
    if not isinstance(deps, list) or any(not isinstance(x, str) or not x.strip() for x in deps):
        errors.append("字段 dependencies 必须是字符串列表（list[str]）")

    options = d.get("options")
    if not isinstance(options, list):
        errors.append("字段 options 必须是 list[{'flag','desc'}]")
    else:
        for i, it in enumerate(options):
            if not isinstance(it, Mapping):
                errors.append(f"options[{i}] 必须是 dict")
                continue
            flag = it.get("flag")
            desc = it.get("desc")
            if not isinstance(flag, str) or not flag.strip():
                errors.append(f"options[{i}].flag 必须为非空字符串")
            if not isinstance(desc, str) or not desc.strip():
                errors.append(f"options[{i}].desc 必须为非空字符串")

    examples = d.get("examples")
    if not isinstance(examples, list):
        errors.append("字段 examples 必须是 list[{'cmd','desc'}]")
    else:
        for i, it in enumerate(examples):
            if not isinstance(it, Mapping):
                errors.append(f"examples[{i}] 必须是 dict")
                continue
            cmd = it.get("cmd")
            desc = it.get("desc")
            if not isinstance(cmd, str) or not cmd.strip():
                errors.append(f"examples[{i}].cmd 必须为非空字符串")
            if not isinstance(desc, str) or not desc.strip():
                errors.append(f"examples[{i}].desc 必须为非空字符串")

    return errors


# ----------------------------
# 内部：把 options/examples 统一成 list[dict[str,str]]
# ----------------------------

def _coerce_options(items: Sequence[Mapping[str, Any]]) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    for it in items:
        out.append(
            {
                "flag": str(it.get("flag", "")).strip(),
                "desc": str(it.get("desc", "")).strip(),
            }
        )
    return out


def _coerce_examples(items: Sequence[Mapping[str, Any]]) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    for it in items:
        out.append(
            {
                "cmd": str(it.get("cmd", "")).strip(),
                "desc": str(it.get("desc", "")).strip(),
            }
        )
    return out


# ----------------------------
# 版本检测：对比 GitHub raw 的 pyproject.toml
# ----------------------------

import re
import time
import urllib.request
from pathlib import Path
from importlib import metadata
from typing import Callable, Optional as _Optional


REMOTE_PYPROJECT_URL = "https://raw.githubusercontent.com/flywithbug/tools/refs/heads/master/pyproject.toml"


@dataclass(frozen=True)
class VersionCheckResult:
    dist_name: str
    installed: _Optional[str]
    latest: _Optional[str]
    has_update: bool
    note: str


def _parse_project_version_from_pyproject_toml(text: str) -> _Optional[str]:
    """从 pyproject.toml 里提取 version = "x.y.z"（容忍文件是一行或多行）。"""
    m = re.search(r'(?m)\bversion\s*=\s*"([^"]+)"', text)
    return m.group(1).strip() if m else None


def _get_installed_version(dist_name: str) -> _Optional[str]:
    try:
        return metadata.version(dist_name)
    except metadata.PackageNotFoundError:
        return None


def _find_local_pyproject(start: Path | None = None) -> Path | None:
    """从当前文件位置向上查找 pyproject.toml（用于源码运行时获取本地版本）。"""
    p = start or Path(__file__).resolve()
    for parent in [p.parent, *p.parents]:
        cand = parent / "pyproject.toml"
        if cand.exists() and cand.is_file():
            return cand
    return None


def _get_local_version_from_pyproject() -> _Optional[str]:
    pyproject = _find_local_pyproject()
    if not pyproject:
        return None
    try:
        text = pyproject.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return None
    return _parse_project_version_from_pyproject_toml(text)


def _version_lt(a: str, b: str) -> bool:
    """a < b ? 优先用 packaging.version，缺失则降级为数字段比较。"""
    try:
        from packaging.version import Version  # type: ignore
        return Version(a) < Version(b)
    except Exception:
        def nums(v: str) -> list[int]:
            v = v.split("+", 1)[0].split("-", 1)[0]
            parts = v.split(".")
            out: list[int] = []
            for p in parts:
                try:
                    out.append(int(p))
                except Exception:
                    out.append(0)
            return out

        na, nb = nums(a), nums(b)
        n = max(len(na), len(nb))
        na += [0] * (n - len(na))
        nb += [0] * (n - len(nb))
        return na < nb


def check_tool_update_against_github_raw(
    dist_name: str = "box",
    *,
    url: str = REMOTE_PYPROJECT_URL,
    timeout_sec: float = 5.0,
) -> VersionCheckResult:
    """
    读取 GitHub raw 的 pyproject.toml，提取其中的 version，并与本地已安装版本对比。

    - dist_name: 本地 distribution 名称（默认 box）
    - url: 远端 pyproject.toml 的 raw 地址
    """
    installed = _get_installed_version(dist_name)
    installed_from = "dist"

    # 源码方式运行（例如 `python3 src/_share/tool_spec.py`）时，本机未必安装了 dist。
    # 这时尝试从仓库内的 pyproject.toml 读取本地版本，避免永远提示“未安装”。
    if installed is None:
        local_v = _get_local_version_from_pyproject()
        if local_v:
            installed = local_v
            installed_from = "local"

    # cache bust：避免公司代理/CDN 缓存返回旧版本
    ts = int(time.time() * 1000)
    url_fetch = url + ("&" if "?" in url else "?") + f"ts={ts}"

    try:
        req = urllib.request.Request(
            url_fetch,
            headers={
                "Cache-Control": "no-cache",
                "Pragma": "no-cache",
                "User-Agent": f"{dist_name}/{installed or 'unknown'} (box-tools version-check)",
            },
        )
        with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
            text = resp.read().decode("utf-8", errors="replace")
        latest = _parse_project_version_from_pyproject_toml(text)
        if not latest:
            return VersionCheckResult(
                dist_name=dist_name,
                installed=installed,
                latest=None,
                has_update=False,
                note=f"读取远端成功，但未解析到 version 字段：{url}",
            )
    except Exception as e:
        return VersionCheckResult(
            dist_name=dist_name,
            installed=installed,
            latest=None,
            has_update=False,
            note=f"读取远端版本失败：{e}",
        )

    if installed is None:
        return VersionCheckResult(
            dist_name=dist_name,
            installed=None,
            latest=latest,
            has_update=False,
            note=f"本机未安装 {dist_name} 且未找到本地 pyproject.toml（无法对比）。",
        )

    has_update = _version_lt(installed, latest)
    return VersionCheckResult(
        dist_name=dist_name,
        installed=installed,
        latest=latest,
        has_update=has_update,
        note=f"ok (installed_from={installed_from})",
    )

def run_version_check(
    *,
    dist_name: str = "box",
    url: str = REMOTE_PYPROJECT_URL,
    timeout_sec: float = 5.0,
    print_fn: Callable[[str], None] = print,
) -> VersionCheckResult:
    """
    供其它工具集调用的版本检查入口：
    - 读取 GitHub raw 的 pyproject.toml version
    - 对比本地已安装版本
    - 如有新版本，只提示，不自动更新
    """
    r = check_tool_update_against_github_raw(dist_name, url=url, timeout_sec=timeout_sec)

    if r.installed is None:
        print_fn(f"ℹ️ {r.note}")
        return r

    if r.latest is None:
        print_fn(f"ℹ️ 无法获取最新版本：{r.note}")
        return r

    # 关键：无论是否有更新，都先把本地/远端版本打印出来，避免黑盒。
    print_fn(f"ℹ️ 当前版本：{r.installed}；远端最新：{r.latest}")

    if not r.has_update:
        # 区分“本地==远端”和“本地>远端”
        if _version_lt(r.latest, r.installed):
            print_fn("✅ 本地版本高于远端（可能是未发布/未合并到 master 的版本）。")
        else:
            print_fn("✅ 已是最新版本。")
        return r

    # 有更新：同时给出两种升级方式
    print_fn(f"⚠️ 发现新版本：{r.dist_name} {r.latest}（当前 {r.installed}）")
    print_fn("建议升级（两种方式任选其一）：")
    print_fn('  1) pipx install --force "git+https://github.com/flywithbug/tools.git"')
    print_fn("  2) box update")

    return r


if __name__ == "__main__":
    run_version_check()
