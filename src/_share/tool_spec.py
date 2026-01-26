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
