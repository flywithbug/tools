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
    py_path: Path               # src/.../tool.py (absolute)
    md_path: Path               # docs md (absolute) - prefer BOX_TOOL["docs"] else same-name.md
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

    module: str                 # e.g. box_tools.flutter.pub_version
    entrypoint: str             # e.g. box_tools.flutter.pub_version:main

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
    # src/box_tools/flutter/pub_version.py -> box_tools.flutter.pub_version
    rel = py_file.relative_to(SRC_DIR).with_suffix("")
    return ".".join(rel.parts)


def same_name_md(py_file: Path) -> Path:
    # same folder, same stem, .md
    return py_file.with_suffix(".md")


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
    “根据文件夹和名字排序分类工具集”
    分类按目录层级（相对 src）：
      - box/box.py -> "box"
      - box_tools/flutter/pub_version.py -> "box_tools/flutter"
    """
    rel = py_file.relative_to(SRC_DIR)
    parent = rel.parent.as_posix()
    return parent if parent else "."


def make_group_title(dir_key: str, category: str) -> str:
    """
    README 的分组标题：优先用 folder 结构（满足“按文件夹分类”）。
    category 仅作为补充，不作为分组依据（避免目录与 category 不一致导致混乱）。
    """
    if dir_key == "box":
        return "box（工具集管理）"

    parts = dir_key.split("/")
    if parts and parts[0] == "box_tools":
        rest = parts[1:]  # 去掉 box_tools 前缀
        if rest:
            return "/".join(rest)
        return "box_tools"
    return dir_key


def _resolve_docs_md(py_file: Path, meta: Dict[str, Any]) -> Path:
    """
    文档路径规则：
    - 优先 BOX_TOOL["docs"]（相对仓库根目录或绝对路径）
    - 否则同名 md（同目录同 stem）
    """
    docs = meta.get("docs")
    if isinstance(docs, str) and docs.strip():
        p = Path(docs.strip())
        if not p.is_absolute():
            p = REPO_ROOT / p
        return p
    return same_name_md(py_file)


def _rel_to_repo(p: Path) -> str:
    """把绝对/相对 Path 都稳定转成相对 REPO_ROOT 的 posix 路径。"""
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


def collect_tools() -> List[Tool]:
    tools: List[Tool] = []
    for py in iter_py_files():
        meta = extract_box_tool_literal(py)
        if not meta:
            continue
        t = build_tool(py, meta)
        if t:
            tools.append(t)

    # 排序：box 最上，其余按 (folder, filename)
    def is_box(t: Tool) -> bool:
        if t.name.lower() == "box":
            return True
        rel = t.py_path.relative_to(SRC_DIR).as_posix().lower()
        return rel == "box/box.py"

    tools.sort(key=lambda t: (0, "") if is_box(t) else (1, t.sort_key[0].lower(), t.sort_key[1].lower()))
    return tools


# -------------------------
# README rendering
# -------------------------

def _slugify_anchor(s: str) -> str:
    """
    生成稳定、Markdown 友好的锚点 id
    - 小写
    - 非 [a-z0-9_-] 都替换成 -
    - 合并连续 -
    """
    s = (s or "").strip().lower()
    s = re.sub(r"[^a-z0-9_\-\/]+", "-", s)
    s = s.replace("/", "-")
    s = re.sub(r"-{2,}", "-", s).strip("-")
    return s or "tool"


def tool_anchor_id(t: Tool) -> str:
    # 用 dir_key + stem 保证唯一性（避免同名工具冲突）
    return _slugify_anchor(f"{t.dir_key}-{t.py_path.stem}")


def render_overview(tools: List[Tool]) -> str:
    """
    “工具总览”：按目录分组列出 tool name + summary，
    并为每个工具提供：
    - 跳转到 README 内工具详情的索引链接
    - 直达文档链接（若存在）
    """
    groups: Dict[str, List[Tool]] = {}
    for t in tools:
        groups.setdefault(t.dir_key, []).append(t)

    group_keys = sorted(groups.keys(), key=lambda k: (0, "") if k == "box" else (1, k.lower()))

    out: List[str] = []
    out.append("## 工具总览\n\n")
    for gk in group_keys:
        title = make_group_title(gk, "")
        out.append(f"### {title}\n\n")
        group_tools = groups[gk]
        group_tools_sorted = sorted(
            group_tools,
            key=lambda t: (0, "") if t.name.lower() == "box" else (1, t.py_path.stem.lower())
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
    """
    “工具集文档索引”：按目录分组列出 tool -> docs.md
    方便把 README 当作统一入口页使用。
    """
    groups: Dict[str, List[Tool]] = {}
    for t in tools:
        groups.setdefault(t.dir_key, []).append(t)

    group_keys = sorted(groups.keys(), key=lambda k: (0, "") if k == "box" else (1, k.lower()))

    out: List[str] = []
    out.append("## 工具集文档索引\n\n")
    for gk in group_keys:
        title = make_group_title(gk, "")
        out.append(f"### {title}\n\n")

        for t in sorted(groups[gk], key=lambda x: (0, "") if x.name.lower() == "box" else (1, x.py_path.stem.lower())):
            if t.md_path.exists():
                out.append(f"- **{t.name}**：[{t.rel_md}]({t.rel_md})\n")
            else:
                out.append(f"- **{t.name}**：未找到文档 `{t.rel_md}`（请创建该文件或在 BOX_TOOL['docs'] 指定）\n")

        out.append("\n")

    out.append("---\n\n")
    return "".join(out)


def render_tool_detail(t: Tool) -> str:
    """
    生成单个工具详情块（信息密度高但规整）。
    文档链接优先 BOX_TOOL["docs"]，否则同名 md。
    """
    out: List[str] = []

    # ✅ 显式锚点：让“索引”稳定跳转（不依赖 Markdown 渲染器的自动锚点规则）
    anchor = tool_anchor_id(t)
    out.append(f'<a id="{anchor}"></a>\n\n')

    if t.name.lower() == "box":
        out.append("## box（工具集管理）\n\n")
    else:
        out.append(f"### {t.name}\n\n")

    if t.summary:
        out.append(f"**简介**：{t.summary}\n\n")

    out.append(f"**命令**：`{t.name}`\n\n")

    if t.usage:
        out.append("**用法**\n\n")
        out.append("```bash\n")
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

    # ✅ 总览（带索引链接 + 文档链接）
    out.append(render_overview(tools))

    # ✅ 工具集文档索引（集中入口）
    out.append(render_docs_index(tools))

    box_tools = [
        t for t in tools
        if t.name.lower() == "box" or t.py_path.relative_to(SRC_DIR).as_posix().lower() == "box/box.py"
    ]
    other_tools = [t for t in tools if t not in box_tools]

    for t in box_tools:
        out.append(render_tool_detail(t))

    groups: Dict[str, List[Tool]] = {}
    for t in other_tools:
        groups.setdefault(t.dir_key, []).append(t)

    group_keys = sorted(groups.keys(), key=lambda k: (1, k.lower()))

    for gk in group_keys:
        title = make_group_title(gk, "")
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

    # ✅ 确保 wheel table 末尾有空行
    if not block2.endswith("\n\n"):
        block2 = block2.rstrip() + "\n\n"

    return text[:m.start()] + block2 + text[m.end():]


# -------------------------
# dependencies: collect + patch
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
                continue  # 相对导入视为项目内部
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


def _ensure_block_ends_with_blank_line(block: str) -> str:
    """
    ✅ 强制 table block 以 \n\n 结尾，避免下一个 [table] 粘上来导致 TOML 解析失败。
    """
    if block.endswith("\n\n"):
        return block
    return block.rstrip() + "\n\n"


def ensure_project_dependencies(text: str, add_deps: List[str]) -> str:
    """
    把 add_deps 合并进 [project].dependencies
    - 若 [project] 不存在：追加 [project] + dependencies，并以空行收尾
    - 若 dependencies 不存在：在 [project] block 末尾追加 dependencies，并以空行收尾
    - 若存在：合并后回写，并以空行收尾（关键修复点）
    """
    if not add_deps:
        return text

    project_pat = re.compile(r"(?ms)^\[project\]\s*\n.*?(?=^\[|\Z)")
    m = project_pat.search(text)
    if not m:
        deps_lines = ", ".join([f'"{d}"' for d in add_deps])
        block = "[project]\n" + f"dependencies = [{deps_lines}]\n\n"
        return text.rstrip() + "\n\n" + block

    block = text[m.start():m.end()]

    # 注意：这里只处理“单行 dependencies = [ ... ]”
    dep_pat = re.compile(r'(?m)^\s*dependencies\s*=\s*\[(?P<body>.*)\]\s*$')
    md = dep_pat.search(block)

    if md:
        body = md.group("body")
        existing = [s.strip() for s in re.findall(r"""["']([^"']+)["']""", body) if s.strip()]

        merged: List[str] = []
        seen: Set[str] = set()

        for d in existing:
            if d not in seen:
                merged.append(d)
                seen.add(d)
        for d in add_deps:
            if d not in seen:
                merged.append(d)
                seen.add(d)

        deps_lines = ", ".join([f'"{d}"' for d in merged])
        new_line = f"dependencies = [{deps_lines}]"
        block2 = dep_pat.sub(new_line, block, count=1)
        block2 = _ensure_block_ends_with_blank_line(block2)
        return text[:m.start()] + block2 + text[m.end():]

    # 没有 dependencies 行：追加到 [project] block 尾部
    deps_lines = ", ".join([f'"{d}"' for d in add_deps])
    new_line = f"dependencies = [{deps_lines}]"
    block2 = block.rstrip() + "\n" + new_line + "\n\n"
    block2 = _ensure_block_ends_with_blank_line(block2)
    return text[:m.start()] + block2 + text[m.end():]


def update_pyproject(tools: List[Tool], do_bump: bool) -> Tuple[str, int, List[str], List[str]]:
    if not PYPROJECT.exists():
        raise SystemExit("未找到 pyproject.toml，请在仓库根目录执行。")

    original = PYPROJECT.read_text(encoding="utf-8", errors="ignore")
    text = original
    new_version = ""

    if do_bump:
        text, new_version = bump_patch_version(text)

    # scripts：命令来自 BOX_TOOL.name，入口来自模块路径 :main
    scripts = [(t.name, t.entrypoint) for t in tools]
    scripts.sort(key=lambda kv: (0, "") if kv[0].lower() == "box" else (1, kv[0].lower()))
    script_lines = [f'{cmd} = "{ep}"' for cmd, ep in scripts]
    text = replace_table_block(text, "project.scripts", script_lines)

    # wheel packages：从工具模块顶层包名生成 src/<pkg>
    top_pkgs: Set[str] = set()
    for t in tools:
        top_pkgs.add(t.module.split(".", 1)[0])
    packages = [f"src/{p}" for p in sorted(top_pkgs, key=lambda x: x.lower())]
    packages.sort(key=lambda s: (0, "") if s.lower() == "src/box" else (1, s.lower()))
    text = replace_wheel_packages_line(text, packages)

    # project dependencies：合并工具依赖（✅ 修复：保证 [project] block 末尾有空行）
    deps_added = collect_tool_dependencies(tools)
    text = ensure_project_dependencies(text, deps_added)

    if text != original:
        PYPROJECT.write_text(text, encoding="utf-8")

    return new_version, len(scripts), packages, deps_added


# -------------------------
# main
# -------------------------

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

    if not args.no_readme:
        header = TEMP_MD.read_text(encoding="utf-8", errors="ignore")
        readme = render_readme(header, tools)
        README_MD.write_text(readme, encoding="utf-8")
        print(f"[ok] README.md 已生成：{README_MD}")

    if not args.no_toml:
        new_version, n_scripts, wheel_pkgs, deps_added = update_pyproject(tools, do_bump=not args.no_bump)
        if new_version:
            print(f"[ok] pyproject.toml version -> {new_version}")
        print(f"[ok] [project.scripts] -> {n_scripts} 项")
        print(f"[ok] wheel packages -> {wheel_pkgs}")
        if deps_added:
            print(f"[ok] project.dependencies merged -> {deps_added}")

    print("Done.")


if __name__ == "__main__":
    main()
