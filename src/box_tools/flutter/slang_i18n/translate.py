# translate.py
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from box_tools._share.openai_translate.translate import translate_flat_dict

from . import data


def run_translate(cfg: data.I18nConfig, incremental: bool = True) -> None:
    """
    增量翻译（推荐默认）：
      - 以每个模块的 source 文件为源（按 data.expected_i18n_filename 生成）
      - 对比对应 target 文件：缺 key / 空字符串 / None -> 翻译补齐；否则跳过
      - 仅处理普通 key（忽略 @@* 元字段）
      - JSON 必须是 flat（由 data.read_json/write_json 保证）

    全量翻译：
      - 以 source 为基准，覆盖生成 target（仍忽略 @@*，但会保留 target 原有 @@*）
    """
    if not cfg.i18n_dir.exists():
        raise FileNotFoundError(f"i18nDir 不存在：{cfg.i18n_dir}")

    module_dirs = data.list_module_dirs(cfg.i18n_dir)
    if not module_dirs:
        print(f"⚠️ i18nDir 下没有业务子目录：{cfg.i18n_dir}")
        return

    src_code = cfg.source_locale.code
    src_lang_name = cfg.source_locale.name_en  # 用于 LLM：English
    model = cfg.openai_model

    targets = cfg.target_locales
    if not targets:
        print("⚠️ target_locales 为空，跳过。")
        return

    mode = "增量" if incremental else "全量"
    print(f"🌍 开始{mode}翻译：source={src_code}({src_lang_name}) -> {[t.code for t in targets]}")

    for md in module_dirs:
        src_file = md / data.expected_i18n_filename(md, src_code)
        if not src_file.exists():
            print(f"⚠️ 跳过模块 {md.name}: 缺少 source 文件 {src_file.name}")
            continue

        src_obj = data.read_json(src_file)  # ✅ 保证 flat
        src_kv = _normal_kv(src_obj)         # 去掉 @@*

        if not src_kv:
            print(f"⚠️ 模块 {md.name}: source 无普通 key，跳过")
            continue

        for tgt in targets:
            tgt_code = tgt.code
            tgt_lang_name = tgt.name_en  # 用于 LLM：Traditional Chinese

            tgt_file = md / data.expected_i18n_filename(md, tgt_code)
            if tgt_file.exists():
                tgt_obj = data.read_json(tgt_file)
            else:
                # 缺文件也能翻译：先给最小骨架（@@locale 固定第一位由 sort_json_keys 保证）
                tgt_obj = {data.LOCALE_META_KEY: tgt_code}
                tgt_obj = data.sort_json_keys(tgt_obj)
                data.write_json(tgt_file, tgt_obj)

            tgt_kv = _normal_kv(tgt_obj)

            if incremental:
                need = _compute_incremental_pairs(src_kv, tgt_kv)
                if not need:
                    print(f"✅ {md.name} / {tgt_file.name}: 无需翻译")
                    continue
                src_for_translate = need
            else:
                # 全量：全部普通 key 都翻译
                src_for_translate = dict(src_kv)

            # 只翻译非空字符串（None/空串不翻）
            src_for_translate = {k: v for k, v in src_for_translate.items() if isinstance(v, str) and v.strip()}
            if not src_for_translate:
                print(f"⚠️ {md.name} / {tgt_file.name}: 无可翻译字符串 key")
                continue

            prompt_en = _build_prompt_en(cfg, target_code=tgt_code)

            print(f"➡️  {md.name} / {tgt_file.name}: 翻译 {len(src_for_translate)} 个 key...")

            out = translate_flat_dict(
                prompt_en=prompt_en,
                src_dict=src_for_translate,
                src_lang=src_lang_name,      # ✅ 用 name_en
                tgt_locale=tgt_lang_name,    # ✅ 用 name_en
                model=model,
                api_key=None,                # ✅ 不关心 OPENAI_API_KEY
            )

            # 合并回 target：保留 target 的 @@* 元字段，更新/覆盖普通 key
            merged = dict(tgt_obj)  # 包含 @@locale 等元字段
            for k, v in out.items():
                if data.is_meta_key(k):
                    continue
                merged[k] = v

            # 让 @@locale 固定第一位 + 其它 key 排序（与 sort 规则一致）
            merged = data.sort_json_keys(merged)
            data.write_json(tgt_file, merged)

            print(f"✅ 写入 {tgt_file}")

    print("🎉 翻译完成。")


def _normal_kv(obj: Dict[str, Any]) -> Dict[str, Any]:
    """只保留普通 key（排除 @@*）。"""
    return {k: v for k, v in obj.items() if not data.is_meta_key(k)}


def _compute_incremental_pairs(src: Dict[str, Any], tgt: Dict[str, Any]) -> Dict[str, str]:
    """
    增量：src 有，tgt 缺 / None / 空字符串 -> 需要翻译
    """
    out: Dict[str, str] = {}
    for k, v in src.items():
        if k not in tgt:
            out[k] = v
            continue
        tv = tgt.get(k)
        if tv is None:
            out[k] = v
            continue
        if isinstance(tv, str) and not tv.strip():
            out[k] = v
            continue
    return out


def _build_prompt_en(cfg: data.I18nConfig, target_code: str) -> Optional[str]:
    """
    prompt 规则：
    - prompts.default_en
    - prompts.by_locale_en[code]（可选）
    translate_flat_dict 会把 prompt_en 拼进 system prompt
    """
    prompts = cfg.prompts or {}
    default_en = (prompts.get("default_en") or "").strip()
    by_locale_en = prompts.get("by_locale_en") or {}
    extra = (by_locale_en.get(target_code) or "").strip() if isinstance(by_locale_en, dict) else ""

    parts = [p for p in [default_en, extra] if p]
    return "\n\n".join(parts) if parts else None
