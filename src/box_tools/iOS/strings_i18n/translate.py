from __future__ import annotations

import os
import time
from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import data

from box_tools._share.openai_translate.translate import translate_flat_dict

# -------------------------
# Task / Result（对齐 slang_i18n 的结构风格）
# -------------------------

@dataclass(frozen=True)
class _Task:
    idx: int
    total: int
    phase: str                 # "base->core" | "source->target"
    src_code: str
    src_lang_name: str
    tgt_code: str
    tgt_lang_name: str
    model: str
    prompt_en: Optional[str]
    base_file: Path
    tgt_file: Path
    base_preamble: List[str]   # 仅用于复制注释（可选）
    base_entries: List[data.StringsEntry]
    tgt_preamble: List[str]
    tgt_entries: List[data.StringsEntry]
    src_for_translate: Dict[str, str]  # 本批次要提交的 key->src_text（已过滤非空字符串）


@dataclass(frozen=True)
class _TaskResult:
    idx: int
    total: int
    phase: str
    src_lang_name: str
    tgt_code: str
    tgt_lang_name: str
    tgt_file: Path
    tgt_preamble: List[str]
    tgt_entries: List[data.StringsEntry]
    src_for_translate: Dict[str, str]
    out: Dict[str, Any]
    success_keys: int
    batch_sec: float


# -------------------------
# Public entry
# -------------------------

def run_translate(cfg: data.StringsI18nConfig, incremental: bool = True) -> None:
    """strings_i18n 翻译入口（框架版）

    内部按两个阶段执行（都为增量/可切全量）：
      1) base_locale -> core_locales
      2) source_locale(pivot) -> target_locales

    注意：这里只搭框架，具体模型/参数由 options/prompts 决定。
    """
    mode = "增量" if incremental else "全量"
    print("🌍 translate")
    print(f"- 模式: {mode}")
    print(f"- Base:   {cfg.base_locale.code} ({cfg.base_locale.name_en})")
    print(f"- Source: {cfg.source_locale.code} ({cfg.source_locale.name_en})")
    print(f"- Core:   {[x.code for x in cfg.core_locales]}")
    print(f"- Targets:{len(cfg.target_locales)}")

    # Phase 1
    translate_base_to_core(cfg, incremental=incremental)

    # Phase 2
    translate_source_to_target(cfg, incremental=incremental)

    # 统一排序/清理（维持工程稳定性）
    print("🔧 translate 后执行 sort（保证格式一致）...")
    data.run_sort(cfg)


def translate_base_to_core(cfg: data.StringsI18nConfig, incremental: bool = True) -> None:
    """阶段 1：base_locale -> core_locales（增量翻译入口）"""
    base_dir, base_files = _load_base_files(cfg)

    # 目标：core_locales（排除 base 自己；也排除 source 若重合）
    targets = [x for x in cfg.core_locales if x.code not in {cfg.base_locale.code}]
    if not targets:
        print("⚠️ base->core：core_locales 为空或仅包含 base_locale，跳过。")
        return

    print("\n🧩 Phase 1: base → core")
    print(f"- src: {cfg.base_locale.code} ({cfg.base_locale.name_en})")
    print(f"- tgt: {[t.code for t in targets]}")

    tasks, total_keys = _build_tasks(
        cfg=cfg,
        phase="base->core",
        src_locale=cfg.base_locale,
        targets=targets,
        base_files=base_files,
        base_dir=base_dir,
        incremental=incremental,
        pivot_locale=None,
    )
    _run_tasks_and_write(cfg, tasks, total_keys)


def translate_source_to_target(cfg: data.StringsI18nConfig, incremental: bool = True) -> None:
    """阶段 2：source_locale(pivot) -> target_locales（增量翻译入口）

    pivot 缺 key/空时：回退使用 Base 的 value 作为 src_text。
    """
    base_dir, base_files = _load_base_files(cfg)

    targets = [x for x in cfg.target_locales if x.code not in {cfg.base_locale.code, cfg.source_locale.code}]
    if not targets:
        print("⚠️ source->target：target_locales 为空或与 base/source 重合，跳过。")
        return

    print("\n🧩 Phase 2: source(pivot) → target")
    print(f"- src: {cfg.source_locale.code} ({cfg.source_locale.name_en})")
    print(f"- tgt: {len(targets)} locales")

    tasks, total_keys = _build_tasks(
        cfg=cfg,
        phase="source->target",
        src_locale=cfg.source_locale,
        targets=targets,
        base_files=base_files,
        base_dir=base_dir,
        incremental=incremental,
        pivot_locale=cfg.source_locale,
    )
    _run_tasks_and_write(cfg, tasks, total_keys)


# -------------------------
# Core framework
# -------------------------

def _load_base_files(cfg: data.StringsI18nConfig) -> Tuple[Path, List[Path]]:
    base_dir = (cfg.lang_root / cfg.base_folder).resolve()
    if not base_dir.exists():
        raise data.ConfigError(f"未找到 base_folder: {base_dir}")
    base_files = sorted(base_dir.glob("*.strings"))
    if not base_files:
        raise data.ConfigError(f"Base.lproj 下未找到任何 .strings：{base_dir}")
    return base_dir, base_files


def _get_max_workers(cfg: data.StringsI18nConfig) -> int:
    v = None
    if isinstance(cfg.options, dict):
        v = cfg.options.get("max_workers", cfg.options.get("maxWorkers"))
    try:
        return int(v) if v is not None else 0
    except Exception:
        return 0


def _compute_workers(max_workers_cfg: int, total_batches: int) -> int:
    if total_batches <= 0:
        return 1
    if max_workers_cfg and max_workers_cfg > 0:
        return max(1, min(max_workers_cfg, total_batches))
    cpu = os.cpu_count() or 4
    guess = max(2, min(8, max(2, cpu // 2)))
    return min(guess, total_batches)


def _get_model(cfg: data.StringsI18nConfig) -> str:
    # 兼容 options 里可能出现的 model/openai_model
    if isinstance(cfg.options, dict):
        m = cfg.options.get("model") or cfg.options.get("openai_model") or cfg.options.get("openaiModel")
        if isinstance(m, str) and m.strip():
            return m.strip()
    return os.environ.get("OPENAI_MODEL", "gpt-4o-mini")


def _build_prompt_en(cfg: data.StringsI18nConfig, target_code: str) -> Optional[str]:
    prompts = cfg.prompts or {}
    default_en = (prompts.get("default_en") or "").strip()
    by_locale_en = prompts.get("by_locale_en") or {}
    extra = (by_locale_en.get(target_code) or "").strip() if isinstance(by_locale_en, dict) else ""
    parts = [p for p in [default_en, extra] if p]
    return "\n\n".join(parts) if parts else None


def _normal_entries(entries: List[data.StringsEntry]) -> List[data.StringsEntry]:
    return [e for e in entries if not e.key.startswith("@@")]


def _only_non_empty_strings(kv: Dict[str, str]) -> Dict[str, str]:
    return {k: v for k, v in kv.items() if isinstance(v, str) and v.strip()}


def _compute_incremental_pairs(src_map: Dict[str, str], tgt_map: Dict[str, data.StringsEntry]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for k, v in src_map.items():
        if k not in tgt_map:
            out[k] = v
            continue
        tv = tgt_map[k].value
        if tv is None:
            out[k] = v
            continue
        if isinstance(tv, str) and not tv.strip():
            out[k] = v
            continue
    return out


def _build_tasks(
    *,
    cfg: data.StringsI18nConfig,
    phase: str,
    src_locale: data.Locale,
    targets: List[data.Locale],
    base_files: List[Path],
    base_dir: Path,
    incremental: bool,
    pivot_locale: Optional[data.Locale],
) -> Tuple[List[_Task], int]:
    tasks: List[_Task] = []
    staged: List[Tuple[data.Locale, Path, Path, List[str], List[data.StringsEntry], List[str], List[data.StringsEntry], Dict[str, str]]] = []
    total_keys = 0

    model = _get_model(cfg)

    for tgt in targets:
        lproj = (cfg.lang_root / f"{tgt.code}.lproj").resolve()
        lproj.mkdir(parents=True, exist_ok=True)

        for bf in base_files:
            tf = lproj / bf.name
            if not tf.exists():
                tf.write_text("", encoding="utf-8")

            base_preamble, base_entries = data.parse_strings_file(bf)
            tgt_preamble, tgt_entries = data.parse_strings_file(tf)

            # key->value（只取普通 key）
            base_map: Dict[str, str] = {e.key: e.value for e in _normal_entries(base_entries)}
            if not base_map:
                continue

            tgt_entry_map: Dict[str, data.StringsEntry] = {e.key: e for e in tgt_entries}

            # 生成 src_map（phase2 用 pivot 文案；缺失回退 base）
            if phase == "source->target" and pivot_locale is not None:
                pivot_file = (cfg.lang_root / f"{pivot_locale.code}.lproj" / bf.name).resolve()
                _, pivot_entries = data.parse_strings_file(pivot_file)
                pivot_map: Dict[str, str] = {e.key: e.value for e in _normal_entries(pivot_entries)}

                src_map: Dict[str, str] = {}
                for k, base_val in base_map.items():
                    pv = pivot_map.get(k)
                    if isinstance(pv, str) and pv.strip():
                        src_map[k] = pv
                    else:
                        # pivot 缺失/空：回退 base
                        if isinstance(base_val, str) and base_val.strip():
                            src_map[k] = base_val
            else:
                src_map = base_map

            # 过滤空源文案（不提交）
            src_map = _only_non_empty_strings(src_map)
            if not src_map:
                continue

            if incremental:
                need_map = _compute_incremental_pairs(src_map, tgt_entry_map)
                src_for_translate = _only_non_empty_strings(need_map)
            else:
                src_for_translate = src_map

            if not src_for_translate:
                continue

            staged.append((tgt, bf, tf, base_preamble, base_entries, tgt_preamble, tgt_entries, src_for_translate))

    total_batches = len(staged)
    if total_batches == 0:
        return [], 0

    for i, (tgt, bf, tf, base_preamble, base_entries, tgt_preamble, tgt_entries, src_for_translate) in enumerate(staged, start=1):
        total_keys += len(src_for_translate)

        tasks.append(
            _Task(
                idx=i,
                total=total_batches,
                phase=phase,
                src_code=src_locale.code,
                src_lang_name=src_locale.name_en,
                tgt_code=tgt.code,
                tgt_lang_name=tgt.name_en,
                model=model,
                prompt_en=_build_prompt_en(cfg, target_code=tgt.code),
                base_file=bf,
                tgt_file=tf,
                base_preamble=base_preamble,
                base_entries=base_entries,
                tgt_preamble=tgt_preamble,
                tgt_entries=tgt_entries,
                src_for_translate=src_for_translate,
            )
        )

    return tasks, total_keys


def _translate_text_map(*, t: _Task) -> Dict[str, Any]:
    # 与 slang_i18n 完全一致的调用方式
    return translate_flat_dict(
        prompt_en=t.prompt_en,
        src_dict=t.src_for_translate,
        src_lang=t.src_lang_name,     # ✅ name_en
        tgt_locale=t.tgt_lang_name,   # ✅ name_en
        model=t.model,
        api_key=None,                 # ✅ 不关心 OPENAI_API_KEY
    )

def _translate_one(t: _Task) -> _TaskResult:
    t0 = time.perf_counter()
    out = _translate_text_map(t=t)  # cfg 当前不需要传入 translate_flat_dict
    t1 = time.perf_counter()

    success = 0
    for k, v in out.items():
        if k.startswith("@@"):
            continue
        if isinstance(v, str) and v.strip():
            success += 1

    return _TaskResult(
        idx=t.idx,
        total=t.total,
        phase=t.phase,
        src_lang_name=t.src_lang_name,
        tgt_code=t.tgt_code,
        tgt_lang_name=t.tgt_lang_name,
        tgt_file=t.tgt_file,
        tgt_preamble=t.tgt_preamble,
        tgt_entries=t.tgt_entries,
        src_for_translate=t.src_for_translate,
        out=out,
        success_keys=success,
        batch_sec=(t1 - t0),
    )


def _print_translated_pairs(
    *,
    src_lang_name: str,
    tgt_lang_name: str,
    src_dict: Dict[str, str],
    out: Dict[str, Any],
    max_print: int,
) -> None:
    printed = 0
    total = len(src_dict)

    for k, src_text in src_dict.items():
        if printed >= max_print:
            remain = total - printed
            if remain > 0:
                print(f"   ...（已截断，剩余 {remain} 条未打印）...")
            break

        tgt_text = out.get(k)
        if not isinstance(tgt_text, str) or not tgt_text.strip():
            continue

        print(f"   - {k}")
        print(f"     {src_lang_name}: {src_text}")
        print(f"     {tgt_lang_name}: {tgt_text}")
        printed += 1


def _run_tasks_and_write(cfg: data.StringsI18nConfig, tasks: List[_Task], total_keys: int) -> None:
    total_batches = len(tasks)
    if total_batches == 0 or total_keys == 0:
        print("✅ 没有需要翻译的 key")
        return

    max_workers_cfg = _get_max_workers(cfg)
    max_workers = _compute_workers(max_workers_cfg, total_batches)
    if max_workers_cfg == 0:
        print(f"- 并发: {max_workers} workers（max_workers=0/自动）")
    else:
        print(f"- 并发: {max_workers} workers（max_workers={max_workers_cfg}）")

    max_print = int(os.environ.get("BOX_STRINGS_I18N_MAX_PRINT", "50") or "50")

    # 提交任务时先按顺序打印 loading（对齐 slang_i18n）
    for t in tasks:
        print(
            f"⏳ [{t.idx}/{t.total}] ({t.phase}) {t.base_file.name} → {t.tgt_code}  "
            f"{t.src_lang_name} → {t.tgt_lang_name}  | {len(t.src_for_translate)} key ..."
        )

    start_all = time.perf_counter()
    sum_batch_sec = 0.0

    done_files = 0
    done_keys = 0

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = [ex.submit(_translate_one, t) for t in tasks]

        for fut in as_completed(futures):
            r = fut.result()
            sum_batch_sec += r.batch_sec

            # 主线程写回：合并原 entries + 新翻译，保留 preamble
            _, base_entries = data.parse_strings_file(tasks[r.idx - 1].base_file)  # 只用于复制注释（稳妥起见重新读）
            base_comments_map: Dict[str, List[str]] = {e.key: e.comments for e in _normal_entries(base_entries)}

            tgt_entry_map: Dict[str, data.StringsEntry] = {e.key: e for e in r.tgt_entries}

            # 合并：只写入非空字符串；保留原注释；若原无注释则用 base 注释（如果有）
            for k, v in r.out.items():
                if k.startswith("@@"):
                    continue
                if not isinstance(v, str) or not v.strip():
                    continue

                existing = tgt_entry_map.get(k)
                if existing and existing.comments:
                    comments = existing.comments
                else:
                    comments = base_comments_map.get(k, [])

                tgt_entry_map[k] = data.StringsEntry(key=k, value=v, comments=comments)

            new_entries = sorted(tgt_entry_map.values(), key=lambda e: e.key)
            data.write_strings_file(r.tgt_file, r.tgt_preamble, new_entries, group_by_prefix=False)

            done_files += 1
            done_keys += r.success_keys

            elapsed_all = time.perf_counter() - start_all
            print(
                f"✅ [{r.idx}/{r.total}] ({r.phase}) {r.tgt_code}  "
                f"+{r.success_keys} key  | {r.batch_sec:.2f}s  | 累计 {elapsed_all:.2f}s"
            )
            _print_translated_pairs(
                src_lang_name=r.src_lang_name,
                tgt_lang_name=r.tgt_lang_name,
                src_dict=r.src_for_translate,
                out=r.out,
                max_print=max_print,
            )

    total_elapsed = time.perf_counter() - start_all
    print("\n🎉 Phase 完成汇总")
    print(f"- 批次: {total_batches}")
    print(f"- 翻译 key: {done_keys}/{total_keys}")
    print(f"- 总耗时(墙钟): {total_elapsed:.2f}s")
    print(f"- 累计翻译耗时(∑每条): {sum_batch_sec:.2f}s")
    if total_elapsed > 0 and sum_batch_sec > 0:
        saved = sum_batch_sec - total_elapsed
        if saved > 0:
            print(f"- 并发节省: {saved:.2f}s")
        print(f"- 加速比: {sum_batch_sec / total_elapsed:.2f}x")