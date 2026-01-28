# translate.py
from __future__ import annotations

import os
import time
import threading
from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional, Tuple

from box_tools._share.openai_translate.translate import translate_flat_dict
from . import data


_PRINT_LOCK = threading.Lock()


def _ts_print(*args, **kwargs) -> None:
    """Thread-safe print to avoid interleaved logs under concurrency."""
    with _PRINT_LOCK:
        print(*args, **kwargs)


@dataclass(frozen=True)
class _Task:
    idx: int
    total: int
    module_name: str
    src_code: str
    src_lang_name: str
    tgt_code: str
    tgt_lang_name: str
    model: str
    prompt_en: Optional[str]
    tgt_file: Any  # Path
    tgt_obj: Dict[str, Any]  # 含 @@*
    src_for_translate: Dict[str, str]  # 本次提交的 key->src_text（已过滤非空字符串）


@dataclass(frozen=True)
class _TaskResult:
    idx: int
    total: int
    module_name: str
    tgt_code: str
    tgt_lang_name: str
    tgt_file: Any  # Path
    tgt_obj: Dict[str, Any]  # 含 @@*
    batch_sec: float
    out: Dict[str, Any]
    src_for_translate: Dict[str, str]
    success_keys: int


def run_translate(cfg: data.I18nConfig, incremental: bool = True) -> None:
    """
    增量翻译（默认）：
      - source: <module>_<src_code>.i18n.json（例如 about_en.i18n.json）
      - target: <module>_<tgt_code>.i18n.json（例如 about_zh_Hant.i18n.json）
      - target 缺 key / None / 空字符串 => 翻译补齐；否则跳过（不打印）
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

    tasks, total_keys, _per_lang_total = _build_tasks(
        cfg=cfg,
        module_dirs=module_dirs,
        src_code=src_code,
        src_lang_name=src_lang_name,
        model=model,
        targets=targets,
        incremental=incremental,
    )

    total_batches = len(tasks)

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

    # 并发数：maxWorkers==0 自适应 2~8；>0 固定上限；都不超过任务数
    max_workers_cfg = _get_max_workers(cfg)
    max_workers = _compute_workers(max_workers_cfg, total_batches)
    if max_workers_cfg == 0:
        print(f"- 并发: {max_workers} workers（maxWorkers=0/自动）")
    else:
        print(f"- 并发: {max_workers} workers（maxWorkers={max_workers_cfg}）")


# ✅ 总耗时：从“翻译开始”到“全部结束”的墙钟时间
    start_all = time.perf_counter()

    # ✅ 累计每条任务耗时（用于对比并发节省）
    sum_batch_sec = 0.0

    # 控制每批打印多少条翻译内容（避免日志爆炸）
    MAX_PRINT_PER_BATCH = 200

    # 汇总统计
    done_keys = 0
    per_lang_done: Dict[str, int] = {t.code: 0 for t in targets}

    # 提交任务时打印 loading（保证顺序）
    for t in tasks:
        print(
            f"⏳ [{t.idx}/{t.total}] {t.module_name} → {t.tgt_code}  "
            f"{t.src_lang_name} → {t.tgt_lang_name}  | {len(t.src_for_translate)} key ..."
        )

    # 并发执行翻译（只做模型调用；写文件/打印由主线程统一处理）
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = [ex.submit(_translate_one, t) for t in tasks]

        for fut in as_completed(futures):
            r = fut.result()
            sum_batch_sec += r.batch_sec  # ✅ 汇总每条耗时（串行近似耗时）

            # 写回文件（主线程做，避免并发写日志混乱）
            merged = dict(r.tgt_obj)
            for k, v in r.out.items():
                if data.is_meta_key(k):
                    continue
                if isinstance(v, str) and v.strip():
                    merged[k] = v

            merged = data.sort_json_keys(merged)
            data.write_json(r.tgt_file, merged)

            done_keys += r.success_keys
            per_lang_done[r.tgt_code] = per_lang_done.get(r.tgt_code, 0) + r.success_keys

            elapsed_all = time.perf_counter() - start_all
            print(
                f"✅ [{r.idx}/{r.total}] {r.module_name} → {r.tgt_code}  "
                f"+{r.success_keys} key  | {r.batch_sec:.2f}s  | 累计 {elapsed_all:.2f}s"
            )

            _print_translated_pairs(
                src_lang_name=src_lang_name,
                tgt_lang_name=r.tgt_lang_name,
                src_dict=r.src_for_translate,
                out=r.out,
                max_print=MAX_PRINT_PER_BATCH,
            )

    total_elapsed = time.perf_counter() - start_all

    print("\n🎉 翻译完成汇总")
    print(f"- Source: {src_code} ({src_lang_name})")
    print(f"- 总批次: {total_batches}")
    print(f"- 总翻译 key: {done_keys}/{total_keys}")
    print(f"- 总耗时(墙钟): {total_elapsed:.2f}s")

    # ✅ 新增：每条耗时汇总（累计翻译耗时）
    print(f"- 累计翻译耗时(∑每条): {sum_batch_sec:.2f}s")

    # ✅ 新增：并发节省与加速比
    saved = sum_batch_sec - total_elapsed
    if saved > 0:
        print(f"- 并发节省: {saved:.2f}s")
    if total_elapsed > 0 and sum_batch_sec > 0:
        print(f"- 加速比: {sum_batch_sec / total_elapsed:.2f}x")

    if total_elapsed > 0:
        print(f"- 平均速度: {done_keys / total_elapsed:.2f} key/s")

    print("\n📌 目标语言汇总（仅展示有产出的）")
    for tgt in targets:
        code = tgt.code
        name = tgt.name_en
        cnt = per_lang_done.get(code, 0)
        if cnt > 0:
            print(f"- {code} ({name}): {cnt} key")


# -------------------------
# 并发 worker 规则
# -------------------------

def _get_max_workers(cfg: data.I18nConfig) -> int:
    # 兼容 maxWorkers / max_workers
    v = getattr(cfg, "maxWorkers", None)
    if v is None:
        v = getattr(cfg, "max_workers", None)
    try:
        return int(v) if v is not None else 0
    except Exception:
        return 0


def _compute_workers(max_workers_cfg: int, total_batches: int) -> int:
    if total_batches <= 0:
        return 1

    if max_workers_cfg and max_workers_cfg > 0:
        return max(1, min(max_workers_cfg, total_batches))

    # maxWorkers == 0：自适应 2~8
    cpu = os.cpu_count() or 4
    guess = max(2, min(8, max(2, cpu // 2)))
    return min(guess, total_batches)


# -------------------------
# 构建任务（严格按 data.py 的文件命名规则）
# -------------------------

def _build_tasks(
        cfg: data.I18nConfig,
        module_dirs: List[Any],
        src_code: str,
        src_lang_name: str,
        model: str,
        targets: List[Any],
        incremental: bool,
) -> Tuple[List[_Task], int, Dict[str, int]]:
    tasks: List[_Task] = []
    total_keys = 0
    per_lang_total: Dict[str, int] = {t.code: 0 for t in targets}

    staged: List[Tuple[str, Any, Dict[str, Any], Dict[str, str], str, str]] = []
    # (module_name, tgt_file, tgt_obj, src_for_translate, tgt_code, tgt_lang_name)

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
            tgt_lang_name = tgt.name_en
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

            if not src_for_translate:
                continue

            staged.append((md.name, tgt_file, tgt_obj, src_for_translate, tgt_code, tgt_lang_name))

    total_batches = len(staged)
    if total_batches == 0:
        return [], 0, per_lang_total

    for i, (module_name, tgt_file, tgt_obj, src_for_translate, tgt_code, tgt_lang_name) in enumerate(staged, start=1):
        n_keys = len(src_for_translate)
        total_keys += n_keys
        per_lang_total[tgt_code] = per_lang_total.get(tgt_code, 0) + n_keys

        prompt_en = _build_prompt_en(cfg, target_code=tgt_code)

        tasks.append(
            _Task(
                idx=i,
                total=total_batches,
                module_name=module_name,
                src_code=src_code,
                src_lang_name=src_lang_name,
                tgt_code=tgt_code,
                tgt_lang_name=tgt_lang_name,
                model=model,
                prompt_en=prompt_en,
                tgt_file=tgt_file,
                tgt_obj=tgt_obj,
                src_for_translate=src_for_translate,
            )
        )

    return tasks, total_keys, per_lang_total





def _make_progress_cb(t: _Task):
    """Build progress callback for translate_flat_dict (best-effort).

    Rules:
      - Only print chunking/chunk start/done when total chunks > 1 (avoid noise)
      - Still print minimal retry/split lines to avoid "silent hang"
      - Callback errors are swallowed and never affect translation
    """
    t_start = time.perf_counter()
    chunk_start: Dict[Any, float] = {}

    # Best-effort chunk size hint (core may not provide it)
    try:
        chunk_keys_hint = int(os.getenv("BOX_TRANSLATE_MAX_CHUNK", "").strip() or "0") or None
    except Exception:
        chunk_keys_hint = None

    _chunk_total: Optional[int] = None
    _printed_chunking_done = False

    def _fmt_idx(ci: Any, cn: Any) -> str:
        try:
            ci_i = int(ci)
            cn_i = int(cn)
            if cn_i > 0 and ci_i > 0 and ci_i <= cn_i:
                return f"{ci_i}/{cn_i}"
        except Exception:
            pass
        return ""

    def _set_total(payload: Dict[str, Any]) -> None:
        nonlocal _chunk_total
        if _chunk_total is not None:
            return
        for v in (payload.get("chunks"), payload.get("chunk_total"), payload.get("n")):
            try:
                if v is not None:
                    _chunk_total = int(v)
                    return
            except Exception:
                continue

    def _should_print_chunks() -> bool:
        return isinstance(_chunk_total, int) and _chunk_total > 1

    def cb(payload: Any) -> None:
        nonlocal _printed_chunking_done
        try:
            if not isinstance(payload, dict):
                return

            e = payload.get("event") or payload.get("type") or payload.get("name") or "event"
            ci = payload.get("chunk_index") or payload.get("i")
            cn = payload.get("chunk_total") or payload.get("n")
            ck = payload.get("chunk_keys") or payload.get("chunk_items") or payload.get("items") or payload.get("keys") or chunk_keys_hint
            attempt = payload.get("attempt")
            msg = payload.get("error") or payload.get("message") or ""

            _set_total(payload)

            now = time.perf_counter()
            since = now - t_start

            if e == "chunk_start":
                chunk_start[ci] = now
            if e == "chunk_done":
                st = chunk_start.pop(ci, None)
                sec = (now - st) if st else None
            else:
                sec = None

            with _PRINT_LOCK:
                head = f"   ⏱️ [{t.idx}/{t.total}] ({t.phase}) {t.tgt_code}"
                idx = _fmt_idx(ci, cn)

                if e == "chunking_done":
                    if _should_print_chunks() and not _printed_chunking_done:
                        size = ck or chunk_keys_hint
                        print(f"{head} 分片完成：{_chunk_total} 片（chunk_keys={size}） | {since:.2f}s")
                        _printed_chunking_done = True
                    return

                if e == "chunk_start":
                    if _should_print_chunks() and idx:
                        print(f"{head} chunk {idx} 开始（{ck} key） | {since:.2f}s")
                    return

                if e == "chunk_done":
                    if _should_print_chunks() and idx:
                        s = f"{sec:.2f}s" if isinstance(sec, (int, float)) else "?"
                        print(f"{head} chunk {idx} 完成（{ck} key） | {s} | {since:.2f}s")
                    return

                if e in {"chunk_error", "retry"}:
                    if idx and _should_print_chunks():
                        print(f"{head} chunk {idx} 异常/重试 attempt={attempt} {msg} | {since:.2f}s")
                    else:
                        print(f"{head} 异常/重试 attempt={attempt} {msg} | {since:.2f}s")
                    return

                if e == "chunk_split":
                    print(f"{head} chunk 拆分重试（减小批次） | {since:.2f}s")
                    return

                if os.environ.get("BOX_SLANG_I18N_PROGRESS_DEBUG", ""):
                    print(f"{head} {e}: {payload} | {since:.2f}s")
        except Exception:
            return

    return cb
def _translate_one(t: _Task) -> _TaskResult:
    t0 = time.perf_counter()
    cb = _make_progress_cb(t)
    out = translate_flat_dict(
        prompt_en=t.prompt_en,
        src_dict=t.src_for_translate,
        src_lang=t.src_lang_name,     # ✅ name_en
        tgt_locale=t.tgt_lang_name,   # ✅ name_en
        model=t.model,
        api_key=None,                 # ✅ 不关心 OPENAI_API_KEY
        progress_cb=cb,
    )
    t1 = time.perf_counter()

    success = 0
    for k, v in out.items():
        if data.is_meta_key(k):
            continue
        if isinstance(v, str) and v.strip():
            success += 1

    return _TaskResult(
        idx=t.idx,
        total=t.total,
        module_name=t.module_name,
        tgt_code=t.tgt_code,
        tgt_lang_name=t.tgt_lang_name,
        tgt_file=t.tgt_file,
        tgt_obj=t.tgt_obj,  # ✅ 不再依赖 tasks[idx-1]
        batch_sec=(t1 - t0),
        out=out,
        src_for_translate=t.src_for_translate,
        success_keys=success,
    )


# -------------------------
# util：KV/增量判断/prompt/打印
# -------------------------

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