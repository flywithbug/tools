from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List

from box_tools._share.openai_translate.translate import TranslationError, translate_flat_dict

from .fs import (
    I18N_DIR,
    ensure_language_files_in_group,
    get_active_groups,
    group_file_name,
    load_json_obj,
    normalize_group_filenames,
    save_json,
    split_slang_json,
)
from .model import ProjectConfig


@dataclass
class Progress:
    total_keys: int
    done_keys: int = 0
    started_at: float = 0.0

    def __post_init__(self) -> None:
        if self.started_at <= 0:
            self.started_at = time.time()

    def bump(self, n: int) -> None:
        self.done_keys += max(0, n)

    def percent(self) -> int:
        if self.total_keys <= 0:
            return 100
        return int(self.done_keys * 100 / self.total_keys)

    def eta_text(self) -> str:
        if self.total_keys <= 0 or self.done_keys <= 0:
            return "ETA: --"
        elapsed = time.time() - self.started_at
        rate = self.done_keys / max(elapsed, 1e-6)
        remain = max(self.total_keys - self.done_keys, 0)
        sec = int(remain / max(rate, 1e-6))
        if sec < 60:
            return f"ETA: {sec}s"
        if sec < 3600:
            return f"ETA: {sec//60}m{sec%60:02d}s"
        return f"ETA: {sec//3600}h{(sec%3600)//60:02d}m"


def ensure_all_language_files(i18n_dir: Path, cfg: ProjectConfig) -> None:
    groups = get_active_groups(i18n_dir)
    locale_codes = [cfg.source_locale.code, *cfg.target_codes()]

    if cfg.options.normalize_filenames:
        for g in groups:
            normalize_group_filenames(g, locale_codes=locale_codes, verbose=True)

    for g in groups:
        ensure_language_files_in_group(g, cfg.source_locale.code, cfg.target_codes())


def _build_prompt_for_target(cfg: ProjectConfig, tgt_code: str) -> str:
    default_en = (cfg.prompts.default_en or "").strip()
    locale_extra_en = (cfg.prompts.by_locale_en.get(tgt_code) or "").strip()

    guard = (
        "You are translating UI strings for a mobile app.\n"
        f"Source locale code: {cfg.source_locale.code}\n"
        f"Source language (English name): {cfg.source_locale.name_en}\n"
        f"Target locale code: {tgt_code}\n"
        f"Target language (English name): {cfg.target_name_en(tgt_code)}\n"
        "Rules:\n"
        f"- Output MUST be written in {cfg.target_name_en(tgt_code)}.\n"
        "- Do NOT output any other language.\n"
        "- Do NOT output Chinese unless the target language is Chinese.\n"
        "- Keep placeholders/variables/formatting unchanged.\n"
        "- Keep the meaning accurate and natural for the target language UI.\n"
    )

    parts: List[str] = []
    if default_en:
        parts.append(default_en)
    if locale_extra_en:
        parts.append(locale_extra_en)
    parts.append(guard)
    return "\n\n".join(parts).strip() + "\n"


def _compute_need_for_one(group: Path, cfg: ProjectConfig, tgt_code: str, incremental: bool, cleanup_extra: bool) -> int:
    src_path = group_file_name(group, cfg.source_locale.code)
    tgt_path = group_file_name(group, tgt_code)

    _, src_body = split_slang_json(src_path, load_json_obj(src_path))
    _, tgt_body = split_slang_json(tgt_path, load_json_obj(tgt_path))

    if cleanup_extra:
        tgt_body = {k: v for k, v in tgt_body.items() if k in src_body}

    need = {k: v for k, v in src_body.items() if k not in tgt_body} if incremental else dict(src_body)
    return len(need)


def translate_group(
        group: Path,
        cfg: ProjectConfig,
        api_key: str,
        model: str,
        incremental: bool,
        cleanup_extra: bool,
        sort_keys: bool,
        progress: Progress,
) -> None:
    src_code = cfg.source_locale.code
    src_name_en = cfg.source_locale.name_en
    targets_code = cfg.target_codes()

    src_path = group_file_name(group, src_code)
    _, src_body = split_slang_json(src_path, load_json_obj(src_path))

    module_name = group.name if group.name != I18N_DIR else "i18n"

    for tgt_code in targets_code:
        tgt_path = group_file_name(group, tgt_code)
        tgt_meta, tgt_body = split_slang_json(tgt_path, load_json_obj(tgt_path))

        if cleanup_extra:
            tgt_body = {k: v for k, v in tgt_body.items() if k in src_body}

        need = {k: v for k, v in src_body.items() if k not in tgt_body} if incremental else dict(src_body)

        if not need:
            tgt_meta = dict(tgt_meta)
            tgt_meta.setdefault("@@locale", tgt_code)
            save_json(tgt_path, tgt_meta, tgt_body, sort_keys=sort_keys)
            continue

        tgt_name_en = cfg.target_name_en(tgt_code)
        print(f"🌍 {module_name}: {src_code} → {tgt_code}  (+{len(need)} keys)")

        prompt_for_target = _build_prompt_for_target(cfg, tgt_code)

        translated = translate_flat_dict(
            prompt_en=prompt_for_target,
            src_dict=need,
            src_lang=src_name_en,
            tgt_locale=tgt_name_en,
            model=model,
            api_key=api_key,
        )

        tgt_body.update(translated)
        tgt_meta = dict(tgt_meta)
        tgt_meta.setdefault("@@locale", tgt_code)
        save_json(tgt_path, tgt_meta, tgt_body, sort_keys=sort_keys)

        progress.bump(len(translated))
        print(f"  📈 {progress.done_keys}/{progress.total_keys} ({progress.percent()}%) {progress.eta_text()}")


def translate_all(i18n_dir: Path, cfg: ProjectConfig, api_key: str, model: str, full: bool) -> None:
    incremental = not full
    cleanup_extra = cfg.options.cleanup_extra_keys
    sort_keys = cfg.options.sort_keys

    groups = get_active_groups(i18n_dir)
    targets = cfg.target_codes()

    total_need = 0
    group_need: Dict[Path, int] = {}
    for g in groups:
        need_sum = 0
        for code in targets:
            need_sum += _compute_need_for_one(g, cfg, code, incremental=incremental, cleanup_extra=cleanup_extra)
        group_need[g] = need_sum
        total_need += need_sum

    prog = Progress(total_keys=total_need)
    print(f"🧮 Total keys to translate: {total_need}（模式={'全量' if full else '增量'}，model={model}）")
    if total_need == 0:
        print("✅ 无需翻译：所有语言文件已齐全")
        return

    for g in groups:
        if group_need.get(g, 0) <= 0:
            continue
        translate_group(
            group=g,
            cfg=cfg,
            api_key=api_key,
            model=model,
            incremental=incremental,
            cleanup_extra=cleanup_extra,
            sort_keys=sort_keys,
            progress=prog,
        )
