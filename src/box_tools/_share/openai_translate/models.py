from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Literal


from enum import Enum

class OpenAIModel(str, Enum):
    # 4.x / 4o
    GPT_4O = "gpt-4o"
    GPT_4O_MINI = "gpt-4o-mini"
    GPT_4_1 = "gpt-4.1"
    GPT_4_1_MINI = "gpt-4.1-mini"

    # 5.x
    GPT_5 = "gpt-5"
    GPT_5_CHAT = "gpt-5-chat-latest"          # ChatGPT 当前使用的 GPT-5 指针（偏“聊天最新”）
    GPT_5_MINI = "gpt-5-mini"
    GPT_5_NANO = "gpt-5-nano"

    # 5.2（当前主推）
    GPT_5_2 = "gpt-5.2"
    GPT_5_2_PRO = "gpt-5.2-pro"
    GPT_5_2_CHAT = "gpt-5.2-chat-latest"      # ChatGPT 当前使用的 GPT-5.2 指针（偏“聊天最新”）
    GPT_5_2_CODEX = "gpt-5.2-codex"            # 指南中提到的 coding/agentic 变体




# =========================
# File type
# =========================

class FileType(str, Enum):
    JSON = "json"
    STRINGS = "strings"


def detect_file_type(path: str) -> FileType:
    ext = Path(path).suffix.lower()
    if ext == ".json":
        return FileType.JSON
    if ext == ".strings":
        return FileType.STRINGS
    raise ValueError(f"Unsupported file type: {path}")


# =========================
# JSON helpers
# =========================

def _load_flat_json_map(path: str) -> Dict[str, str]:
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"JSON must be an object(map): {path}")
    for k, v in data.items():
        if not isinstance(k, str) or not isinstance(v, str):
            raise ValueError(f"Flat JSON required: key/value must be strings: {k}={type(v)}")
    return data


def _save_sorted_json(path: str, data: Dict[str, str]) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        # sort_keys=True：稳定输出
        json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")


# =========================
# .strings helpers
# =========================
# 读取时支持注释；写回时按你的需求分两种：
# - 源文件：保留注释 + 按前缀分组 + 组间空行
# - 目标文件：无注释、无分组、无空行（每条一行）

_STRINGS_PAIR_RE = re.compile(
    r'^\s*"(?P<key>(?:\\.|[^"\\])*)"\s*=\s*"(?P<val>(?:\\.|[^"\\])*)"\s*;\s*$'
)

def _unescape_strings(s: str) -> str:
    return (
        s.replace(r"\\", "\\")
        .replace(r"\"", "\"")
        .replace(r"\n", "\n")
        .replace(r"\t", "\t")
        .replace(r"\r", "\r")
    )

def _escape_strings(s: str) -> str:
    return (
        s.replace("\\", r"\\")
        .replace("\"", r"\"")
        .replace("\n", r"\n")
        .replace("\t", r"\t")
        .replace("\r", r"\r")
    )

def _key_prefix(key: str) -> str:
    # “前缀分组”：点号前；没有点号则归到空前缀组（会排在最前）
    if "." in key:
        return key.split(".", 1)[0]
    return ""


@dataclass
class StringsKV:
    key: str
    value: str
    leading_comments: List[str]  # 紧贴在上方的注释（行注释// 或块注释/* */ 原样行文本）


def _parse_strings_keep_comments(path: str) -> Tuple[List[str], List[StringsKV]]:
    """
    返回：
    - header_lines: 文件最前面的“游离注释/内容”（在第一个 key=value; 之前的注释/其它行）
    - pairs: 每条 key-value + 紧贴注释
    解析策略（简单但够用）：
    - // 行注释归入 pending_comments
    - /* */ 块注释整体按原行收集，归入 pending_comments
    - 遇到 "k"="v";：把 pending_comments 绑定到该条，然后清空
    - 遇到空行：会打断 pending_comments（把 pending_comments 释放到 header 或丢弃？）
      这里选择：在尚未遇到任何 pair 前，释放到 header；遇到 pair 后，空行会让 pending_comments 变成“无主注释”，也释放到 header（保持不丢内容）。
    """
    if not os.path.exists(path):
        return [], []

    with open(path, "r", encoding="utf-8") as f:
        lines = f.read().splitlines()

    header: List[str] = []
    pairs: List[StringsKV] = []

    pending_comments: List[str] = []
    in_block_comment = False
    block_buf: List[str] = []
    seen_any_pair = False

    def flush_pending_to_header():
        nonlocal pending_comments
        if pending_comments:
            header.extend(pending_comments)
            pending_comments = []

    def flush_block():
        nonlocal block_buf
        if block_buf:
            pending_comments.append("\n".join(block_buf))
            block_buf = []

    for line in lines:
        stripped = line.strip()

        if in_block_comment:
            block_buf.append(line)
            if "*/" in line:
                in_block_comment = False
                flush_block()
            continue

        if stripped.startswith("/*"):
            in_block_comment = True
            block_buf.append(line)
            if "*/" in line:
                in_block_comment = False
                flush_block()
            continue

        if stripped.startswith("//"):
            pending_comments.append(line)
            continue

        if stripped == "":
            # 空行打断“紧贴注释链”
            flush_pending_to_header()
            # 源文件排序时，我们会自行重排空行规则，所以这里不保留原空行
            continue

        m = _STRINGS_PAIR_RE.match(line)
        if m:
            seen_any_pair = True
            k = _unescape_strings(m.group("key"))
            v = _unescape_strings(m.group("val"))
            pairs.append(StringsKV(key=k, value=v, leading_comments=pending_comments[:] if pending_comments else []))
            pending_comments = []
            continue

        # 其它内容：放进 header（不丢）
        flush_pending_to_header()
        header.append(line)

    # 文件结束：把残余注释也放 header
    flush_pending_to_header()
    if in_block_comment:
        flush_block()
        flush_pending_to_header()

    return header, pairs


def _write_strings_source_sorted(path: str, header: List[str], pairs: List[StringsKV]) -> None:
    """
    源文件写回规则：
    - 保留注释：输出在对应文案上方（leading_comments 原样输出）
    - 按前缀分组
    - 组与组之间 1 个空行
    - 组内按 key 排序
    """
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)

    # 去重策略：同 key 多次出现时，以“最后一个”为准（贴近编译时覆盖直觉）
    last_by_key: Dict[str, StringsKV] = {}
    for kv in pairs:
        last_by_key[kv.key] = kv

    unique_pairs = list(last_by_key.values())

    # 分组
    groups: Dict[str, List[StringsKV]] = {}
    for kv in unique_pairs:
        groups.setdefault(_key_prefix(kv.key), []).append(kv)

    # 排序 group 名 + 每组 key
    group_names = sorted(groups.keys())
    for g in group_names:
        groups[g].sort(key=lambda x: x.key)

    out_lines: List[str] = []

    # header 原样输出（如果 header 里有块注释字符串含换行，这里要拆行）
    if header:
        for h in header:
            out_lines.extend(h.split("\n"))
        out_lines.append("")  # header 与正文之间留一空行（更常见）

    first_group = True
    for g in group_names:
        if not first_group:
            out_lines.append("")  # 组间 1 空行
        first_group = False

        for kv in groups[g]:
            # 输出注释（原样）
            for c in kv.leading_comments:
                out_lines.extend(c.split("\n"))
            kk = _escape_strings(kv.key)
            vv = _escape_strings(kv.value)
            out_lines.append(f"\"{kk}\" = \"{vv}\";")

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(out_lines).rstrip() + "\n")


def _write_strings_target_sorted_no_comments(path: str, data: Dict[str, str]) -> None:
    """
    目标文件写回规则：
    - 无注释
    - 无分组
    - 无额外空行（每条一行）
    - 仅按 key 排序，稳定输出
    """
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    lines: List[str] = []
    for k in sorted(data.keys()):
        kk = _escape_strings(k)
        vv = _escape_strings(data[k])
        lines.append(f"\"{kk}\" = \"{vv}\";")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def _load_strings_map(path: str) -> Dict[str, str]:
    """
    纯读取成 dict，不关心注释（用于目标文件、以及通用增量翻译读取）
    """
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        lines = f.read().splitlines()

    out: Dict[str, str] = {}
    in_block_comment = False
    for line in lines:
        stripped = line.strip()
        if in_block_comment:
            if "*/" in line:
                in_block_comment = False
            continue
        if stripped.startswith("/*"):
            if "*/" not in line:
                in_block_comment = True
            continue
        if stripped.startswith("//") or stripped == "":
            continue
        m = _STRINGS_PAIR_RE.match(line)
        if m:
            k = _unescape_strings(m.group("key"))
            v = _unescape_strings(m.group("val"))
            out[k] = v
    return out


# =========================
# Public sort APIs
# =========================

SortRole = Literal["source", "target"]


def sort_file(path: str, *, role: SortRole) -> None:
    """
    外部可直接调用的排序方法。
    - JSON：统一按 key 排序写回（源/目标都一样）
    - STRINGS：
      - role=source：保留注释 + 前缀分组 + 组间 1 空行
      - role=target：无注释、无分组、无空行，仅 key 排序
    """
    ft = detect_file_type(path)

    if ft == FileType.JSON:
        data = _load_flat_json_map(path)
        _save_sorted_json(path, data)
        return

    if ft == FileType.STRINGS:
        if role == "source":
            header, pairs = _parse_strings_keep_comments(path)
            _write_strings_source_sorted(path, header, pairs)
            return
        else:
            data = _load_strings_map(path)
            _write_strings_target_sorted_no_comments(path, data)
            return

    raise ValueError(f"Unsupported file type: {path}")


def sort_before_translate(*, sourceFilePath: str, targetFilePath: str) -> None:
    """
    翻译前调用：把源/目标都整理成稳定形态。
    """
    sort_file(sourceFilePath, role="source")
    sort_file(targetFilePath, role="target")
