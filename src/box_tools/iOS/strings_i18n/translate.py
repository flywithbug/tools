#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
strings_i18n translate.py

骨架目标：
- 对齐 slang_i18n 的 translate.py 结构（增量/全量、并发、主线程写回）
- 但具体 I/O 是 Xcode .lproj/Localizable.strings（后续补齐）

本文件先提供 tool.py 依赖的最小 API：
- get_api_key
- translate_core
- translate_target
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import data

# 尽量复用现有 translate_flat_dict（若项目里已有共享实现）
try:
    from box_tools._share.openai_translate.translate import translate_flat_dict  # type: ignore
except Exception:  # pragma: no cover
    translate_flat_dict = None  # type: ignore


def get_api_key(cli_api_key: Optional[str]) -> Optional[str]:
    return (cli_api_key or os.getenv("OPENAI_API_KEY") or "").strip() or None


# -------------------------
# Task model（参考 slang_i18n）
# -------------------------

@dataclass(frozen=True)
class _Task:
    idx: int
    total: int
    src_code: str
    src_lang_name: str
    tgt_code: str
    tgt_lang_name: str
    model: str
    prompt_en: Optional[str]

    # Xcode 侧：后续会替换为 .strings 文件路径与解析对象
    src_kv: Dict[str, str]  # 本次提交的 key -> src_text
    tgt_path: Path
    tgt_existing: Dict[str, str]  # 目标文件已存在的 KV（不含注释）


def translate_core(
    project_root: Path,
    cfg: data.I18nConfig,
    api_key: str,
    model: str,
    full: bool,
    dry_run: bool,
) -> None:
    """
    翻译（core）：Base.lproj -> core_locales
    full=False 时按增量；full=True 时全量覆盖（保留目标已有元信息/注释策略待定）。
    """
    _translate_entry(
        project_root=project_root,
        cfg=cfg,
        api_key=api_key,
        model=model,
        full=full,
        dry_run=dry_run,
        targets=cfg.core_locales,
        mode_name="translate-core",
        src_locale=cfg.base_locale,  # core 翻译从 base 出发（按你们 PRD/约定可调整）
    )


def translate_target(
    project_root: Path,
    cfg: data.I18nConfig,
    api_key: str,
    model: str,
    full: bool,
    dry_run: bool,
) -> None:
    """
    翻译（target）：source_locale -> target_locales
    """
    _translate_entry(
        project_root=project_root,
        cfg=cfg,
        api_key=api_key,
        model=model,
        full=full,
        dry_run=dry_run,
        targets=cfg.target_locales,
        mode_name="translate-target",
        src_locale=cfg.source_locale,
    )


# -------------------------
# Core runner（骨架）
# -------------------------

def _translate_entry(
    project_root: Path,
    cfg: data.I18nConfig,
    api_key: str,
    model: str,
    full: bool,
    dry_run: bool,
    targets: List[data.Locale],
    mode_name: str,
    src_locale: data.Locale,
) -> None:
    # TODO: 解析 source .strings（src_kv）
    # 当前骨架：只做结构检查，不真正翻译
    lang_root = cfg.lang_root if cfg.lang_root.is_absolute() else (project_root / cfg.lang_root).resolve()

    src_dir = lang_root / _lproj_dir_name(src_locale.code, base_folder=cfg.base_folder)
    src_strings = src_dir / "Localizable.strings"
    if not src_strings.exists():
        raise FileNotFoundError(f"❌ 源语言缺少 Localizable.strings：{src_strings}")

    if not targets:
        print(f"⚠️ {mode_name}：targets 为空，跳过。")
        return

    incremental = (not full)
    mode = "增量" if incremental else "全量"
    print(f"🌍 {mode_name} 开始（骨架）")
    print(f"- 模式: {mode}")
    print(f"- Source: {src_locale.code} ({src_locale.name_en})")
    print(f"- Targets: {[t.code for t in targets]}")
    print(f"- lang_root: {lang_root}")

    # TODO: src_kv = parse_strings(src_strings)
    src_kv: Dict[str, str] = {}

    tasks = _build_tasks(
        cfg=cfg,
        lang_root=lang_root,
        src_locale=src_locale,
        src_kv=src_kv,
        targets=targets,
        model=model,
        incremental=incremental,
    )

    if not tasks:
        print("✅ 没有需要翻译的任务（骨架：src_kv 为空）")
        return

    max_workers = min(4, len(tasks))
    print(f"- 并发: {max_workers} workers（骨架默认）")

    # NOTE：骨架不写文件；后续实现会在主线程写回（像 slang_i18n 一样）
    if translate_flat_dict is None:
        raise RuntimeError("❌ 缺少 translate_flat_dict 实现：请确认 box_tools._share.openai_translate.translate 可用")

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = [ex.submit(_translate_one, t, api_key) for t in tasks]
        for fut in as_completed(futures):
            _ = fut.result()
            # TODO: merge -> sort -> write .strings (main thread), respect dry_run


def _build_tasks(
    cfg: data.I18nConfig,
    lang_root: Path,
    src_locale: data.Locale,
    src_kv: Dict[str, str],
    targets: List[data.Locale],
    model: str,
    incremental: bool,
) -> List[_Task]:
    tasks: List[_Task] = []

    # TODO: 读取各 target .strings，决定增量需要翻译的 key
    # 当前骨架：不做真实 diff，因为 src_kv 为空也没有意义
    total = len(targets)
    for i, tgt in enumerate(targets, start=1):
        tgt_dir = lang_root / _lproj_dir_name(tgt.code, base_folder=cfg.base_folder)
        tgt_path = tgt_dir / "Localizable.strings"
        prompt_en = _build_prompt_en(cfg, target_code=tgt.code)

        tasks.append(
            _Task(
                idx=i,
                total=total,
                src_code=src_locale.code,
                src_lang_name=src_locale.name_en,
                tgt_code=tgt.code,
                tgt_lang_name=tgt.name_en,
                model=model,
                prompt_en=prompt_en,
                src_kv={},  # TODO
                tgt_path=tgt_path,
                tgt_existing={},  # TODO
            )
        )

    return tasks


def _translate_one(t: _Task, api_key: str) -> Dict[str, Any]:
    # slang_i18n：translate_flat_dict 只关心 prompt/src_dict/lang names
    out = translate_flat_dict(
        prompt_en=t.prompt_en,
        src_dict=t.src_kv,
        src_lang=t.src_lang_name,
        tgt_locale=t.tgt_lang_name,
        model=t.model,
        api_key=api_key,
    )
    return out


def _build_prompt_en(cfg: data.I18nConfig, target_code: str) -> Optional[str]:
    prompts = cfg.prompts or {}
    default_en = (prompts.get("default_en") or "").strip()
    by_locale_en = prompts.get("by_locale_en") or {}
    extra = (by_locale_en.get(target_code) or "").strip() if isinstance(by_locale_en, dict) else ""
    parts = [p for p in [default_en, extra] if p]
    return "\n\n".join(parts) if parts else None


def _lproj_dir_name(code: str, base_folder: str) -> str:
    # Base.lproj 特殊；其他按 <code>.lproj
    if code.lower() == "base" or code == "Base":
        return base_folder
    if code.endswith(".lproj"):
        return code
    return f"{code}.lproj"
