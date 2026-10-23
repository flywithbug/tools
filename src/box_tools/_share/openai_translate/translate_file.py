from __future__ import annotations

import re
import os
from pathlib import Path
import sys

from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Tuple, Union, Literal

from .translate_list import translate_list, _Options
from .models import OpenAIModel, sort_before_translate, load_map, save_target_map


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


def _supports_osc8() -> bool:
    # 非 TTY（比如重定向到文件 / CI）就别输出控制符，避免乱码
    if not sys.stdout.isatty():
        return False

    # 一些常见终端/IDE 环境信号（覆盖 VS Code、Windows Terminal、通用 xterm 系）
    if os.environ.get("VSCODE_PID") or os.environ.get("WT_SESSION"):
        return True

    term = os.environ.get("TERM", "")
    return any(
        x in term
        for x in ("xterm", "screen", "tmux", "vt100", "rxvt", "alacritty", "kitty")
    )


def _osc8_link(text: str, target: str) -> str:
    esc = "\033"
    return f"{esc}]8;;{target}\a{text}{esc}]8;;\a"


def _file_hyperlink_display(path: str) -> str:
    name = os.path.basename(path)
    if not _supports_osc8():
        return name
    uri = Path(path).resolve().as_uri()  # file:///Users/...
    return _osc8_link(name, uri)


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
# Core translate_from_to
# =========================


def _incremental_jobs(
    src: Dict[str, str], tgt: Dict[str, str]
) -> List[Tuple[str, str]]:
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


def _chunk(
    items: List[Tuple[str, str]], batch_size: int
) -> List[List[Tuple[str, str]]]:
    return [items[i : i + batch_size] for i in range(0, len(items), batch_size)]


def _resolve_model(model: Optional[Union[OpenAIModel, str]]) -> OpenAIModel:
    if model is None:
        return OpenAIModel.GPT_4O_MINI
    if isinstance(model, OpenAIModel):
        return model
    # Allow arbitrary model strings (pass-through elsewhere)
    try:
        return OpenAIModel(str(model))
    except Exception:
        # Type: ignore to allow str return at call sites
        return str(model)  # type: ignore[return-value]


def translate_from_to(
    *,
    source_file_path: str,
    target_file_path: str,
    src_locale: str,
    tgt_locale: str,
    model: Optional[Union[OpenAIModel, str]] = None,
    api_key: Optional[str] = None,
    prompt_en: Optional[str] = None,
    progress: Optional[ProgressCallback] = None,
    batch_size: int = 50,
    pre_sort: bool = True,
) -> None:

    if not os.path.exists(source_file_path):
        raise FileNotFoundError(f"source file not found: {source_file_path}")

    """
    *.json / *.strings 平铺 key-value 增量翻译（内部分片串行）：
    - pre_sort=True：翻译前对源/目标排序（源保留注释分组；目标纯排序）
    - batch_size：每批翻译条数
    - 每批翻完立刻写盘（断点续跑）
    - 目标 .strings：无注释、无分组、无空行（由 models.save_target_map 保证）
    """

    def emit(
        stage: ProgressStage, total: int, done: int, message: Optional[str] = None
    ) -> None:
        if progress:
            progress(
                FileProgress(
                    file=_file_hyperlink_display(
                        target_file_path
                    ),  # ✅ 显示为文件名，点击打开完整路径
                    stage=stage,
                    total=total,
                    done=done,
                    message=message,
                )
            )

    try:
        if pre_sort:
            sort_before_translate(
                source_file_path=source_file_path, target_file_path=target_file_path
            )

        src = load_map(source_file_path)
        tgt = load_map(target_file_path)

        jobs = _incremental_jobs(src, tgt)
        total = len(jobs)

        emit("start", total, 0, message=f"incremental {src_locale} -> {tgt_locale}")

        if total == 0:
            # 即使不用翻译，也写回一次，保证目标文件存在且格式稳定
            save_target_map(target_file_path, tgt)
            emit("done", 0, 0, message="nothing to translate")
            return

        m = _resolve_model(model)
        batches = _chunk(jobs, batch_size)

        opt = _Options(
            retries=2,
            placeholder_fallback_safe_to_source=True,
        )

        done = 0
        for bi, batch in enumerate(batches, start=1):
            k0, t0 = batch[0]
            emit(
                "progress",
                total,
                done,
                message=f"batch {bi}/{len(batches)} start | "
                + _make_item_message(k0, t0),
            )

            keys = [k for k, _ in batch]
            texts = [t for _, t in batch]

            translations = translate_list(
                prompt_en=prompt_en,
                src_items=texts,
                src_locale=src_locale,
                tgt_locale=tgt_locale,
                model=m,
                api_key=api_key,
                opt=opt,
            )

            for k, tr in zip(keys, translations):
                tgt[k] = tr

            done += len(batch)

            # ✅ 复用 models 的目标写回规则（json 稳定排序；strings 纯 pairs 排序无注释）
            save_target_map(target_file_path, tgt)

            emit(
                "progress",
                total,
                done,
                message=f"batch {bi}/{len(batches)} done | {done}/{total}",
            )

        emit("done", total, done, message="completed")

    except Exception as e:
        emit("error", 0, 0, message=str(e))
        raise
