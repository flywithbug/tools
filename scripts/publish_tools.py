#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
发布检查/编排脚本（新项目版 + 内容检查 + 自动提交）

功能：
1) version patch 自增（可选）
2) [project].dependencies 自动增减（基于工具显式依赖 + import 推断）
3) [project.scripts] 自动增减：
   - box 命令永远置顶
   - 其他命令统一加 box_ 前缀
4) [tool.hatch.build.targets.wheel].packages 自动增减（按 src 下实际包）
5) README.md 汇总自动增减（基于 BOX_TOOL 元数据）
6) tests/ 单元测试骨架自动生成 + pyproject pytest 配置自动维护（可选）
7) ✅ 内容检查（严格）：发现问题直接抛出
8) ✅ 自动提交：检查通过且有变更时自动 git add/commit

硬性约定（强校验）：
- 工具入口文件必须命名为 tool.py
- tool.py 必须包含 BOX_TOOL（可 ast.literal_eval 的 dict）
- 除 tool.py 之外，任何 .py 文件不得包含 BOX_TOOL（否则判定为结构违规）
- README 汇总的文档链接统一显示为 [README.md](path)（不显示冗长路径作为文本）

内容检查（新增，严格模式）：
- 任意工具的 docs 文件不存在 -> 报错退出
- 任意工具 BOX_TOOL 缺少关键字段（id/name/category/summary）-> 报错退出
- README.md / pyproject.toml 若声明要生成但没生成成功 -> 报错退出
"""

from __future__ import annotations

import argparse
import ast
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Set


REPO_ROOT = Path.cwd()
SRC_DIR = REPO_ROOT / "src"
TEMP_MD = REPO_ROOT / "temp.md"
README_MD = REPO_ROOT / "README.md"
PYPROJECT = REPO_ROOT / "pyproject.toml"
TESTS_DIR = REPO_ROOT / "tests"


# -------------------------
# Data models
# -------------------------

@dataclass(frozen=True)
class Tool:
    py_path: Path
    md_path: Path
    rel_py: str
    rel_md: str

    dir_key: str
    sort_key: Tuple[str, str]

    id: str
    name: str
    category: str
    summary: str
    usage: List[str]
    options: List[Dict[str, str]]
    examples: List[Dict[str, str]]

    module: str
    entrypoint: str

    extra_meta: Dict[str, Any]


# -------------------------
# Git helpers (新增)
# -------------------------

def _has_cmd(cmd: str) -> bool:
    from shutil import which
    return which(cmd) is not None


def is_git_repo(cwd: Path) -> bool:
    if not _has_cmd("git"):
        return False
    try:
        p = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            check=True,
        )
        return p.stdout.strip().lower() == "true"
    except Exception:
        return False


def run_cmd(cmd: List[str], *, dry_run: bool = False) -> subprocess.CompletedProcess[str] | None:
    if dry_run:
        print("🧪 DRY-RUN:", " ".join(cmd))
        return None
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        raise SystemExit(
            "❌ 执行命令失败：{}\nexit code: {}\nstdout:\n{}\nstderr:\n{}\n".format(
                " ".join(cmd), p.returncode, p.stdout, p.stderr
            )
        )
    return p


def git_changed_files(*, dry_run: bool = False) -> List[str]:
    """
    返回所有变更文件（含 staged/unstaged/untracked）。
    """
    if dry_run:
        print("🧪 DRY-RUN: git status --porcelain")
        return []

    p = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True)
    lines = (p.stdout or "").splitlines()

    changed: List[str] = []
    for line in lines:
        if len(line) < 4:
            continue
        path_part = line[3:].strip()
        # rename: old -> new
        if " -> " in path_part:
            path_part = path_part.split(" -> ", 1)[1].strip()
        if path_part.startswith('"') and path_part.endswith('"'):
            path_part = path_part[1:-1]
        changed.append(path_part)

    # 去重保持稳定顺序
    seen: Set[str] = set()
    out: List[str] = []
    for f in changed:
        if f not in seen:
            seen.add(f)
            out.append(f)
    return out


def git_commit_changed_files(
        *,
        message: str,
        dry_run: bool = False,
) -> None:
    files = git_changed_files(dry_run=dry_run)
    if not files:
        print("ℹ️ git 工作区无变更，无需提交")
        return

    print("📝 git add ...")
    run_cmd(["git", "add", *files], dry_run=dry_run)

    print("📝 git commit ...")
    run_cmd(["git", "commit", "-m", message], dry_run=dry_run)

    print(f"✅ 已自动提交：{message}")
    print("   提交文件：")
    for f in files:
        print(f"   - {f}")


# -------------------------
# File discovery & BOX_TOOL extraction
# -------------------------

def iter_tool_entry_files() -> List[Path]:
    """只扫描入口文件 tool.py（避免误扫 core.py/utils.py）。"""
    if not SRC_DIR.exists():
        raise SystemExit("未找到 src/ 目录，请在仓库根目录执行。")
    return sorted([p for p in SRC_DIR.rglob("tool.py") if p.is_file()], key=lambda p: p.as_posix().lower())


def iter_all_py_files() -> List[Path]:
    if not SRC_DIR.exists():
        raise SystemExit("未找到 src/ 目录，请在仓库根目录执行。")
    return sorted([p for p in SRC_DIR.rglob("*.py") if p.is_file()], key=lambda p: p.as_posix().lower())


def extract_box_tool_literal(py_file: Path) -> Optional[Dict[str, Any]]:
    """
    抽取形如 BOX_TOOL = {...} 的 dict 字面量（不 import，避免副作用）。
    要求 BOX_TOOL 能被 ast.literal_eval 解析。
    """
    try:
        src = py_file.read_text(encoding="utf-8", errors="ignore")
        tree = ast.parse(src)
    except Exception:
        return None

    for node in tree.body:
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == "BOX_TOOL":
                    try:
                        v = ast.literal_eval(node.value)
                    except Exception:
                        return None
                    return v if isinstance(v, dict) else None
    return None


def validate_structure_or_exit() -> None:
    """
    强校验：
    1) 任何非 tool.py 的文件如果包含 BOX_TOOL -> 报错退出
    2) 任何 tool.py 如果不包含 BOX_TOOL -> 报错退出
    """
    entry_files = set(iter_tool_entry_files())
    offenders_has_box_tool: List[Path] = []
    offenders_missing_box_tool: List[Path] = []

    for py in iter_all_py_files():
        meta = extract_box_tool_literal(py)
        if py.name == "tool.py":
            if meta is None:
                offenders_missing_box_tool.append(py)
        else:
            if meta is not None:
                offenders_has_box_tool.append(py)

    if offenders_has_box_tool or offenders_missing_box_tool:
        print("❌ 工具结构校验失败：")
        if offenders_has_box_tool:
            print("\n[发现 BOX_TOOL 但文件名不是 tool.py]（请重命名为 tool.py 或移除 BOX_TOOL）")
            for p in offenders_has_box_tool:
                print(f"  - {p.relative_to(REPO_ROOT).as_posix()}")
        if offenders_missing_box_tool:
            print("\n[文件名是 tool.py 但没有 BOX_TOOL]（请补充 BOX_TOOL 元数据）")
            for p in offenders_missing_box_tool:
                print(f"  - {p.relative_to(REPO_ROOT).as_posix()}")
        raise SystemExit(2)

    if not entry_files:
        print("❌ 未找到任何 tool.py（至少应该有 src/box/tool.py）。")
        raise SystemExit(2)


def module_from_py_path(py_file: Path) -> str:
    rel = py_file.relative_to(SRC_DIR).with_suffix("")
    return ".".join(rel.parts)


def norm_str(v: Any) -> str:
    return str(v).strip() if v is not None else ""


def norm_list_str(v: Any) -> List[str]:
    if v is None:
        return []
    if isinstance(v, (list, tuple)):
        out = []
        for x in v:
            s = str(x).strip()
            if s:
                out.append(s)
        return out
    s = str(v).strip()
    return [s] if s else []


def norm_list_dict(v: Any) -> List[Dict[str, str]]:
    if v is None:
        return []
    if not isinstance(v, (list, tuple)):
        return []
    out: List[Dict[str, str]] = []
    for item in v:
        if isinstance(item, dict):
            out.append({str(k): str(val) for k, val in item.items()})
    return out


def dir_key_from_py(py_file: Path) -> str:
    rel = py_file.relative_to(SRC_DIR)
    parent = rel.parent.as_posix()
    return parent if parent else "."


def make_group_title(dir_key: str) -> str:
    if dir_key == "box":
        return "box（工具集管理）"
    parts = dir_key.split("/")
    if parts and parts[0] == "box_tools":
        rest = parts[1:]
        return "/".join(rest) if rest else "box_tools"
    return dir_key


def _resolve_docs_md(py_file: Path, meta: Dict[str, Any]) -> Path:
    """
    文档路径规则：
    - 优先 BOX_TOOL["docs"]
      - 若为绝对路径：直接用
      - 若为相对路径：按“工具目录”相对（README.md）
    - 否则同目录 README.md
    """
    docs = meta.get("docs")
    if isinstance(docs, str) and docs.strip():
        raw = docs.strip()
        p = Path(raw)
        if p.is_absolute():
            return p
        return py_file.parent / raw
    return py_file.parent / "README.md"


def _rel_to_repo(p: Path) -> str:
    return p.relative_to(REPO_ROOT).as_posix()


def build_tool(py_file: Path, meta: Dict[str, Any]) -> Optional[Tool]:
    name = norm_str(meta.get("name") or meta.get("cmd") or "")
    if not name:
        return None

    tool_id = norm_str(meta.get("id"))
    category = norm_str(meta.get("category") or "")
    summary = norm_str(meta.get("summary") or meta.get("desc") or meta.get("description") or "")

    usage = norm_list_str(meta.get("usage"))
    options = norm_list_dict(meta.get("options"))
    examples = norm_list_dict(meta.get("examples"))

    md_file = _resolve_docs_md(py_file, meta)

    rel_py = py_file.relative_to(REPO_ROOT).as_posix()
    rel_md = _rel_to_repo(md_file)

    dir_key = dir_key_from_py(py_file)
    stem = py_file.stem
    module = module_from_py_path(py_file)
    entrypoint = f"{module}:main"

    return Tool(
        py_path=py_file,
        md_path=md_file,
        rel_py=rel_py,
        rel_md=rel_md,
        dir_key=dir_key,
        sort_key=(dir_key, stem),
        id=tool_id,
        name=name,
        category=category,
        summary=summary,
        usage=usage,
        options=options,
        examples=examples,
        module=module,
        entrypoint=entrypoint,
        extra_meta=meta,
    )


def _is_box_tool(t: Tool) -> bool:
    if t.name.strip().lower() == "box":
        return True
    return t.module == "box.tool" or t.module.startswith("box.")


def collect_tools() -> List[Tool]:
    tools: List[Tool] = []
    for py in iter_tool_entry_files():
        meta = extract_box_tool_literal(py)
        if not meta:
            continue
        t = build_tool(py, meta)
        if t:
            tools.append(t)

    tools.sort(key=lambda t: (0, "") if _is_box_tool(t) else (1, t.sort_key[0].lower(), t.sort_key[1].lower()))
    return tools


# -------------------------
# 内容检查（新增，严格）
# -------------------------

def validate_tools_content_or_exit(tools: List[Tool]) -> None:
    """
    严格内容检查：
    - docs 文件必须存在
    - BOX_TOOL 必填字段必须非空（id/name/category/summary）
    """
    problems: List[str] = []

    for t in tools:
        if not t.id:
            problems.append(f"[{t.rel_py}] BOX_TOOL.id 为空")
        if not t.name:
            problems.append(f"[{t.rel_py}] BOX_TOOL.name 为空")
        if not t.category:
            problems.append(f"[{t.rel_py}] BOX_TOOL.category 为空")
        if not t.summary:
            problems.append(f"[{t.rel_py}] BOX_TOOL.summary 为空")

        if not t.md_path.exists():
            problems.append(f"[{t.rel_py}] 文档缺失：{t.rel_md}（BOX_TOOL['docs'] 或默认 README.md）")

    if problems:
        print("❌ 内容检查失败：")
        for p in problems:
            print(f"  - {p}")
        raise SystemExit(2)


def validate_outputs_or_exit(*, gen_readme: bool, gen_toml: bool) -> None:
    problems: List[str] = []
    if gen_readme and not README_MD.exists():
        problems.append("README.md 预期生成，但未找到 README.md")
    if gen_toml and not PYPROJECT.exists():
        problems.append("pyproject.toml 预期更新，但未找到 pyproject.toml")
    if problems:
        print("❌ 输出检查失败：")
        for p in problems:
            print(f"  - {p}")
        raise SystemExit(2)


# -------------------------
# README rendering
# -------------------------

def _slugify_anchor(s: str) -> str:
    s = (s or "").strip().lower()
    s = re.sub(r"[^a-z0-9_\-\/]+", "-", s)
    s = s.replace("/", "-")
    s = re.sub(r"-{2,}", "-", s).strip("-")
    return s or "section"


def tool_anchor_id(t: Tool) -> str:
    return _slugify_anchor(f"{t.dir_key}-{t.py_path.stem}")


def section_anchor_id(title: str) -> str:
    return _slugify_anchor(title)


def render_readme_toc(tools: List[Tool]) -> str:
    groups: Dict[str, List[Tool]] = {}
    for t in tools:
        groups.setdefault(t.dir_key, []).append(t)

    group_keys = sorted(groups.keys(), key=lambda k: (0, "") if k == "box" else (1, k.lower()))

    out: List[str] = []
    out.append("## 目录\n\n")
    out.append(f"- [工具总览](#{section_anchor_id('工具总览')})\n")
    out.append(f"- [工具集文档索引](#{section_anchor_id('工具集文档索引')})\n")

    for gk in group_keys:
        title = make_group_title(gk)
        out.append(f"- [{title}](#{section_anchor_id(title)})\n")
        for t in sorted(groups[gk], key=lambda x: (0, "") if _is_box_tool(x) else (1, x.py_path.stem.lower())):
            a = tool_anchor_id(t)
            out.append(f"  - [`{t.name}`](#{a})\n")

    out.append("\n---\n\n")
    return "".join(out)


def render_overview(tools: List[Tool]) -> str:
    groups: Dict[str, List[Tool]] = {}
    for t in tools:
        groups.setdefault(t.dir_key, []).append(t)

    group_keys = sorted(groups.keys(), key=lambda k: (0, "") if k == "box" else (1, k.lower()))

    out: List[str] = []
    out.append(f'<a id="{section_anchor_id("工具总览")}"></a>\n\n')
    out.append("## 工具总览\n\n")

    for gk in group_keys:
        title = make_group_title(gk)
        out.append(f"### {title}\n\n")

        group_tools_sorted = sorted(
            groups[gk],
            key=lambda t: (0, "") if _is_box_tool(t) else (1, t.py_path.stem.lower())
        )
        for t in group_tools_sorted:
            a = tool_anchor_id(t)
            s = t.summary or ""
            if t.md_path.exists():
                doc_part = f"（[README.md]({t.rel_md})）"
            else:
                doc_part = f"（文档缺失：`{t.rel_md}`）"

            if s:
                out.append(f"- **[`{t.name}`](#{a})**：{s}{doc_part}\n")
            else:
                out.append(f"- **[`{t.name}`](#{a})**{doc_part}\n")
        out.append("\n")

    out.append("---\n\n")
    return "".join(out)


def render_docs_index(tools: List[Tool]) -> str:
    groups: Dict[str, List[Tool]] = {}
    for t in tools:
        groups.setdefault(t.dir_key, []).append(t)

    group_keys = sorted(groups.keys(), key=lambda k: (0, "") if k == "box" else (1, k.lower()))

    out: List[str] = []
    out.append(f'<a id="{section_anchor_id("工具集文档索引")}"></a>\n\n')
    out.append("## 工具集文档索引\n\n")

    for gk in group_keys:
        title = make_group_title(gk)
        out.append(f"### {title}\n\n")

        for t in sorted(groups[gk], key=lambda x: (0, "") if _is_box_tool(x) else (1, x.py_path.stem.lower())):
            if t.md_path.exists():
                out.append(f"- **{t.name}**：[README.md]({t.rel_md})\n")
            else:
                out.append(f"- **{t.name}**：未找到文档 `{t.rel_md}`（请创建该文件或在 BOX_TOOL['docs'] 指定）\n")

        out.append("\n")

    out.append("---\n\n")
    return "".join(out)


def render_tool_detail(t: Tool) -> str:
    out: List[str] = []
    anchor = tool_anchor_id(t)
    out.append(f'<a id="{anchor}"></a>\n\n')

    if _is_box_tool(t):
        out.append("## box（工具集管理）\n\n")
    else:
        out.append(f"### {t.name}\n\n")

    if t.summary:
        out.append(f"**简介**：{t.summary}\n\n")

    out.append(f"**命令**：`{t.name}`\n\n")

    if t.usage:
        out.append("**用法**\n\n```bash\n")
        out.extend([u + "\n" for u in t.usage])
        out.append("```\n\n")

    if t.options:
        out.append("**参数说明**\n\n")
        for opt in t.options:
            flag = norm_str(opt.get("flag") or opt.get("name") or "")
            desc = norm_str(opt.get("desc") or opt.get("description") or "")
            if flag and desc:
                out.append(f"- `{flag}`：{desc}\n")
            elif flag:
                out.append(f"- `{flag}`\n")
            elif desc:
                out.append(f"- {desc}\n")
        out.append("\n")

    if t.examples:
        out.append("**示例**\n\n")
        for ex in t.examples:
            cmd = norm_str(ex.get("cmd") or ex.get("command") or "")
            desc = norm_str(ex.get("desc") or ex.get("description") or "")
            if cmd and desc:
                out.append(f"- `{cmd}`：{desc}\n")
            elif cmd:
                out.append(f"- `{cmd}`\n")
            elif desc:
                out.append(f"- {desc}\n")
        out.append("\n")

    out.append("**文档**\n\n")
    if t.md_path.exists():
        out.append(f"[README.md]({t.rel_md})\n\n")
    else:
        out.append(f"- 未找到文档：`{t.rel_md}`（请创建该文件）\n\n")

    out.append("---\n\n")
    return "".join(out)


def render_readme(temp_header: str, tools: List[Tool]) -> str:
    out: List[str] = []
    out.append(temp_header.rstrip() + "\n\n")
    out.append(render_readme_toc(tools))
    out.append(render_overview(tools))
    out.append(render_docs_index(tools))

    box_tools = [t for t in tools if _is_box_tool(t)]
    other_tools = [t for t in tools if t not in box_tools]

    for t in box_tools:
        out.append(render_tool_detail(t))

    groups: Dict[str, List[Tool]] = {}
    for t in other_tools:
        groups.setdefault(t.dir_key, []).append(t)

    group_keys = sorted(groups.keys(), key=lambda k: (1, k.lower()))
    for gk in group_keys:
        title = make_group_title(gk)
        out.append(f'<a id="{section_anchor_id(title)}"></a>\n\n')
        out.append(f"## {title}\n\n")
        for t in sorted(groups[gk], key=lambda x: x.py_path.stem.lower()):
            out.append(render_tool_detail(t))

    return "".join(out)


# -------------------------
# pyproject.toml patch helpers
# -------------------------

_VERSION_LINE_RE = re.compile(r'(?m)^version\s*=\s*"(\d+)\.(\d+)\.(\d+)"\s*$')


def bump_patch_version(text: str) -> Tuple[str, str]:
    m = _VERSION_LINE_RE.search(text)
    if not m:
        raise SystemExit('pyproject.toml 未找到 version = "x.y.z" 行，无法升级版本号。')
    major, minor, patch = map(int, m.groups())
    new_version = f"{major}.{minor}.{patch + 1}"
    new_text = _VERSION_LINE_RE.sub(f'version = "{new_version}"', text, count=1)
    return new_text, new_version


def replace_table_block(text: str, header: str, body_lines: List[str]) -> str:
    """
    替换一个 table 块：从 [header] 开始到下一个 [..] 或 EOF。
    """
    pattern = re.compile(rf"(?ms)^\[{re.escape(header)}\]\s*\n.*?(?=^\[|\Z)")
    new_block = f"[{header}]\n" + "\n".join(body_lines).rstrip() + "\n\n"

    m = pattern.search(text)
    if m:
        return text[:m.start()] + new_block + text[m.end():]
    return text.rstrip() + "\n\n" + new_block


def replace_wheel_packages_line(text: str, packages: List[str]) -> str:
    table_pat = re.compile(r"(?ms)^\[tool\.hatch\.build\.targets\.wheel\]\s*\n.*?(?=^\[|\Z)")
    m = table_pat.search(text)

    quoted = ", ".join([f'"{p}"' for p in packages])
    new_line = f"packages = [{quoted}]"

    if not m:
        block = "[tool.hatch.build.targets.wheel]\n" + new_line + "\n\n"
        return text.rstrip() + "\n\n" + block

    block = text[m.start():m.end()]
    if re.search(r"(?m)^packages\s*=\s*\[.*\]\s*$", block):
        block2 = re.sub(r"(?m)^packages\s*=\s*\[.*\]\s*$", new_line, block, count=1)
    else:
        block2 = block.rstrip() + "\n" + new_line + "\n"

    if not block2.endswith("\n\n"):
        block2 = block2.rstrip() + "\n\n"

    return text[:m.start()] + block2 + text[m.end():]


# -------------------------
# dependencies: collect + exact patch (增减)
# -------------------------

_STDLIB_NAMES: Set[str] = set(getattr(sys, "stdlib_module_names", set()))
_STDLIB_FALLBACK = {
    "argparse", "ast", "datetime", "os", "re", "subprocess", "pathlib",
    "typing", "dataclasses", "json", "time", "sys", "textwrap", "shlex",
    "collections", "itertools", "functools", "logging", "math", "random",
    "traceback", "inspect", "types", "enum", "copy", "hashlib", "base64",
    "threading", "multiprocessing", "signal", "tempfile", "contextlib",
}
_STDLIB_NAMES |= _STDLIB_FALLBACK

_IMPORT_TO_PIP = {
    "yaml": "PyYAML",
    "openai": "openai",
    "requests": "requests",
    "toml": "toml",
    "tomli": "tomli",
    "rich": "rich",
}

_DEFAULT_DEP_SPECS = {
    "openai": "openai>=1.0.0",
    "PyYAML": "PyYAML>=6.0",
}

_DEP_BASE_RE = re.compile(r"^\s*([A-Za-z0-9_.\-]+)")


def _dep_base(dep: str) -> str:
    m = _DEP_BASE_RE.match(dep.strip())
    return m.group(1) if m else dep.strip()


def _top_import_name(mod: str) -> str:
    return (mod or "").split(".", 1)[0].strip()


def _norm_dep_list(v: Any) -> List[str]:
    if v is None:
        return []
    if isinstance(v, str):
        s = v.strip()
        return [s] if s else []
    if isinstance(v, (list, tuple)):
        out: List[str] = []
        for x in v:
            if x is None:
                continue
            s = str(x).strip()
            if s:
                out.append(s)
        return out
    return []


def infer_deps_from_imports(py_file: Path) -> List[str]:
    try:
        src = py_file.read_text(encoding="utf-8", errors="ignore")
        tree = ast.parse(src)
    except Exception:
        return []

    imports: Set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                name = _top_import_name(alias.name)
                if name:
                    imports.add(name)
        elif isinstance(node, ast.ImportFrom):
            if node.level and node.level > 0:
                continue
            name = _top_import_name(node.module or "")
            if name:
                imports.add(name)

    project_top_pkgs: Set[str] = set()
    if SRC_DIR.exists():
        for p in SRC_DIR.iterdir():
            if p.is_dir() and (p / "__init__.py").exists():
                project_top_pkgs.add(p.name)

    out: List[str] = []
    seen: Set[str] = set()
    for name in sorted(imports):
        if name in _STDLIB_NAMES:
            continue
        if name in project_top_pkgs:
            continue
        pip_name = _IMPORT_TO_PIP.get(name, name)
        if pip_name not in seen:
            out.append(pip_name)
            seen.add(pip_name)
    return out


def collect_tool_dependencies(tools: List[Tool]) -> List[str]:
    merged: List[str] = []
    seen: Set[str] = set()

    for t in tools:
        meta = t.extra_meta or {}
        raw = (meta.get("dependencies") or meta.get("depends") or meta.get("deps") or meta.get("requires"))
        explicit = _norm_dep_list(raw)
        inferred = infer_deps_from_imports(t.py_path)

        for dep in explicit + inferred:
            if dep not in seen:
                merged.append(dep)
                seen.add(dep)

    return merged


def _read_project_block(text: str) -> Optional[str]:
    project_pat = re.compile(r"(?ms)^\[project\]\s*\n.*?(?=^\[|\Z)")
    m = project_pat.search(text)
    if not m:
        return None
    return text[m.start():m.end()]


def _replace_project_block(text: str, new_block: str) -> str:
    project_pat = re.compile(r"(?ms)^\[project\]\s*\n.*?(?=^\[|\Z)")
    m = project_pat.search(text)
    if not m:
        return text.rstrip() + "\n\n" + new_block.rstrip() + "\n\n"
    return text[:m.start()] + new_block.rstrip() + "\n\n" + text[m.end():]


def _parse_single_line_dependencies(project_block: str) -> List[str]:
    dep_pat = re.compile(r'(?m)^\s*dependencies\s*=\s*\[(?P<body>.*)\]\s*$')
    m = dep_pat.search(project_block)
    if not m:
        return []
    body = m.group("body")
    return [s.strip() for s in re.findall(r"""["']([^"']+)["']""", body) if s.strip()]


def _write_single_line_dependencies(project_block: str, deps: List[str]) -> str:
    dep_pat = re.compile(r'(?m)^\s*dependencies\s*=\s*\[(?P<body>.*)\]\s*$')
    deps_line = "dependencies = [" + ", ".join([f'"{d}"' for d in deps]) + "]"
    if dep_pat.search(project_block):
        block2 = dep_pat.sub(deps_line, project_block, count=1)
    else:
        block2 = project_block.rstrip() + "\n" + deps_line + "\n"
    if not block2.endswith("\n\n"):
        block2 = block2.rstrip() + "\n\n"
    return block2


def ensure_project_dependencies_exact(text: str, desired_raw: List[str]) -> Tuple[str, List[str]]:
    project_block = _read_project_block(text)
    if project_block is None:
        deps_bases = []
        seen = set()
        final_deps: List[str] = []
        for raw in desired_raw:
            base = _dep_base(raw)
            if base in seen:
                continue
            seen.add(base)
            deps_bases.append(base)
        for base in deps_bases:
            final_deps.append(_DEFAULT_DEP_SPECS.get(base, base))
        new_block = "[project]\n" + _write_single_line_dependencies("", final_deps).lstrip()
        return _replace_project_block(text, new_block), final_deps

    existing = _parse_single_line_dependencies(project_block)
    existing_by_base: Dict[str, str] = {}
    for d in existing:
        base = _dep_base(d)
        if base not in existing_by_base:
            existing_by_base[base] = d

    desired_bases: List[str] = []
    seen2: Set[str] = set()
    for raw in desired_raw:
        base = _dep_base(raw)
        if not base or base in seen2:
            continue
        seen2.add(base)
        desired_bases.append(base)

    final_deps: List[str] = []
    for base in desired_bases:
        if base in existing_by_base:
            final_deps.append(existing_by_base[base])
        else:
            final_deps.append(_DEFAULT_DEP_SPECS.get(base, base))

    block2 = _write_single_line_dependencies(project_block, final_deps)
    return _replace_project_block(text, block2), final_deps


# -------------------------
# scripts: 自动增减 + box 置顶 + box_ 前缀
# -------------------------

def _command_with_prefix(name: str) -> str:
    n = (name or "").strip()
    if not n:
        return n
    if n.lower() == "box":
        return "box"
    if n.startswith("box_"):
        return n
    return f"box_{n}"


def build_scripts(tools: List[Tool]) -> List[Tuple[str, str]]:
    items: List[Tuple[str, str]] = []
    for t in tools:
        cmd = _command_with_prefix(t.name)
        items.append((cmd, t.entrypoint))

    items.sort(key=lambda kv: (0, "") if kv[0] == "box" else (1, kv[0].lower()))
    return items


def update_project_scripts(text: str, scripts: List[Tuple[str, str]]) -> str:
    lines = [f'{cmd} = "{ep}"' for cmd, ep in scripts]
    return replace_table_block(text, "project.scripts", lines)


# -------------------------
# wheel packages
# -------------------------

def collect_src_packages() -> List[str]:
    if not SRC_DIR.exists():
        return []
    pkgs: List[str] = []
    for p in SRC_DIR.iterdir():
        if p.is_dir() and (p / "__init__.py").exists():
            pkgs.append(f"src/{p.name}")
    pkgs = sorted(pkgs, key=lambda s: (0, "") if s.lower() == "src/box" else (1, s.lower()))
    return pkgs


# -------------------------
# tests generation + pytest config
# -------------------------

def _snake(s: str) -> str:
    s = (s or "").strip().lower()
    s = re.sub(r"[^a-z0-9]+", "_", s).strip("_")
    return s or "tool"


def ensure_tests_skeleton(tools: List[Tool]) -> List[Path]:
    TESTS_DIR.mkdir(parents=True, exist_ok=True)
    written: List[Path] = []

    conftest = TESTS_DIR / "conftest.py"
    if not conftest.exists():
        conftest.write_text(
            "import sys\n"
            "from pathlib import Path\n\n"
            "import pytest\n\n\n"
            "REPO_ROOT = Path(__file__).resolve().parents[1]\n"
            "SRC_DIR = REPO_ROOT / 'src'\n"
            "if SRC_DIR.exists():\n"
            "    sys.path.insert(0, str(SRC_DIR))\n\n\n"
            "@pytest.fixture\n"
            "def chdir_tmp(tmp_path, monkeypatch):\n"
            "    \"\"\"切到临时目录执行（避免污染仓库）。\"\"\"\n"
            "    monkeypatch.chdir(tmp_path)\n"
            "    return tmp_path\n",
            encoding="utf-8",
        )
        written.append(conftest)

    known_templates: Dict[str, str] = {
        "box_pub_version": (
            "from pathlib import Path\n\n"
            "from box_tools.flutter.pub_version import tool as tool\n\n\n"
            "def test_patch_bump_no_git(chdir_tmp):\n"
            "    pubspec = Path('pubspec.yaml')\n"
            "    pubspec.write_text('name: demo\\nversion: 1.2.3+abc\\n', encoding='utf-8')\n"
            "    rc = tool.main(['patch', '--no-git', '--file', str(pubspec)])\n"
            "    assert rc == 0\n"
            "    assert 'version: 1.2.4+abc' in pubspec.read_text(encoding='utf-8')\n"
        ),
        "box_pub_upgrade": (
            "from box_tools.flutter.pub_upgrade import tool as tool\n\n\n"
            "def test_smoke_import_only():\n"
            "    assert hasattr(tool, 'main')\n"
        ),
    }

    for t in tools:
        cmd = _command_with_prefix(t.name)
        if cmd == "box":
            continue

        fname = f"test_{_snake(cmd)}.py"
        test_file = TESTS_DIR / fname
        if test_file.exists():
            continue

        if cmd in known_templates:
            content = known_templates[cmd]
        else:
            content = (
                "import importlib\n\n\n"
                "def test_smoke_import():\n"
                f"    mod = importlib.import_module('{t.module}')\n"
                "    assert hasattr(mod, 'main')\n"
            )

        test_file.write_text(content, encoding="utf-8")
        written.append(test_file)

    return written


def ensure_pytest_config(text: str) -> str:
    pytest_lines = [
        'testpaths = ["tests"]',
        'addopts = "-q"',
    ]
    return replace_table_block(text, "tool.pytest.ini_options", pytest_lines)


def ensure_dev_pytest_dependency(text: str) -> str:
    """
    维护：
    [project.optional-dependencies]
    dev = ["pytest>=8.0.0", ...]
    仅做字符串级增补（避免引入 TOML parser 依赖）。
    """
    block_pat = re.compile(r"(?ms)^\[project\.optional-dependencies\]\s*\n.*?(?=^\[|\Z)")
    m = block_pat.search(text)

    pytest_spec = "pytest>=8.0.0"

    if not m:
        block = (
            "[project.optional-dependencies]\n"
            f'dev = ["{pytest_spec}"]\n\n'
        )
        return text.rstrip() + "\n\n" + block

    block = text[m.start():m.end()]

    dev_line_pat = re.compile(r'(?m)^\s*dev\s*=\s*\[(?P<body>.*)\]\s*$')
    dm = dev_line_pat.search(block)

    if not dm:
        block2 = block.rstrip() + f'\ndev = ["{pytest_spec}"]\n\n'
        return text[:m.start()] + block2 + text[m.end():]

    body = dm.group("body")
    items = [s.strip() for s in re.findall(r"""["']([^"']+)["']""", body)]
    bases = {_dep_base(x) for x in items}
    if _dep_base(pytest_spec) in bases:
        return text

    items.append(pytest_spec)
    new_body = ", ".join([f'"{x}"' for x in items])
    block2 = dev_line_pat.sub(f"dev = [{new_body}]", block, count=1)

    if not block2.endswith("\n\n"):
        block2 = block2.rstrip() + "\n\n"

    return text[:m.start()] + block2 + text[m.end():]


# -------------------------
# update pyproject
# -------------------------

def update_pyproject(tools: List[Tool], do_bump: bool, ensure_tests: bool) -> Tuple[str, int, List[str], List[str], List[Path]]:
    if not PYPROJECT.exists():
        raise SystemExit("未找到 pyproject.toml，请在仓库根目录执行。")

    original = PYPROJECT.read_text(encoding="utf-8", errors="ignore")
    text = original
    new_version = ""

    if do_bump:
        text, new_version = bump_patch_version(text)

    scripts = build_scripts(tools)
    text = update_project_scripts(text, scripts)

    wheel_pkgs = collect_src_packages()
    text = replace_wheel_packages_line(text, wheel_pkgs)

    deps_desired = collect_tool_dependencies(tools)
    text, final_deps = ensure_project_dependencies_exact(text, deps_desired)

    written_tests: List[Path] = []
    if ensure_tests:
        written_tests = ensure_tests_skeleton(tools)
        text = ensure_dev_pytest_dependency(text)
        text = ensure_pytest_config(text)

    if text != original:
        PYPROJECT.write_text(text, encoding="utf-8")

    return new_version, len(scripts), wheel_pkgs, final_deps, written_tests


# -------------------------
# main
# -------------------------

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-readme", action="store_true", help="不生成 README.md")
    ap.add_argument("--no-toml", action="store_true", help="不更新 pyproject.toml")
    ap.add_argument("--no-bump", action="store_true", help="不升级 version（仍会更新 scripts / wheel packages / dependencies）")
    ap.add_argument("--no-tests", action="store_true", help="不生成 tests/ 与 pytest 配置")

    # ✅ 新增：检查/提交控制
    ap.add_argument("--no-check", action="store_true", help="跳过内容检查（默认会严格检查）")
    ap.add_argument("--no-git", action="store_true", help="不进行 git 自动提交")
    ap.add_argument("--commit-msg", default="", help="自定义提交信息（默认自动生成）")
    ap.add_argument("--dry-run", action="store_true", help="仅打印将执行的 git 命令（不实际提交）")

    args = ap.parse_args()

    if not TEMP_MD.exists():
        raise SystemExit("未找到 temp.md（需要放在仓库根目录作为 README 文件头）。")
    if not SRC_DIR.exists():
        raise SystemExit("未找到 src/ 目录，请在仓库根目录执行。")

    # 1) 结构强校验（原本就有）
    validate_structure_or_exit()

    # 2) 收集工具
    tools = collect_tools()
    if not tools:
        raise SystemExit("未找到任何包含 BOX_TOOL 的工具入口（tool.py）。")

    # 3) 严格内容检查（新增）
    if not args.no_check:
        validate_tools_content_or_exit(tools)

    # 4) 生成 README
    if not args.no_readme:
        header = TEMP_MD.read_text(encoding="utf-8", errors="ignore")
        readme = render_readme(header, tools)
        README_MD.write_text(readme, encoding="utf-8")
        print(f"[ok] README.md 已生成：{README_MD}")

    # 5) 更新 pyproject / tests
    new_version = ""
    if not args.no_toml:
        new_version, n_scripts, wheel_pkgs, final_deps, written_tests = update_pyproject(
            tools,
            do_bump=not args.no_bump,
            ensure_tests=not args.no_tests,
        )
        if new_version:
            print(f"[ok] pyproject.toml version -> {new_version}")
        print(f"[ok] [project.scripts] -> {n_scripts} 项（box 置顶，其余自动加 box_ 前缀）")
        print(f"[ok] wheel packages -> {wheel_pkgs}")
        print(f"[ok] project.dependencies -> {final_deps}")

        if not args.no_tests:
            if written_tests:
                rels = [p.relative_to(REPO_ROOT).as_posix() for p in written_tests]
                print(f"[ok] tests 已生成：{rels}")
            else:
                print("[ok] tests 已存在（未新增）")

    # 6) 输出检查（新增）
    if not args.no_check:
        validate_outputs_or_exit(gen_readme=not args.no_readme, gen_toml=not args.no_toml)

    # 7) 自动提交（新增）
    if args.no_git:
        print("ℹ️ 已跳过 git 自动提交（--no-git）")
        print("Done.")
        return

    if not is_git_repo(REPO_ROOT):
        print("ℹ️ 当前目录不是 git 仓库或未安装 git，跳过自动提交")
        print("Done.")
        return

    if not args.no_check:
        # ✅ 只有检查通过才允许提交
        pass

    default_msg = "chore: sync tools"
    if new_version:
        default_msg = f"chore: sync tools (version {new_version})"
    commit_msg = (args.commit_msg or "").strip() or default_msg

    # 有变更才提交
    files = git_changed_files(dry_run=args.dry_run)
    if not files:
        print("ℹ️ 无文件变更，跳过提交")
        print("Done.")
        return

    print("✅ 内容检查通过，准备自动提交...")
    git_commit_changed_files(message=commit_msg, dry_run=args.dry_run)

    print("Done.")


if __name__ == "__main__":
    main()
