from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import List, Tuple


# 仅解析最常见格式： "KEY" = "VALUE";
# 注释与更复杂语法（多行、转义等）后续再增强
LINE_RE = re.compile(r'^\s*"(?P<key>(?:\\.|[^"\\])*)"\s*=\s*"(?P<val>(?:\\.|[^"\\])*)"\s*;\s*$')


@dataclass
class StringsEntry:
    key: str
    value: str
    line_no: int


@dataclass
class StringsFile:
    path: Path
    entries: List[StringsEntry]
    duplicates: List[str]


def parse_strings_file(path: Path) -> StringsFile:
    entries: List[StringsEntry] = []
    seen = {}
    dups: List[str] = []

    if not path.exists():
        return StringsFile(path=path, entries=[], duplicates=[])

    lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    for i, raw in enumerate(lines, start=1):
        m = LINE_RE.match(raw)
        if not m:
            continue
        key = m.group("key")
        val = m.group("val")
        entries.append(StringsEntry(key=key, value=val, line_no=i))
        if key in seen and key not in dups:
            dups.append(key)
        seen[key] = val

    return StringsFile(path=path, entries=entries, duplicates=dups)


def write_strings_file_sorted_dedup(path: Path, items: List[Tuple[str, str]]) -> None:
    # 最小版：按 key 排序，后写回（不保留注释；后续增强“保留原注释/原顺序块”）
    items_sorted = sorted(items, key=lambda kv: kv[0])

    out_lines = []
    for k, v in items_sorted:
        out_lines.append(f"\"{k}\" = \"{v}\";")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(out_lines) + ("\n" if out_lines else ""), encoding="utf-8")
