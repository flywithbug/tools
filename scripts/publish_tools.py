from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


REPO_ROOT = Path.cwd()
TEMPLATE_MD = REPO_ROOT / "temp.md"
OUTPUT_README = REPO_ROOT / "README.md"

SRC_DIR = REPO_ROOT / "src"
DEFAULT_BOX_MD = REPO_ROOT / "src/box/box.md"


@dataclass(frozen=True)
class ToolDoc:
    name: str         # md 文件名 stem（工具名）
    rel_path: str     # 相对路径（README 链接用）
    title: str        # 文档标题（# Heading）
    summary: str      # 首段摘要（尽力提取）


_HEADING_RE = re.compile(r"^\s*#{1,6}\s+(.+?)\s*$")


def _strip_md_inline(text: str) -> str:
    """把一段 markdown 行内内容清理成更适合作为简介的纯文本（粗略版）。"""
    t = text.strip()
    # 去掉图片
    t = re.sub(r"!\[[^\]]*\]\([^)]+\)", "", t)
    # 链接 [text](url) -> text
    t = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", t)
    # 行内 code
    t = t.replace("`", "")
    # 压缩空白
    t = re.sub(r"\s+", " ", t).strip()
    return t


def extract_title_and_summary(md_path: Path) -> tuple[str, str]:
    """
    标题：第一个 markdown heading；没有则用文件名
    摘要：标题之后第一段非空正文（跳过 fenced code block / heading）
    """
    text = md_path.read_text(encoding="utf-8", errors="ignore")
    lines = text.splitlines()

    title: Optional[str] = None
    summary_lines: list[str] = []

    in_code_block = False
    seen_title = False

    for raw in lines:
        line = raw.rstrip("\n")

        # fenced code block toggle
        if re.match(r"^\s*```", line):
            in_code_block = not in_code_block
            continue
        if in_code_block:
            continue

        if not seen_title:
            m = _HEADING_RE.match(line)
            if m:
                title = m.group(1).strip()
                seen_title = True
                continue
            # 跳过开头空行
            if not line.strip():
                continue
            # 没有 heading：先继续扫描，最后会 fallback
            continue

        # 已找到标题：开始抓摘要
        if _HEADING_RE.match(line):
            if summary_lines:
                break
            continue

        if not line.strip():
            if summary_lines:
                break
            continue

        summary_lines.append(line.strip())
        if len(summary_lines) >= 2:
            break

    if title is None:
        title = md_path.stem

    summary = _strip_md_inline(" ".join(summary_lines))
    return title, summary


def scan_docs() -> list[ToolDoc]:
    if not SRC_DIR.exists():
        raise SystemExit("未找到 src/ 目录，请在仓库根目录执行。")

    md_files = sorted(SRC_DIR.rglob("*.md"))

    docs: list[ToolDoc] = []
    for p in md_files:
        rel = p.relative_to(REPO_ROOT).as_posix()
        name = p.stem
        title, summary = extract_title_and_summary(p)
        docs.append(ToolDoc(name=name, rel_path=rel, title=title, summary=summary))

    # box 排第一，其它按 name 排序
    docs.sort(key=lambda d: (0, "") if d.name.lower() == "box" else (1, d.name.lower()))
    return docs


def find_box_doc(docs: list[ToolDoc]) -> Optional[ToolDoc]:
    # 优先 src/box/box.md
    if DEFAULT_BOX_MD.exists():
        rel = DEFAULT_BOX_MD.relative_to(REPO_ROOT).as_posix()
        for d in docs:
            if d.rel_path == rel:
                return d
    # fallback：stem == box
    for d in docs:
        if d.name.lower() == "box":
            return d
    return None


def build_appendix(docs: list[ToolDoc]) -> str:
    box_doc = find_box_doc(docs)

    parts: list[str] = []
    parts.append("\n")  # 与 temp.md 分隔
    parts.append("## BOX_TOOL\n\n")
    if box_doc:
        parts.append(f"- 文档：[{box_doc.name}]({box_doc.rel_path})\n")
        if box_doc.summary:
            parts.append(f"- 简介：{box_doc.summary}\n")
    else:
        parts.append("- 未找到 `src/box/box.md`，请确认 BOX 文档位置。\n")

    parts.append("\n## 工具文档索引\n\n")
    for d in docs:
        line = f"- **[{d.name}]({d.rel_path})**"
        if d.summary:
            line += f"：{d.summary}"
        parts.append(line + "\n")

    return "".join(parts)


def main() -> None:
    if not (REPO_ROOT / "pyproject.toml").exists():
        raise SystemExit("请在仓库根目录执行（需要存在 pyproject.toml）。")

    if not TEMPLATE_MD.exists():
        raise SystemExit("未找到根目录 temp.md，请先创建该文件作为 README 模板。")

    template = TEMPLATE_MD.read_text(encoding="utf-8", errors="ignore").rstrip() + "\n"
    docs = scan_docs()
    appendix = build_appendix(docs)

    OUTPUT_README.write_text(template + appendix, encoding="utf-8")
    print(f"已生成：{OUTPUT_README}")


if __name__ == "__main__":
    main()
