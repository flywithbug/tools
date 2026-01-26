from __future__ import annotations
from . import data


def run_translate(cfg: data.StringsI18nConfig, incremental: bool = True) -> None:
    mode = "增量" if incremental else "全量"
    print("🌍 translate（框架版）")
    print(f"- 模式: {mode}")
    print(f"- lang_root: {cfg.lang_root}")
    print(f"- base_folder: {cfg.base_folder}")
    print(f"- base_locale: {cfg.base_locale.code}")
    print(f"- source_locale: {cfg.source_locale.code}")
    print(f"- core_locales: {[x.code for x in cfg.core_locales]}")
    print(f"- target_locales: {[x.code for x in cfg.target_locales]}")
    print("✅ translate 结束（框架版：尚未接入 .strings 翻译管线）")
