from __future__ import annotations

import os
import time
from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from . import data

# 尝试复用 slang_i18n 同款翻译入口（若你的运行环境具备 box_tools._share.openai_translate）
try:
    from box_tools._share.openai_translate.translate import translate_flat_dict  # type: ignore
except Exception:  # pragma: no cover
    translate_flat_dict = None  # type: ignore


def _is_meta_key(key: str) -> bool:
    return key.startswith("@@")


def _only_non_empty(src: Dict[str, str]) -> Dict[str, str]:
    return {k: v for k, v in src.items() if isinstance(v, str) and v.strip() and not _is_meta_key(k)}


def _compute_incremental_pairs(src_kv: Dict[str, str], tgt_kv: Dict[str, str]) -> Dict[str, str]:
    """返回需要翻译的 key -> src_text（仅按“目标缺失/空值”判定）。"""
    out: Dict[str, str] = {}
    for k, v in src_kv.items():
        if _is_meta_key(k):
            continue
        if not isinstance(v, str) or not v.strip():
            continue
        tv = tgt_kv.get(k)
        if tv is None or (isinstance(tv, str) and not tv.strip()):
            out[k] = v
    return out


def _entries_to_kv(entries: List[data.StringsEntry]) -> Dict[str, str]:
    return {e.key: e.value for e in entries}


def _merge_translated(
    tgt_entries: List[data.StringsEntry],
    translations: Dict[str, str],
) -> List[data.StringsEntry]:
    """把翻译结果合并进 target entries（保留已有 comment；新增 entry comment 为空）。"""
    by_key: Dict[str, data.StringsEntry] = {e.key: e for e in tgt_entries}

    for k, v in translations.items():
        if _is_meta_key(k):
            continue
        if not isinstance(v, str) or not v.strip():
            continue
        if k in by_key:
            e = by_key[k]
            by_key[k] = data.StringsEntry(key=e.key, value=v, comments=e.comments)
        else:
            by_key[k] = data.StringsEntry(key=k, value=v, comments=[])

    return list(by_key.values())


def _get_max_workers(cfg: data.StringsI18nConfig) -> int:
    # 兼容 options.maxWorkers / options.max_workers
    opt = cfg.options or {}
    v = opt.get("maxWorkers", None)
    if v is None:
        v = opt.get("max_workers", None)
    try:
        return int(v) if v is not None else 0
    except Exception:
        return 0


def _compute_workers(max_workers_cfg: int, total_tasks: int) -> int:
    if total_tasks <= 0:
        return 1
    if max_workers_cfg and max_workers_cfg > 0:
        return max(1, min(max_workers_cfg, total_tasks))
    cpu = os.cpu_count() or 4
    guess = max(2, min(8, max(2, cpu // 2)))
    return min(guess, total_tasks)


def _compose_prompt(cfg: data.StringsI18nConfig, tgt_code: str) -> Optional[str]:
    prompts = cfg.prompts or {}
    default_en = prompts.get("default_en")
    by_locale = (prompts.get("by_locale_en") or {})
    extra = by_locale.get(tgt_code) or by_locale.get(tgt_code.replace("-", "_"))
    parts = []
    if isinstance(default_en, str) and default_en.strip():
        parts.append(default_en.strip())
    if isinstance(extra, str) and extra.strip():
        parts.append(extra.strip())
    if not parts:
        return None
    return "\n\n".join(parts)


def _translate_text_map(
    *,
    cfg: data.StringsI18nConfig,
    src_map: Dict[str, str],
    src_lang_name: str,
    tgt_code: str,
    tgt_lang_name: str,
    phase: str,
) -> Dict[str, str]:
    """翻译一批 key->src_text，返回 key->translated_text。

    参考 slang_i18n：优先走 translate_flat_dict；否则用占位 stub（可运行）。
    """
    src_map = _only_non_empty(src_map)
    if not src_map:
        return {}

    model = (cfg.options or {}).get("model") or (cfg.options or {}).get("openai_model") or "gpt-4.1-mini"
    prompt_en = _compose_prompt(cfg, tgt_code)

    if translate_flat_dict is None:
        # fallback：占位翻译（便于你验证流程，不会误伤已有翻译）
        return {k: f"[[{tgt_code}|{phase}]] {v}" for k, v in src_map.items()}

    # translate_flat_dict 期望：src_map 是 flat dict[str,str]
    # 备注：这里不传 temperature 等高级参数，保持骨架稳定；需要的话可从 cfg.options 扩展
    out = translate_flat_dict(
        src_lang_name=src_lang_name,
        tgt_lang_name=tgt_lang_name,
        src_kv=src_map,
        model=model,
        prompt_en=prompt_en,
    )
    # translate_flat_dict 可能返回 Any；这里做最小保障
    if not isinstance(out, dict):
        return {}
    return {k: (v if isinstance(v, str) else str(v)) for k, v in out.items()}


@dataclass(frozen=True)
class _Task:
    idx: int
    total: int
    phase: str
    src_code: str
    src_lang_name: str
    tgt_code: str
    tgt_lang_name: str
    base_file: Path
    tgt_file: Path
    tgt_preamble: List[str]
    tgt_entries: List[data.StringsEntry]
    src_for_translate: Dict[str, str]  # 本次提交的 key->src_text


@dataclass(frozen=True)
class _TaskResult:
    idx: int
    total: int
    phase: str
    tgt_code: str
    tgt_lang_name: str
    tgt_file: Path
    tgt_preamble: List[str]
    tgt_entries: List[data.StringsEntry]
    src_for_translate: Dict[str, str]
    out: Dict[str, str]
    batch_sec: float
    success_keys: int


def _build_tasks_phase(
    *,
    cfg: data.StringsI18nConfig,
    phase: str,
    src_locale: data.Locale,
    tgt_locales: List[data.Locale],
    base_files: List[Path],
    pivot_dir: Optional[Path] = None,
    incremental: bool = True,
) -> Tuple[List[_Task], int, Dict[str, int]]:
    """构建一批任务：每个 (tgt_locale, file) 一个 task。"""
    tasks: List[_Task] = []
    total_keys = 0
    per_lang_total: Dict[str, int] = {t.code: 0 for t in tgt_locales}

    # base_dir 永远是 src_key 的全集来源
    base_dir = (cfg.lang_root / cfg.base_folder).resolve()

    staged: List[Tuple[Path, data.Locale, Dict[str, str], List[str], List[data.StringsEntry]]] = []
    # (tgt_file, tgt_locale, src_for_translate, tgt_preamble, tgt_entries)

    for base_file in base_files:
        base_preamble, base_entries = data.parse_strings_file(base_file)
        base_kv = _entries_to_kv(base_entries)

        # phase2: 如果给了 pivot_dir，则 src_text 取 pivot；否则取 base
        pivot_kv: Optional[Dict[str, str]] = None
        if pivot_dir is not None:
            pivot_file = pivot_dir / base_file.name
            p_pre, p_entries = data.parse_strings_file(pivot_file)
            pivot_kv = _entries_to_kv(p_entries)

        for tgt in tgt_locales:
            tgt_dir = cfg.lang_root / f"{tgt.code}.lproj"
            tgt_file = tgt_dir / base_file.name
            tgt_preamble, tgt_entries = data.parse_strings_file(tgt_file)
            tgt_kv = _entries_to_kv(tgt_entries)

            if pivot_kv is not None:
                # src 为 pivot，缺失则 fallback base
                src_kv = dict(base_kv)
                for k, v in (pivot_kv or {}).items():
                    if isinstance(v, str) and v.strip():
                        src_kv[k] = v
            else:
                src_kv = base_kv

            if incremental:
                need_map = _compute_incremental_pairs(src_kv, tgt_kv)
            else:
                need_map = {k: v for k, v in src_kv.items() if not _is_meta_key(k) and isinstance(v, str) and v.strip()}

            need_map = _only_non_empty(need_map)
            if not need_map:
                continue

            staged.append((tgt_file, tgt, need_map, tgt_preamble, tgt_entries))
            total_keys += len(need_map)
            per_lang_total[tgt.code] = per_lang_total.get(tgt.code, 0) + len(need_map)

    total_tasks = len(staged)
    for i, (tgt_file, tgt, src_for_translate, tgt_preamble, tgt_entries) in enumerate(staged, start=1):
        tasks.append(
            _Task(
                idx=i,
                total=total_tasks,
                phase=phase,
                src_code=src_locale.code,
                src_lang_name=src_locale.name_en,
                tgt_code=tgt.code,
                tgt_lang_name=tgt.name_en,
                base_file=base_dir / tgt_file.name,
                tgt_file=tgt_file,
                tgt_preamble=tgt_preamble,
                tgt_entries=tgt_entries,
                src_for_translate=src_for_translate,
            )
        )

    return tasks, total_keys, per_lang_total


def _translate_one(cfg: data.StringsI18nConfig, t: _Task) -> _TaskResult:
    start = time.perf_counter()
    out = _translate_text_map(
        cfg=cfg,
        src_map=t.src_for_translate,
        src_lang_name=t.src_lang_name,
        tgt_code=t.tgt_code,
        tgt_lang_name=t.tgt_lang_name,
        phase=t.phase,
    )
    sec = time.perf_counter() - start
    success = sum(1 for _, v in out.items() if isinstance(v, str) and v.strip())
    return _TaskResult(
        idx=t.idx,
        total=t.total,
        phase=t.phase,
        tgt_code=t.tgt_code,
        tgt_lang_name=t.tgt_lang_name,
        tgt_file=t.tgt_file,
        tgt_preamble=t.tgt_preamble,
        tgt_entries=t.tgt_entries,
        src_for_translate=t.src_for_translate,
        out=out,
        batch_sec=sec,
        success_keys=success,
    )


def run_translate(cfg: data.StringsI18nConfig, incremental: bool = True) -> None:
    """
    两阶段增量翻译（对齐 slang_i18n 的并发/写回方式）：
      1) Base -> Core（core_locales，排除 base_locale）
      2) Core(pivot=source_locale) -> Target（target_locales）
         - pivot 缺 key/空值时，fallback Base
    """
    mode = "增量" if incremental else "全量"
    print("🌍 translate")
    print(f"- 模式: {mode}")
    print(f"- Base: {cfg.base_locale.code} ({cfg.base_locale.name_en})")
    print(f"- Pivot(core): {cfg.source_locale.code} ({cfg.source_locale.name_en})")
    print(f"- Core: {[x.code for x in cfg.core_locales]}")
    print(f"- Targets: {len(cfg.target_locales)}")

    # 先做完整性检查（确保各语言文件集齐全）
    data.ensure_strings_files_integrity(cfg)

    base_dir = (cfg.lang_root / cfg.base_folder).resolve()
    if not base_dir.exists():
        raise data.ConfigError(f"未找到 base_folder: {base_dir}")
    base_files = sorted(base_dir.glob("*.strings"))
    if not base_files:
        print("⚠️ Base.lproj 下未找到 *.strings，跳过")
        return

    # phase1: Base -> Core（排除 base_locale）
    core_targets = [l for l in cfg.core_locales if l.code != cfg.base_locale.code]
    tasks1, total_keys1, per_lang_total1 = _build_tasks_phase(
        cfg=cfg,
        phase="base->core",
        src_locale=cfg.base_locale,
        tgt_locales=core_targets,
        base_files=base_files,
        pivot_dir=None,
        incremental=incremental,
    )

    # phase2: Pivot(core=source_locale) -> Target（pivot_dir=source_locale.lproj）
    pivot_dir = cfg.lang_root / f"{cfg.source_locale.code}.lproj"
    tasks2, total_keys2, per_lang_total2 = _build_tasks_phase(
        cfg=cfg,
        phase="core->target",
        src_locale=cfg.source_locale,
        tgt_locales=cfg.target_locales,
        base_files=base_files,
        pivot_dir=pivot_dir,
        incremental=incremental,
    )

    tasks = tasks1 + tasks2
    total_keys = total_keys1 + total_keys2

    if not tasks:
        print("✅ 无需翻译：所有语言均已补齐（或没有可翻译条目）")
        return

    max_workers_cfg = _get_max_workers(cfg)
    max_workers = _compute_workers(max_workers_cfg, len(tasks))
    print(f"- 任务数: {len(tasks)}（base->core={len(tasks1)}, core->target={len(tasks2)}）")
    print(f"- 待翻译 key: {total_keys}（base->core={total_keys1}, core->target={total_keys2}）")
    print(f"- 并发: {max_workers} workers（maxWorkers={max_workers_cfg}）")

    start_all = time.perf_counter()
    sum_batch_sec = 0.0

    # 主线程统计
    done_keys = 0
    per_lang_done: Dict[str, int] = {}

    # 提交任务时打印 loading（保证顺序）
    for t in tasks:
        print(
            f"⏳ [{t.idx}/{t.total}] ({t.phase}) {t.tgt_code}  "
            f"{t.src_lang_name} → {t.tgt_lang_name}  | {len(t.src_for_translate)} key ..."
        )

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = [ex.submit(_translate_one, cfg, t) for t in tasks]

        for fut in as_completed(futures):
            r = fut.result()
            sum_batch_sec += r.batch_sec

            # 合并写回（主线程）
            merged_entries = _merge_translated(r.tgt_entries, r.out)
            # target 语言：按 key 排序（不分组）；Base 的分组规则由 sort 管
            merged_entries = sorted(merged_entries, key=lambda e: e.key)

            # 写回保持 preamble
            data.write_strings_file(r.tgt_file, r.tgt_preamble, merged_entries, group_by_prefix=False)

            done_keys += r.success_keys
            per_lang_done[r.tgt_code] = per_lang_done.get(r.tgt_code, 0) + r.success_keys

            elapsed_all = time.perf_counter() - start_all
            print(
                f"✅ [{r.idx}/{r.total}] ({r.phase}) {r.tgt_code}  "
                f"+{r.success_keys} key  | {r.batch_sec:.2f}s  | 累计 {elapsed_all:.2f}s"
            )

    total_elapsed = time.perf_counter() - start_all
    print("\n🎉 翻译完成汇总")
    print(f"- 总任务: {len(tasks)}")
    print(f"- 总翻译 key: {done_keys}/{total_keys}")
    print(f"- 总耗时(墙钟): {total_elapsed:.2f}s")
    print(f"- 累计翻译耗时(∑每条): {sum_batch_sec:.2f}s")
    if total_elapsed > 0 and sum_batch_sec > 0:
        saved = sum_batch_sec - total_elapsed
        if saved > 0:
            print(f"- 并发节省: {saved:.2f}s")
        print(f"- 加速比: {sum_batch_sec / total_elapsed:.2f}x")
        print(f"- 平均速度: {done_keys / total_elapsed:.2f} key/s")

    # translate 后执行 sort（保证 Base 分组与注释规则、以及其他语言排序一致）
    if (cfg.options or {}).get("sort_after_translate", True):
        print("\n🔧 translate 后执行 sort（保证格式一致）...")
        data.run_sort(cfg)
