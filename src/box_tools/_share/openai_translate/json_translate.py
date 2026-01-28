from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Dict, Optional, Union, Any, Iterable, Tuple, Callable

from .models import OpenAIModel
from .translate import translate_flat_dict, _Options, ProgressCallback


class JsonTranslateError(RuntimeError):
    pass


def _load_json_flat(path: str) -> Dict[str, str]:
    """
    Load a JSON file into a flat dict[str, str].
    - Missing file => {}
    - Non-str values are coerced to JSON string via json.dumps, to avoid crashes.
    """
    if not path or not os.path.exists(path):
        return {}

    with open(path, "r", encoding="utf-8") as f:
        try:
            data = json.load(f)
        except json.JSONDecodeError as e:
            raise JsonTranslateError(f"Invalid JSON: {path}: {e}") from e

    if data is None:
        return {}
    if not isinstance(data, dict):
        raise JsonTranslateError(f"JSON root must be an object/dict: {path}")

    out: Dict[str, str] = {}
    for k, v in data.items():
        if v is None:
            out[str(k)] = ""
        elif isinstance(v, str):
            out[str(k)] = v
        else:
            # Best-effort: keep info without crashing.
            out[str(k)] = json.dumps(v, ensure_ascii=False)
    return out


def _atomic_write_json(path: str, data: Dict[str, str]) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")
    os.replace(tmp, path)


def _iter_chunks(items: Dict[str, str], chunk_size: int) -> Iterable[Dict[str, str]]:
    if chunk_size <= 0:
        yield items
        return
    chunk: Dict[str, str] = {}
    for k, v in items.items():
        chunk[k] = v
        if len(chunk) >= chunk_size:
            yield chunk
            chunk = {}
    if chunk:
        yield chunk


def _safe_cb(cb: Optional[ProgressCallback], payload: Dict[str, Any]) -> None:
    if not cb:
        return
    try:
        cb(payload)
    except Exception:
        # MUST NOT affect translation flow
        return


def translate_from_to(
        *,
        sourceFilePath: str,
        targetFilePath: str,
        src_locale: str,
        tgt_locale: str,
        model: Optional[Union[OpenAIModel, str]] = None,
        api_key: Optional[str] = None,
        prompt_en: Optional[str] = None,
        opt: Optional[_Options] = None,
        progress_cb: Optional[ProgressCallback] = None,
) -> Dict[str, str]:
    """
    Translate from {sourceFilePath}.json into {targetFilePath}.json incrementally.

    - Auto diff: translate keys that are missing/empty in target, or whose target text == source text
      (a common "not yet translated" signal).
    - Auto chunking by key-count using opt.max_chunk_items (default=60).
    - Stream write: after each chunk, merge into target and write to targetFilePath.

    Returns: the final merged target dict.
    """
    opt = opt or _Options()

    src = _load_json_flat(sourceFilePath)
    if not src:
        raise JsonTranslateError(f"Source JSON is empty or missing: {sourceFilePath}")

    tgt = _load_json_flat(targetFilePath)

    # Decide what needs translation
    todo: Dict[str, str] = {}
    for k, src_text in src.items():
        tgt_text = tgt.get(k)
        if tgt_text is None or tgt_text == "" or tgt_text == src_text:
            todo[k] = src_text

    _safe_cb(progress_cb, {
        "event": "diff",
        "source": sourceFilePath,
        "target": targetFilePath,
        "src_locale": src_locale,
        "tgt_locale": tgt_locale,
        "src_keys": len(src),
        "target_keys": len(tgt),
        "todo_keys": len(todo),
    })

    if not todo:
        # Ensure target exists on disk
        _atomic_write_json(targetFilePath, tgt)
        _safe_cb(progress_cb, {
            "event": "noop",
            "tgt_locale": tgt_locale,
            "todo_keys": 0,
        })
        return tgt

    # Translate chunk by chunk, and write after each merge
    chunk_size = opt.max_chunk_items
    done = 0
    total = len(todo)

    for idx, chunk in enumerate(_iter_chunks(todo, chunk_size), start=1):
        _safe_cb(progress_cb, {
            "event": "chunk_begin",
            "chunk_index": idx,
            "chunk_items": len(chunk),
            "done": done,
            "total": total,
            "src_locale": src_locale,
            "tgt_locale": tgt_locale,
        })

        # translate_flat_dict will internally chunk too, but since we pre-chunk
        # to <= max_chunk_items, this is typically one request per outer chunk.
        out_part = translate_flat_dict(
            prompt_en=prompt_en,
            src_dict=chunk,
            src_lang=src_locale,
            tgt_locale=tgt_locale,
            model=model,
            api_key=api_key,
            opt=opt,
            progress_cb=progress_cb,
        )

        # Merge & write immediately
        tgt.update(out_part)
        _atomic_write_json(targetFilePath, tgt)

        done += len(chunk)
        _safe_cb(progress_cb, {
            "event": "chunk_done",
            "chunk_index": idx,
            "chunk_items": len(chunk),
            "done": done,
            "total": total,
            "src_locale": src_locale,
            "tgt_locale": tgt_locale,
            "written_to": targetFilePath,
        })

    _safe_cb(progress_cb, {
        "event": "all_done",
        "tgt_locale": tgt_locale,
        "total_written": done,
        "targetFilePath": targetFilePath,
    })
    return tgt
