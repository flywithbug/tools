from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple, Union, Literal, Protocol

from translate_list import translate_list
from models import OpenAIModel, sort_before_translate


# =========================
# Progress
# =========================

ProgressStage = Literal["start", "progress", "done", "error"]


@dataclass
class FileProgress:
    file: str
    stage: ProgressStage
    total: int
    done: int
    message: Optional[str] = None  # 仅用于展示摘要（不要作为逻辑字段）


ProgressCallback = Callable[[FileProgress], None]


def _one_line(s: str) -> str:
    return s.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\\n")


def _collapse_spaces(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def _truncate(s: str, max_len: int = 100) -> str:
    if len(s) <= max_len:
        return s
    return s[: max_len - 1] + "…"


def _make_item_message(key: str, src_text: str, max_len: int = 100) -> str:
    t = _truncate(_collapse_spaces(_one_line(src_text)), max_len=max_len)
    return f"key={key} | {t}"


# =========================
# File type + handler
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


class FlatMapFileHandler(Protocol):
    file_type: FileType

    def load_map(self, path: str) -> Dict[str, str]:
        """返回平铺 dict[str,str]。文件不存在时返回 {}。"""
        ...

    def save_map(self, path: str, data: Dict[str, str]) -> None:
        """写回到文件。"""
        ...


# =========================
# JSON handler
# =========================

class JsonFlatMapHandler:
    file_type = FileType.JSON

    def load_map(self, path: str) -> Dict[str, str]:
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

    def save_map(self, path: str, data: Dict[str, str]) -> None:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")


# =========================
# .strings handler (READ parses comments; WRITE outputs NO comments)
# =========================
# ✅ 读取：支持 /*...*/、//...、"k"="v";
# ✅ 写回：只输出纯 pairs，不保留任何注释（符合你的要求）

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


class StringsFlatMapHandler:
    file_type = FileType.STRINGS

    def load_map(self, path: str) -> Dict[str, str]:
        if not os.path.exists(path):
            return {}

        with open(path, "r", encoding="utf-8") as f:
            lines = f.read().splitlines()

        out: Dict[str, str] = {}

        in_block_comment = False

        for line in lines:
            stripped = line.strip()

            # 处理块注释状态
            if in_block_comment:
                if "*/" in line:
                    in_block_comment = False
                continue

            # 块注释开始
            if stripped.startswith("/*"):
                if "*/" not in line:
                    in_block_comment = True
                continue

            # 行注释
            if stripped.startswith("//"):
                continue

            # key-value
            m = _STRINGS_PAIR_RE.match(line)
            if m:
                k = _unescape_strings(m.group("key"))
                v = _unescape_strings(m.group("val"))
                # 重复 key：后者覆盖前者（保守）
                out[k] = v
                continue

            # 其他行忽略（比如 BOM、非标准内容等）

        return out

    def save_map(self, path: str, data: Dict[str, str]) -> None:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)

        # 不保留注释：直接重写“纯 pairs”
        # 这里默认按 key 排序，保证输出稳定；如果你不想排序可以删掉 sorted(...)
        lines: List[str] = []
        for k in sorted(data.keys()):
            v = data[k]
            kk = _escape_strings(k)
            vv = _escape_strings(v)
            lines.append(f"\"{kk}\" = \"{vv}\";")

        text = "\n".join(lines) + "\n"
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)


# =========================
# Core translate_from_to
# =========================

def _incremental_jobs(src: Dict[str, str], tgt: Dict[str, str]) -> List[Tuple[str, str]]:
    """
    增量规则：
    - target 缺 key 或 target[key] == "" -> 需要翻译
    - source 空字符串 -> 只同步 key（target.setdefault），不翻译
    """
    jobs: List[Tuple[str, str]] = []
    for k, src_text in src.items():
        if src_text.strip() == "":
            tgt.setdefault(k, "")
            continue
        if k not in tgt or tgt.get(k, "") == "":
            jobs.append((k, src_text))
    return jobs


def _chunk(items: List[Tuple[str, str]], batch_size: int) -> List[List[Tuple[str, str]]]:
    return [items[i:i + batch_size] for i in range(0, len(items), batch_size)]


def _resolve_model(model: Optional[Union[OpenAIModel, str]]) -> OpenAIModel:
    if model is None:
        return OpenAIModel.GPT_4O_MINI
    if isinstance(model, OpenAIModel):
        return model
    return OpenAIModel(model)


def _get_handler_for(path: str) -> FlatMapFileHandler:
    ft = detect_file_type(path)
    if ft == FileType.JSON:
        return JsonFlatMapHandler()
    if ft == FileType.STRINGS:
        return StringsFlatMapHandler()
    raise ValueError(f"Unsupported file type: {path}")


def translate_from_to(
        *,
        sourceFilePath: str,
        targetFilePath: str,
        src_locale: str,
        tgt_locale: str,
        model: Optional[Union[OpenAIModel, str]] = None,
        api_key: Optional[str] = None,
        prompt_en: Optional[str] = None,
        progress: Optional[ProgressCallback] = None,
        pre_sort: bool = True,
        batch_size:int = 40,
) -> None:
    """
    支持 *.json / *.strings 的平铺 key-value 增量翻译（内部分片串行）：
    - 只翻译 target 缺失或为空字符串的 key
    - translate_list 分批串行调用
    - 每批写盘一次
    - .strings 的目标文件写回“无注释纯 pairs”（符合你的要求）
    - progress.message 输出摘要
    """
    def emit(stage: ProgressStage, total: int, done: int, message: Optional[str] = None) -> None:
        if progress:
            progress(FileProgress(
                file=targetFilePath,
                stage=stage,
                total=total,
                done=done,
                message=message,
            ))

    if pre_sort:
        sort_before_translate(sourceFilePath=sourceFilePath, targetFilePath=targetFilePath)


    try:
        src_handler = _get_handler_for(sourceFilePath)
        tgt_handler = _get_handler_for(targetFilePath)

        src = src_handler.load_map(sourceFilePath)
        tgt = tgt_handler.load_map(targetFilePath)

        jobs = _incremental_jobs(src, tgt)
        total = len(jobs)

        emit("start", total, 0, message=f"incremental {src_locale} -> {tgt_locale}")

        if total == 0:
            tgt_handler.save_map(targetFilePath, tgt)
            emit("done", 0, 0, message="nothing to translate")
            return

        m = _resolve_model(model)
        batches = _chunk(jobs, batch_size)

        done = 0
        for bi, batch in enumerate(batches, start=1):
            k0, t0 = batch[0]
            emit(
                "progress",
                total,
                done,
                message=f"batch {bi}/{len(batches)} start | " + _make_item_message(k0, t0),
            )

            keys = [k for k, _ in batch]
            texts = [t for _, t in batch]

            translations = translate_list(
                prompt_en=prompt_en,
                src_items=texts,
                src_lang=src_locale,
                tgt_locale=tgt_locale,
                model=m,
                api_key=api_key,
                max_retries=2,
                placeholder_fallback_safe_to_source=True,
            )

            for k, tr in zip(keys, translations):
                tgt[k] = tr

            done += len(batch)

            # 每 batch 落盘一次
            tgt_handler.save_map(targetFilePath, tgt)

            emit("progress", total, done, message=f"batch {bi}/{len(batches)} done | {done}/{total}")

        emit("done", total, done, message="completed")

    except Exception as e:
        emit("error", 0, 0, message=str(e))
        raise
