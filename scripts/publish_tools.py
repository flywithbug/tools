#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import argparse
import ast
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Set


REPO_ROOT = Path.cwd()
SRC_DIR = REPO_ROOT / "src"
TEMP_MD = REPO_ROOT / "temp.md"
README_MD = REPO_ROOT / "README.md"
PYPROJECT = REPO_ROOT / "pyproject.toml"


# -------------------------
# Data models
# -------------------------

@dataclass(frozen=True)
class Tool:
    py_path: Path               # src/.../*.py (absolute)
    md_path: Path               # docs md (absolute) - prefer BOX_TOOL["docs"] else README.md in same folder
    rel_py: str                 # relative path for display/link
    rel_md: str                 # relative path for link

    dir_key: str                # folder grouping key (relative to src)
    sort_key: Tuple[str, str]   # (dir_key, stem)

    id: str
    name: str
    category: str
    summary: str
    usage: List[str]
    options: List[Dict[str, str]]
    examples: List[Dict[str, str]]

    module: str                 # e.g. box_tools.flutter.pub_publish.tool OR box.cli
    entrypoint: str             # e.g. box_tools.flutter.pub_publish.tool:main

    extra_meta: Dict[str, Any]  # 原始 BOX_TOOL meta（用于 deps/docs 等扩展）


# -------------------------
# Extraction: BOX_TOOL
# -------------------------

def iter_py_files() -> List[Path]:
    if not SRC_DIR.exists():
        raise SystemExit("未找到 src/ 目录，请在仓库根目录执行。")
    return [p for p in SRC_DIR.rglob("*.py") if p.is_file()]


def extract_box_tool_literal(py_file: Path) -> Optional[Dict[str, Any]]:
    """
    抽取形如 BOX_TOOL = {...} 的 dict 字面量（不 import，避免副作用）。
    需要 BOX_TOOL 是 ast.literal_eval 可解析的 dict。
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


def module_from_py_path(py_file: Path) -> str:
    # src/box/cli.py -> box.cli
    # src/box_tools/flutter/pub_publish/tool.py -> box_tools.flutter.pub_publish.tool
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
    """
    分类按目录层级（相对 src）：
      - src/box/cli.py -> "box"
      - src/box_tools/flutter/pub_publish/tool.py -> "box_tools/flutter/pub_publish"
    """
    rel = py_file.relative_to(SRC_DIR)
    parent = rel.parent.as_posix()
    return parent if parent else "."


def make_group_title(dir_key: str) -> str:
    if dir_key == "box":
        return "box（工具集管理）"

    parts = dir_key.split("/")
    if parts and parts[0] == "box_tools":
        rest = parts[1:]
        if rest:
            return "/".join(rest)
        return "box_tools"
    return dir_key


def _resolve_docs_md(py_file: Path, meta: Dict[str, Any]) -> Path:
    """
    文档路径规则：
    - 优先 BOX_TOOL["docs"]（相对仓库根目录或绝对路径）
    - 否则同目录 README.md
    """
    docs = meta.get("docs")
    if isinstance(docs, str) and docs.strip():
        p = Path(docs.strip())
        if not p.is_absolute():
            p = REPO_ROOT / p
        return p

    # 默认：同目录 README.md
    return py_file.parent / "README.md"


def _rel_to_repo(p: Path) -> str:
    if p.is_absolute():
        return p.relative_to(REPO_ROOT).as_posix()
    return (REPO_ROOT / p).relative_to(REPO_ROOT).as_posix()


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
    # 以 name=box 或 module=box.cli / box.* 作为 box 识别规则（兼容你新结构）
    if t.name.strip().lower() == "box":
        return True
    return t.module == "box.cli" or t.module.startswith("box.")


def collect_tools() -> List[Tool]:
    tools: List[Tool] = []
    for py in iter_py_files():
        meta = extract_box_tool_literal(py)
        if not meta:
            continue
        t = build_tool(py, meta)
        if t:
            tools.append(t)

    # box 最上，其余按 (folder, filename)
    tools.sort(key=lambda t: (0, "") if _is_box_tool(t) else (1, t.sort_key[0].lower(), t.sort_key[1].lower()))
    return tools


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
                doc_part = f"（[文档]({t.rel_md})）"
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
                out.append(f"- **{t.name}**：[{t.rel_md}]({t.rel_md})\n")
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
        out.append(f"[{t.rel_md}]({t.rel_md})\n\n")
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
# pyproject.toml patch
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
    替换后强制以一个空行结束（\n\n），避免粘连下一段。
    """
    pattern = re.compile(rf"(?ms)^\[{re.escape(header)}\]\s*\n.*?(?=^\[|\Z)")
    new_block = f"[{header}]\n" + "\n".join(body_lines).rstrip() + "\n\n"

    m = pattern.search(text)
    if m:
        return text[:m.start()] + new_block + text[m.end():]
    return text.rstrip() + "\n\n" + new_block


def replace_wheel_packages_line(text: str, packages: List[str]) -> str:
    """
    仅替换 [tool.hatch.build.targets.wheel] table 内的 packages = [...] 行。
    没有 packages 行则追加一行；没有 table 则追加整个 table。
    """
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

# 对“常用依赖”提供默认版本约束（可按你需要扩展）
_DEFAULT_DEP_SPECS = {
    "openai": "openai>=1.0.0",
    "PyYAML": "PyYAML>=6.0",
}

_DEP_BASE_RE = re.compile(r"^\s*([A-Za-z0-9_.\-]+)")

def _dep_base(dep: str) -> str:
    """
    从依赖字符串里抽 base name：
      - "PyYAML>=6.0" -> "PyYAML"
      - "openai" -> "openai"
      - "tiktoken==0.7.0" -> "tiktoken"
    """
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
    """
    解析 import / from import，提取非 stdlib 的顶层模块名，再映射到 pip 包名。
    """
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
    """
    依赖来源：
    1) BOX_TOOL 显式：dependencies/depends/deps/requires
    2) import 推断（兜底）
    合并去重：显式优先、顺序稳定。
    """
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
    """
    ✅ 依赖“自动增减”：
    - desired_raw 是从工具收集到的依赖名（可能无版本约束）
    - 从现有 dependencies 中保留已存在的版本约束（若 base name 匹配）
    - 对于新增依赖，若在 _DEFAULT_DEP_SPECS 里则用默认约束，否则用裸包名
    - 移除不在 desired 里的依赖
    - 去重（按 base name）
    返回：(新文本, 最终依赖列表)
    """
    project_block = _read_project_block(text)
    if project_block is None:
        # 没有 [project]：创建最小 project，并写入 dependencies
        # 注意：name/version/requires-python 由你项目自己维护，此处不乱补
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
        # 如果重复，保留第一个（更稳定）
        if base not in existing_by_base:
            existing_by_base[base] = d

    desired_bases: List[str] = []
    seen: Set[str] = set()
    for raw in desired_raw:
        base = _dep_base(raw)
        if not base or base in seen:
            continue
        seen.add(base)
        desired_bases.append(base)

    final_deps: List[str] = []
    for base in desired_bases:
        # 先保留现有 spec（如果有）
        if base in existing_by_base:
            final_deps.append(existing_by_base[base])
        else:
            # 否则用默认 spec 或裸包名
            final_deps.append(_DEFAULT_DEP_SPECS.get(base, base))

    # 写回
    # 只改 dependencies 行，其余 [project] 字段不动
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
    """
    - box 命令保持为 "box"
    - 其他命令全部加 box_ 前缀
    - 自动增减：以 tools 列表为准
    - box 永远排第一
    """
    items: List[Tuple[str, str]] = []
    for t in tools:
        cmd = _command_with_prefix(t.name)
        items.append((cmd, t.entrypoint))

    # 排序：box 最上，然后按 cmd 排
    items.sort(key=lambda kv: (0, "") if kv[0] == "box" else (1, kv[0].lower()))
    return items


def update_project_scripts(text: str, scripts: List[Tuple[str, str]]) -> str:
    lines = [f'{cmd} = "{ep}"' for cmd, ep in scripts]
    # 强制 box 第一：build_scripts 已保证
    return replace_table_block(text, "project.scripts", lines)


# -------------------------
# wheel packages: 自动增减（按 src 下实际包）
# -------------------------

def collect_src_packages() -> List[str]:
    """
    自动扫描 src/ 下的顶层包（含 __init__.py），生成 wheel packages 列表：["src/box", "src/box_tools", ...]
    并保证 src/box 永远在最前。
    """
    if not SRC_DIR.exists():
        return []
    pkgs: List[str] = []
    for p in SRC_DIR.iterdir():
        if p.is_dir() and (p / "__init__.py").exists():
            pkgs.append(f"src/{p.name}")

    pkgs = sorted(pkgs, key=lambda s: (0, "") if s.lower() == "src/box" else (1, s.lower()))
    return pkgs


# -------------------------
# main update
# -------------------------

def update_pyproject(tools: List[Tool], do_bump: bool) -> Tuple[str, int, List[str], List[str]]:
    if not PYPROJECT.exists():
        raise SystemExit("未找到 pyproject.toml，请在仓库根目录执行。")

    original = PYPROJECT.read_text(encoding="utf-8", errors="ignore")
    text = original
    new_version = ""

    if do_bump:
        text, new_version = bump_patch_version(text)

    # 3) scripts：自动增减 + box 置顶 + 其余加 box_ 前缀
    scripts = build_scripts(tools)
    text = update_project_scripts(text, scripts)

    # 4) wheel packages：按 src 下实际包自动增减
    wheel_pkgs = collect_src_packages()
    text = replace_wheel_packages_line(text, wheel_pkgs)

    # 2) dependencies：自动增减（exact）
    deps_desired = collect_tool_dependencies(tools)
    text, final_deps = ensure_project_dependencies_exact(text, deps_desired)

    if text != original:
        PYPROJECT.write_text(text, encoding="utf-8")

    return new_version, len(scripts), wheel_pkgs, final_deps


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-readme", action="store_true", help="不生成 README.md")
    ap.add_argument("--no-toml", action="store_true", help="不更新 pyproject.toml")
    ap.add_argument("--no-bump", action="store_true", help="不升级 version（仍会更新 scripts / wheel packages / dependencies）")
    args = ap.parse_args()

    if not TEMP_MD.exists():
        raise SystemExit("未找到 temp.md（需要放在仓库根目录作为 README 文件头）。")
    if not SRC_DIR.exists():
        raise SystemExit("未找到 src/ 目录，请在仓库根目录执行。")

    tools = collect_tools()
    if not tools:
        raise SystemExit("未找到任何包含 BOX_TOOL 的工具脚本。")

    # 5) README 汇总自动增减
    if not args.no_readme:
        header = TEMP_MD.read_text(encoding="utf-8", errors="ignore")
        readme = render_readme(header, tools)
        README_MD.write_text(readme, encoding="utf-8")
        print(f"[ok] README.md 已生成：{README_MD}")

    if not args.no_toml:
        new_version, n_scripts, wheel_pkgs, final_deps = update_pyproject(tools, do_bump=not args.no_bump)
        if new_version:
            print(f"[ok] pyproject.toml version -> {new_version}")
        print(f"[ok] [project.scripts] -> {n_scripts} 项（box 置顶，其余自动加 box_ 前缀）")
        print(f"[ok] wheel packages -> {wheel_pkgs}")
        print(f"[ok] project.dependencies -> {final_deps}")

    print("Done.")


if __name__ == "__main__":
    main()
