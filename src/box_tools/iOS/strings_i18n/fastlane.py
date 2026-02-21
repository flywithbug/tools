from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import data
from box_tools._share.openai_translate.translate import translate_flat_dict

URL_PASSTHROUGH_FILES = {"marketing_url.txt", "support_url.txt", "privacy_url.txt"}


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


def _is_zh_locale(locale: str) -> bool:
    loc = locale.lower()
    return loc.startswith("zh") or "hans" in loc or "hant" in loc


def _locale_allows_cjk(locale: str) -> bool:
    loc = locale.lower()
    if _is_zh_locale(loc):
        return True
    if loc.startswith("ja") or loc.startswith("ko"):
        return True
    return False


def _has_cjk(text: str) -> bool:
    for ch in text:
        cp = ord(ch)
        if 0x4E00 <= cp <= 0x9FFF:
            return True
        if 0x3400 <= cp <= 0x4DBF:
            return True
    return False


def _looks_wrong_language(locale: str, text: str) -> bool:
    if _locale_allows_cjk(locale):
        return False
    return _has_cjk(text)


def _extract_ascii(text: str) -> str:
    allowed = set(
        "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-'.,:/()[]{}+&_#@!? "
    )
    out = "".join(ch for ch in text if ch in allowed)
    out = " ".join(out.split())
    return out.strip(" .,:;/|·-—_")


def _length_policy_for(filename: str) -> tuple[Optional[int], Optional[str]]:
    if filename == "keywords.txt":
        return (99, "keywords")
    if filename == "subtitle.txt":
        return (29, "subtitle")
    if filename == "name.txt":
        return (29, "name")
    return (None, None)


def _postprocess_keywords_locale(s: str, max_chars: Optional[int], tgt_locale: str) -> str:
    s = s.replace("，", ",").replace("、", ",").replace("；", ",")
    parts = [p.strip() for p in s.split(",") if p.strip()]
    cleaned: List[str] = []
    seen = set()
    keep_unicode = _locale_allows_cjk(tgt_locale) or _is_zh_locale(tgt_locale)
    for p in parts:
        tok = p if keep_unicode else _extract_ascii(p)
        if not tok:
            continue
        key = tok.lower()
        if key not in seen:
            seen.add(key)
            cleaned.append(tok)
    out = ",".join(cleaned)
    if max_chars and len(out) > max_chars:
        keep: List[str] = []
        total = 0
        for w in cleaned:
            add = len(w) if not keep else 1 + len(w)
            if total + add <= max_chars:
                keep.append(w)
                total += add
            else:
                break
        out = ",".join(keep)
    return out


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


def _load_code_to_asc(cfg: data.StringsI18nConfig) -> Dict[str, str]:
    out: Dict[str, str] = {}
    try:
        arr = json.loads(cfg.languages_path.read_text(encoding="utf-8"))
    except Exception:
        return out
    if not isinstance(arr, list):
        return out
    for it in arr:
        if not isinstance(it, dict):
            continue
        code = str(it.get("code", "")).strip()
        asc = str(it.get("asc_code", "")).strip()
        if code and asc:
            out[code] = asc
    return out


def _asc_code(loc: data.Locale, code_to_asc: Dict[str, str]) -> str:
    cfg_asc = (loc.asc_code or "").strip()
    if cfg_asc and cfg_asc != loc.code:
        return cfg_asc
    mapped = (code_to_asc.get(loc.code) or "").strip()
    if mapped:
        return mapped
    if cfg_asc:
        return cfg_asc
    return loc.code.strip()


def _read_text(fp: Path) -> str:
    if not fp.exists():
        return ""
    try:
        return fp.read_text(encoding="utf-8")
    except Exception:
        return ""


def _dedup_targets_by_asc(
    locales: List[data.Locale], code_to_asc: Dict[str, str]
) -> List[data.Locale]:
    seen = set()
    out: List[data.Locale] = []
    for loc in locales:
        asc = _asc_code(loc, code_to_asc)
        if not asc or asc in seen:
            continue
        seen.add(asc)
        out.append(loc)
    return out


def _build_field_prompt(
    cfg: data.StringsI18nConfig,
    *,
    target_code: str,
    target_locale_name: str,
    filename: str,
) -> Optional[str]:
    base = _build_prompt_en(cfg, target_code=target_code) or ""
    max_chars, field = _length_policy_for(filename)
    rules: List[str] = []
    if field == "keywords":
        rules.append("Output only a comma-separated keywords list; no duplicates; no trailing comma.")
    if field == "name":
        rules.append('If source contains "TimeTrails", keep brand as "TimeTrails".')
    if field == "subtitle":
        rules.append('Do not include brand name "TimeTrails".')
        rules.append("Avoid quotes and emojis.")
    if max_chars:
        rules.append(f"Keep output length <= {max_chars} characters.")
    if not _locale_allows_cjk(target_locale_name):
        rules.append("Do NOT use Chinese characters.")
    if not rules:
        return base if base else None
    extra = " ".join(rules)
    return f"{base}\n\n{extra}".strip()


def _postprocess_translated_text(
    *,
    filename: str,
    target_locale: str,
    source_text: str,
    translated_text: str,
) -> str:
    text = (translated_text or "").strip()
    max_chars, field = _length_policy_for(filename)
    if field == "keywords":
        text = _postprocess_keywords_locale(text, max_chars, target_locale)
    elif field == "name":
        if _is_zh_locale(target_locale):
            text = "时光轨迹" if target_locale == "zh-Hans" else "時光軌跡"
        else:
            if "TimeTrails" not in text:
                text = f"TimeTrails · {text}".strip(" ·") if text else "TimeTrails"
    elif field == "subtitle":
        for banned in ("时光轨迹", "時光軌跡", "TimeTrails"):
            text = text.replace(banned, "")
        text = text.strip().strip(" -–—:|·_/\\")
        if not text:
            text = source_text.strip()
    if _looks_wrong_language(target_locale, text):
        if field == "keywords":
            text = _postprocess_keywords_locale(text, max_chars, target_locale)
        else:
            text = _extract_ascii(text)
    if max_chars and text and len(text) > max_chars:
        text = text[:max_chars].rstrip()
    return text


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
    code_to_asc: Dict[str, str],
    fallback_src_locale: Optional[data.Locale] = None,
) -> None:
    if not targets:
        print(f"⚠️ {phase_name}：目标为空，跳过。")
        return

    model = _get_model(cfg)
    api_key = _norm_api_key(getattr(cfg, "api_key", None))
    root = cfg.fastlane_metadata_root

    src_asc = _asc_code(src_locale, code_to_asc)
    src_dir = (root / src_asc).resolve()
    fallback_dir = None
    if fallback_src_locale is not None:
        fallback_dir = (root / _asc_code(fallback_src_locale, code_to_asc)).resolve()

    print(f"\n🧩 {phase_name}")
    print(f"- src: {src_locale.code} -> {src_asc}")
    print(f"- tgt: {[f'{x.code}->{_asc_code(x, code_to_asc)}' for x in targets]}")
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
        tgt_asc = _asc_code(tgt, code_to_asc)
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
                if tgt_fp.exists():
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

        wrote = 0
        for fn, src_txt in to_translate.items():
            fp = (tgt_dir / fn).resolve()
            if fn in URL_PASSTHROUGH_FILES:
                fp.parent.mkdir(parents=True, exist_ok=True)
                fp.write_text(src_txt.strip() + "\n", encoding="utf-8")
                wrote += 1
                continue

            out = translate_flat_dict(
                prompt_en=_build_field_prompt(
                    cfg,
                    target_code=tgt.code,
                    target_locale_name=tgt.name_en,
                    filename=fn,
                ),
                src_dict={fn: src_txt},
                src_lang=src_locale.name_en,
                tgt_locale=tgt.name_en,
                model=model,
                api_key=api_key,
                progress_cb=None,
            )
            v = out.get(fn)
            if not isinstance(v, str):
                continue
            text = _postprocess_translated_text(
                filename=fn,
                target_locale=tgt_asc,
                source_text=src_txt,
                translated_text=v,
            )
            if not text.strip():
                continue
            fp.parent.mkdir(parents=True, exist_ok=True)
            fp.write_text(text.strip() + "\n", encoding="utf-8")
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
    code_to_asc = _load_code_to_asc(cfg)
    src_asc = _asc_code(cfg.base_locale, code_to_asc)
    targets = [
        x for x in cfg.core_locales if _asc_code(x, code_to_asc) != src_asc
    ]
    targets = _dedup_targets_by_asc(targets, code_to_asc)
    _translate_phase(
        cfg=cfg,
        phase_name="Phase 1: base_locale -> core_locales",
        src_locale=cfg.base_locale,
        targets=targets,
        incremental=incremental,
        code_to_asc=code_to_asc,
    )


def translate_source_to_target(
    cfg: data.StringsI18nConfig, incremental: bool = True
) -> None:
    code_to_asc = _load_code_to_asc(cfg)
    base_asc = _asc_code(cfg.base_locale, code_to_asc)
    src_asc = _asc_code(cfg.source_locale, code_to_asc)
    targets = [
        x
        for x in cfg.target_locales
        if _asc_code(x, code_to_asc) not in {base_asc, src_asc}
    ]
    targets = _dedup_targets_by_asc(targets, code_to_asc)
    _translate_phase(
        cfg=cfg,
        phase_name="Phase 2: source_locale -> target_locales",
        src_locale=cfg.source_locale,
        targets=targets,
        incremental=incremental,
        code_to_asc=code_to_asc,
        fallback_src_locale=cfg.base_locale,
    )


def run_fastlane(cfg: data.StringsI18nConfig, incremental: bool = True) -> None:
    mode = "增量" if incremental else "全量"
    print("🚀 fastlane metadata translate")
    print(f"- 模式: {mode}")
    print(f"- metadata root: {cfg.fastlane_metadata_root}")
    legacy_en_dir = (cfg.fastlane_metadata_root / "en").resolve()
    if legacy_en_dir.exists() and legacy_en_dir.is_dir():
        print(f"⚠️ 检测到旧目录：{legacy_en_dir}（建议使用 en-US）")

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
