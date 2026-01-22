# translate.py
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from box_tools._share.openai_translate.translate import translate_flat_dict

from . import data


def run_translate(cfg: data.I18nConfig, incremental: bool = True) -> None:
    """
    增量翻译（默认）：
      - source 文件：<module>_<src_code>.i18n.json（例如 about_en.i18n.json）
      - target 文件：<module>_<tgt_code>.i18n.json（例如 about_zh_Hant.i18n.json）
      - 对比 source/target：target 缺 key / None / 空字符串 => 翻译补齐；否则跳过（不打印）
      - 仅处理普通 key（忽略 @@* 元字段）
      - JSON 必须是 flat（由 data.read_json/write_json 保证）

    全量翻译：
      - 以 source 覆盖生成 target（仍保留 target 原有 @@* 元字段）
    """
    if not cfg.i18n_dir.exists():
        raise FileNotFoundError(f"i18nDir 不存在：{cfg.i18n_dir}")

    module_dirs = data.list_module_dirs(cfg.i18n_dir)
    if not module_dirs:
        print(f"⚠️ i18nDir 下没有业务子目录：{cfg.i18n_dir}")
        return

    src_code = cfg.source_locale.code
    src_lang_name = cfg.source_locale.name_en
    model = cfg.openai_model

    targets = cfg.target_locales
    if not targets:
        print("⚠️ target_locales 为空，跳过。")
        return

    mode = "增量" if incremental else "全量"

    # 预计算：总批次（有实际要翻译的 file 对）+ 总 key（仅统计非空字符串）
    total_batches, total_keys, per_lang_total = _precompute_plan(
        cfg=cfg,
        module_dirs=module_dirs,
        src_code=src_code,
        targets=targets,
        incremental=incremental,
    )

    print("🌍 翻译开始")
    print(f"- 模式: {mode}")
    print(f"- Source: {src_code} ({src_lang_name})")
    print(f"- Targets: {[t.code for t in targets]}")
    print(f"- i18nDir: {cfg.i18n_dir}")
    print(f"- 总批次: {total_batches}（仅包含需要翻译的文件）")
    print(f"- 总 key: {total_keys}")

    if total_batches == 0 or total_keys == 0:
        print("✅ 没有需要翻译的 key")
        return

    # 统计运行
    done_batches = 0
    done_keys = 0
    per_lang_done: Dict[str, int] = {t.code: 0 for t in targets}
    start_all = time.perf_counter()

    # 控制每批打印多少条翻译内容（避免日志爆炸）
    # 你也可以把它改成 cfg 里可配置的字段
    MAX_PRINT_PER_BATCH = 200

    for md in module_dirs:
        # source 文件名严格按 data.py 规则生成
        src_file = md / data.expected_i18n_filename(md, src_code)
        if not src_file.exists():
            continue

        src_obj = data.read_json(src_file)
        src_kv = _normal_kv(src_obj)
        if not src_kv:
            continue

        for tgt in targets:
            tgt_code = tgt.code
            tgt_lang_name = tgt.name_en

            tgt_file = md / data.expected_i18n_filename(md, tgt_code)
            if tgt_file.exists():
                tgt_obj = data.read_json(tgt_file)
            else:
                # 目标文件不存在：内存里先给最小骨架，翻译后落盘
                tgt_obj = {data.LOCALE_META_KEY: tgt_code}

            tgt_kv = _normal_kv(tgt_obj)

            if incremental:
                need_map = _compute_incremental_pairs(src_kv, tgt_kv)
                src_for_translate = _only_non_empty_strings(need_map)
            else:
                src_for_translate = _only_non_empty_strings(src_kv)

            n_keys = len(src_for_translate)
            if n_keys == 0:
                continue  # ✅ 无需翻译：不打印

            # 这批属于“有效批次”
            done_batches += 1
            idx = done_batches

            prompt_en = _build_prompt_en(cfg, target_code=tgt_code)

            # loading 行（更紧凑）
            t0 = time.perf_counter()
            print(
                f"⏳ [{idx}/{total_batches}] {md.name} → {tgt_code}  "
                f"{src_lang_name} → {tgt_lang_name}  | {n_keys} key ..."
            )

            out = translate_flat_dict(
                prompt_en=prompt_en,
                src_dict=src_for_translate,
                src_lang=src_lang_name,     # ✅ 用 name_en
                tgt_locale=tgt_lang_name,   # ✅ 用 name_en
                model=model,
                api_key=None,               # ✅ 不关心 OPENAI_API_KEY
            )

            t1 = time.perf_counter()
            batch_sec = t1 - t0

            # 合并写回：保留 @@* 元字段，只覆盖普通 key
            merged = dict(tgt_obj)  # 包含 @@locale 等元字段
            success_keys = 0
            for k, v in out.items():
                if data.is_meta_key(k):
                    continue
                # out 里只要是非空字符串就算成功（避免 None/空串污染）
                if isinstance(v, str) and v.strip():
                    merged[k] = v
                    success_keys += 1

            merged = data.sort_json_keys(merged)
            data.write_json(tgt_file, merged)

            done_keys += success_keys
            per_lang_done[tgt_code] = per_lang_done.get(tgt_code, 0) + success_keys

            elapsed_all = time.perf_counter() - start_all
            print(
                f"✅ [{idx}/{total_batches}] {md.name} → {tgt_code}  "
                f"+{success_keys} key  | {batch_sec:.2f}s  | 累计 {elapsed_all:.2f}s"
            )

            # ✅ 打印本次翻译内容（源语言 + 目标语言）
            _print_translated_pairs(
                src_lang_name=src_lang_name,
                tgt_lang_name=tgt_lang_name,
                src_dict=src_for_translate,
                out=out,
                max_print=MAX_PRINT_PER_BATCH,
            )

    total_elapsed = time.perf_counter() - start_all

    # 完成汇总：源语言 + 目标语言 + key 数
    print("\n🎉 翻译完成汇总")
    print(f"- Source: {src_code} ({src_lang_name})")
    print(f"- 总批次: {done_batches}/{total_batches}")
    print(f"- 总翻译 key: {done_keys}/{total_keys}")
    print(f"- 总耗时: {total_elapsed:.2f}s")
    if total_elapsed > 0:
        print(f"- 平均速度: {done_keys / total_elapsed:.2f} key/s")

    print("\n📌 目标语言翻译统计（按配置顺序，仅展示有产出的）")
    for tgt in targets:
        code = tgt.code
        name = tgt.name_en
        cnt = per_lang_done.get(code, 0)
        if cnt > 0:
            print(f"- {code} ({name}): {cnt} key")


def _precompute_plan(
        cfg: data.I18nConfig,
        module_dirs: List[Any],
        src_code: str,
        targets: List[Any],
        incremental: bool,
) -> tuple[int, int, Dict[str, int]]:
    """
    预计算“需要翻译”的批次数与 key 数，便于输出 [i/total] 和总耗时统计。
    """
    total_batches = 0
    total_keys = 0
    per_lang_total: Dict[str, int] = {t.code: 0 for t in targets}

    for md in module_dirs:
        src_file = md / data.expected_i18n_filename(md, src_code)
        if not src_file.exists():
            continue

        src_obj = data.read_json(src_file)
        src_kv = _normal_kv(src_obj)
        if not src_kv:
            continue

        for tgt in targets:
            tgt_code = tgt.code
            tgt_file = md / data.expected_i18n_filename(md, tgt_code)

            if tgt_file.exists():
                tgt_obj = data.read_json(tgt_file)
            else:
                tgt_obj = {data.LOCALE_META_KEY: tgt_code}

            tgt_kv = _normal_kv(tgt_obj)

            if incremental:
                need_map = _compute_incremental_pairs(src_kv, tgt_kv)
                src_for_translate = _only_non_empty_strings(need_map)
            else:
                src_for_translate = _only_non_empty_strings(src_kv)

            n = len(src_for_translate)
            if n <= 0:
                continue

            total_batches += 1
            total_keys += n
            per_lang_total[tgt_code] = per_lang_total.get(tgt_code, 0) + n

    return total_batches, total_keys, per_lang_total


def _normal_kv(obj: Dict[str, Any]) -> Dict[str, Any]:
    """只保留普通 key（排除 @@* 元字段）。"""
    return {k: v for k, v in obj.items() if not data.is_meta_key(k)}


def _only_non_empty_strings(kv: Dict[str, Any]) -> Dict[str, str]:
    """只保留非空字符串 value。"""
    out: Dict[str, str] = {}
    for k, v in kv.items():
        if isinstance(v, str) and v.strip():
            out[k] = v
    return out


def _compute_incremental_pairs(src: Dict[str, Any], tgt: Dict[str, Any]) -> Dict[str, Any]:
    """
    增量：src 有，tgt 缺 / None / 空字符串 -> 需要翻译
    """
    out: Dict[str, Any] = {}
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
    """
    prompts = cfg.prompts or {}
    default_en = (prompts.get("default_en") or "").strip()
    by_locale_en = prompts.get("by_locale_en") or {}
    extra = (by_locale_en.get(target_code) or "").strip() if isinstance(by_locale_en, dict) else ""

    parts = [p for p in [default_en, extra] if p]
    return "\n\n".join(parts) if parts else None


def _print_translated_pairs(
        src_lang_name: str,
        tgt_lang_name: str,
        src_dict: Dict[str, str],
        out: Dict[str, Any],
        max_print: int = 200,
) -> None:
    """
    打印本次翻译成功的内容（源语言 + 目标语言）。
    只打印：
      - out 里存在该 key
      - 且 out[key] 是非空字符串
    """
    printed = 0
    total = len(src_dict)

    for k, src_text in src_dict.items():
        if printed >= max_print:
            remain = total - printed
            if remain > 0:
                print(f"   ...（已截断，剩余 {remain} 条未打印）...")
            break

        if k not in out:
            continue

        tgt_text = out.get(k)
        if not isinstance(tgt_text, str) or not tgt_text.strip():
            continue

        print(f"   - {k}")
        print(f"     {src_lang_name}: {src_text}")
        print(f"     {tgt_lang_name}: {tgt_text}")
        printed += 1
