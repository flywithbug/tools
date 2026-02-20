from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import data
from box_tools._share.openai_translate.translate import translate_flat_dict


def _norm_api_key(v: Any) -> Optional[str]:
    if isinstance(v, str):
        s = v.strip()
        return s or None
    return None


def _get_model(cfg: data.StringsI18nConfig) -> str:
    m0 = getattr(cfg, "openai_model", None)
    if isinstance(m0, str) and m0.strip():
        return m0.strip()

    if isinstance(cfg.options, dict):
        m = (
            cfg.options.get("model")
            or cfg.options.get("openai_model")
            or cfg.options.get("openaiModel")
        )
        if isinstance(m, str) and m.strip():
            return m.strip()

    return os.environ.get("OPENAI_MODEL", "gpt-4o-mini")


def _build_prompt_en(cfg: data.StringsI18nConfig, target_code: str) -> Optional[str]:
    prompts = cfg.prompts or {}
    default_en = (prompts.get("default_en") or "").strip()
    by_locale_en = prompts.get("by_locale_en") or {}
    extra = (
        (by_locale_en.get(target_code) or "").strip()
        if isinstance(by_locale_en, dict)
        else ""
    )
    parts = [x for x in [default_en, extra] if x]
    return "\n\n".join(parts) if parts else None


def _asc_code(loc: data.Locale) -> str:
    return (loc.asc_code or loc.code).strip()


def _read_text(fp: Path) -> str:
    if not fp.exists():
        return ""
    try:
        return fp.read_text(encoding="utf-8")
    except Exception:
        return ""


def _has_content(fp: Path) -> bool:
    txt = _read_text(fp)
    return bool(txt.strip())


def _dedup_targets_by_asc(locales: List[data.Locale]) -> List[data.Locale]:
    seen = set()
    out: List[data.Locale] = []
    for loc in locales:
        asc = _asc_code(loc)
        if not asc or asc in seen:
            continue
        seen.add(asc)
        out.append(loc)
    return out


def _list_locale_files(locale_dir: Path) -> List[str]:
    if not locale_dir.exists() or not locale_dir.is_dir():
        return []
    out: List[str] = []
    for p in sorted(locale_dir.iterdir(), key=lambda x: x.name.lower()):
        if p.is_file():
            out.append(p.name)
    return out


def _translate_phase(
    *,
    cfg: data.StringsI18nConfig,
    phase_name: str,
    src_locale: data.Locale,
    targets: List[data.Locale],
    incremental: bool,
    fallback_src_locale: Optional[data.Locale] = None,
) -> None:
    if not targets:
        print(f"⚠️ {phase_name}：目标为空，跳过。")
        return

    model = _get_model(cfg)
    api_key = _norm_api_key(getattr(cfg, "api_key", None))
    root = cfg.fastlane_metadata_root

    src_asc = _asc_code(src_locale)
    src_dir = (root / src_asc).resolve()
    fallback_dir = None
    if fallback_src_locale is not None:
        fallback_dir = (root / _asc_code(fallback_src_locale)).resolve()

    print(f"\n🧩 {phase_name}")
    print(f"- src: {src_locale.code} -> {src_asc}")
    print(f"- tgt: {[f'{x.code}->{_asc_code(x)}' for x in targets]}")
    source_files = set(_list_locale_files(src_dir))
    if fallback_dir is not None:
        source_files.update(_list_locale_files(fallback_dir))
    files = sorted(source_files, key=lambda x: x.lower())
    if not files:
        print(f"⚠️ {phase_name}：未在源目录发现可翻译文件，跳过。")
        return

    print(f"- files: {len(files)}")

    total_files = 0
    translated_files = 0
    start = time.perf_counter()

    for tgt in targets:
        tgt_asc = _asc_code(tgt)
        if tgt_asc == src_asc:
            continue

        src_map: Dict[str, str] = {}
        for fn in files:
            total_files += 1
            src_fp = (src_dir / fn).resolve()
            txt = _read_text(src_fp)

            if not txt.strip() and fallback_dir is not None:
                fb_txt = _read_text((fallback_dir / fn).resolve())
                if fb_txt.strip():
                    txt = fb_txt

            if txt.strip():
                src_map[fn] = txt.strip()

        if not src_map:
            print(f"⚠️ {tgt_asc} 无可用源文案，跳过。")
            continue

        tgt_dir = (root / tgt_asc).resolve()
        tgt_dir.mkdir(parents=True, exist_ok=True)

        to_translate: Dict[str, str] = {}
        if incremental:
            for fn, src_txt in src_map.items():
                tgt_fp = (tgt_dir / fn).resolve()
                if _has_content(tgt_fp):
                    continue
                to_translate[fn] = src_txt
        else:
            to_translate = dict(src_map)

        if not to_translate:
            continue

        print(
            f"⏳ ({phase_name}) {src_locale.code}->{tgt.code} [{src_asc}->{tgt_asc}]"
            f"  {len(to_translate)} file(s)"
        )

        out = translate_flat_dict(
            prompt_en=_build_prompt_en(cfg, target_code=tgt.code),
            src_dict=to_translate,
            src_lang=src_locale.name_en,
            tgt_locale=tgt.name_en,
            model=model,
            api_key=api_key,
            progress_cb=None,
        )

        wrote = 0
        for fn, v in out.items():
            if fn.startswith("@@"):
                continue
            if not isinstance(v, str) or not v.strip():
                continue
            fp = (tgt_dir / fn).resolve()
            fp.parent.mkdir(parents=True, exist_ok=True)
            fp.write_text(v.strip() + "\n", encoding="utf-8")
            wrote += 1

        translated_files += wrote
        print(f"✅ {tgt_asc} 写入 {wrote} file(s)")

    sec = time.perf_counter() - start
    print(
        f"🎉 {phase_name} 完成：translated={translated_files}, scanned={total_files}, elapsed={sec:.2f}s"
    )


def translate_base_to_core(
    cfg: data.StringsI18nConfig, incremental: bool = True
) -> None:
    src_asc = _asc_code(cfg.base_locale)
    targets = [x for x in cfg.core_locales if _asc_code(x) != src_asc]
    targets = _dedup_targets_by_asc(targets)
    _translate_phase(
        cfg=cfg,
        phase_name="Phase 1: base_locale -> core_locales",
        src_locale=cfg.base_locale,
        targets=targets,
        incremental=incremental,
    )


def translate_source_to_target(
    cfg: data.StringsI18nConfig, incremental: bool = True
) -> None:
    base_asc = _asc_code(cfg.base_locale)
    src_asc = _asc_code(cfg.source_locale)
    targets = [
        x
        for x in cfg.target_locales
        if _asc_code(x) not in {base_asc, src_asc}
    ]
    targets = _dedup_targets_by_asc(targets)
    _translate_phase(
        cfg=cfg,
        phase_name="Phase 2: source_locale -> target_locales",
        src_locale=cfg.source_locale,
        targets=targets,
        incremental=incremental,
        fallback_src_locale=cfg.base_locale,
    )


def run_fastlane(cfg: data.StringsI18nConfig, incremental: bool = True) -> None:
    mode = "增量" if incremental else "全量"
    print("🚀 fastlane metadata translate")
    print(f"- 模式: {mode}")
    print(f"- metadata root: {cfg.fastlane_metadata_root}")

    if sys.stdin.isatty():
        while True:
            print("\n=== fastlane phases ===")
            print("1. base_locale -> core_locales")
            print("2. source_locale -> target_locales")
            print("3. 回退")
            print("0. 退出")
            choice = input("> ").strip()
            if choice == "1":
                translate_base_to_core(cfg, incremental=incremental)
            elif choice == "2":
                translate_source_to_target(cfg, incremental=incremental)
            elif choice == "3":
                return
            elif choice == "0":
                raise SystemExit(0)
            else:
                print("请输入 1/2/3/0")
    else:
        translate_base_to_core(cfg, incremental=incremental)
        translate_source_to_target(cfg, incremental=incremental)
