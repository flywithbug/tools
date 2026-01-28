from __future__ import annotations

import os
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import List, Tuple, Dict, Any, Optional

from .data import Config, compute_missing, create_missing, detect_mode, list_modules
from box_tools._share.openai_translate.json_translate import translate_from_to, JsonTranslateError
from box_tools._share.openai_translate.translate import _Options


_PRINT_LOCK = threading.Lock()


def _ts_print(*args: object) -> None:
    # Avoid interleaved logs in multi-threading
    with _PRINT_LOCK:
        print(*args, flush=True)


@dataclass(frozen=True)
class _Task:
    idx: int
    total: int
    module_name: str
    src_code: str
    tgt_code: str
    source_fp: Path
    target_fp: Path


def _expected_tasks(cfg: Config) -> List[_Task]:
    """Build translation tasks per target file (root or module mode)."""
    mode = detect_mode(cfg)
    suffix = cfg.file_suffix or ""
    tasks: List[_Task] = []

    if mode == "root":
        src_name = cfg.layout.root.pattern.format(code=cfg.source.code, suffix=suffix)
        source_fp = cfg.i18n_dir / src_name
        # stable ordering by target code
        targets = sorted(cfg.targets, key=lambda x: x.code)
        for t in targets:
            tgt_name = cfg.layout.root.pattern.format(code=t.code, suffix=suffix)
            tasks.append(_Task(
                idx=0, total=0,
                module_name="root",
                src_code=cfg.source.code,
                tgt_code=t.code,
                source_fp=source_fp,
                target_fp=cfg.i18n_dir / tgt_name,
            ))
    else:
        folders = list_modules(cfg)
        targets = sorted(cfg.targets, key=lambda x: x.code)
        for folder in folders:
            src_name = cfg.layout.module.pattern.format(folder=folder, code=cfg.source.code, suffix=suffix)
            source_fp = cfg.i18n_dir / folder / src_name
            for t in targets:
                tgt_name = cfg.layout.module.pattern.format(folder=folder, code=t.code, suffix=suffix)
                tasks.append(_Task(
                    idx=0, total=0,
                    module_name=folder,
                    src_code=cfg.source.code,
                    tgt_code=t.code,
                    source_fp=source_fp,
                    target_fp=cfg.i18n_dir / folder / tgt_name,
                ))

    # assign idx/total
    total = len(tasks)
    return [ _Task(idx=i+1, total=total, module_name=x.module_name, src_code=x.src_code, tgt_code=x.tgt_code, source_fp=x.source_fp, target_fp=x.target_fp)
            for i, x in enumerate(tasks) ]


def _fmt_secs(s: float) -> str:
    if s < 60:
        return f"{s:.1f}s"
    if s < 3600:
        m = int(s // 60)
        r = s - m * 60
        return f"{m}m{r:.0f}s"
    h = int(s // 3600)
    m = int((s - h * 3600) // 60)
    return f"{h}h{m}m"


def _make_progress_cb(t: _Task):
    """Slang-style robust progress callback (compatible with json_translate + translate_flat_dict)."""
    task_start = time.perf_counter()
    ctx: Dict[str, Any] = {
        "chunk_total": None,   # int
        "chunk_keys": None,    # int
        "chunk_starts": {},    # raw_idx(int) -> perf_counter(float)
        "printed_diff": False,
    }

    def _as_int(x: Any) -> Optional[int]:
        try:
            if x is None:
                return None
            if isinstance(x, bool):
                return None
            return int(x)
        except Exception:
            return None

    def _pick_total(ev: Dict[str, Any]) -> Optional[int]:
        return (
            _as_int(ev.get("chunks_total"))
            or _as_int(ev.get("total_chunks"))
            or _as_int(ev.get("chunk_total"))
            or _as_int(ev.get("chunks"))
            or _as_int(ev.get("n"))
            or _as_int(ctx.get("chunk_total"))
        )

    def _pick_chunk_keys(ev: Dict[str, Any]) -> Optional[int]:
        return (
            _as_int(ev.get("chunk_keys"))
            or _as_int(ev.get("chunk_size"))
            or _as_int(ev.get("max_chunk_items"))
            or _as_int(ev.get("max_keys"))
            or _as_int(ev.get("items_per_chunk"))
            or _as_int(ctx.get("chunk_keys"))
        )

    def _pick_idx(ev: Dict[str, Any]) -> Optional[int]:
        return (
            _as_int(ev.get("chunk_index"))
            or _as_int(ev.get("chunk_i"))
            or _as_int(ev.get("index"))
            or _as_int(ev.get("idx"))
            or _as_int(ev.get("i"))
        )

    def _pick_nkeys(ev: Dict[str, Any]) -> Optional[int]:
        return (
            _as_int(ev.get("n_keys"))
            or _as_int(ev.get("keys"))
            or _as_int(ev.get("items"))
            or _as_int(ev.get("chunk_len"))
            or _as_int(ev.get("size"))
            or _pick_chunk_keys(ev)
        )

    def _normalize_display_idx(raw_i: Optional[int], total: Optional[int]) -> Optional[int]:
        # normalize to 1-based for display
        if raw_i is None:
            return None
        if total is not None and 0 <= raw_i < total:
            return raw_i + 1
        if total is None and raw_i == 0:
            return 1
        return raw_i

    def cb(ev: Dict[str, Any]) -> None:
        try:
            et = ev.get("event") or ev.get("type") or ev.get("name")
            if not et:
                return
            et = str(et)

            now = time.perf_counter()
            elapsed = now - task_start

            # json_translate diff event (most informative)
            if et == "diff":
                src_keys = ev.get("src_keys")
                target_keys = ev.get("target_keys")
                todo_keys = ev.get("todo_keys")
                ctx["printed_diff"] = True
                _ts_print(
                    f"🔎 [{t.idx}/{t.total}] {t.module_name}->{t.tgt_code} "
                    f"diff src={src_keys} target={target_keys} todo={todo_keys} | {elapsed:.2f}s"
                )
                return

            # translate_flat_dict chunking summary
            if et in ("chunking_done", "chunked", "chunking"):
                total = _pick_total(ev)
                ck = _pick_chunk_keys(ev)
                if total is not None:
                    ctx["chunk_total"] = total
                if ck is not None:
                    ctx["chunk_keys"] = ck

                # total<=1 时不刷屏
                if (ctx["chunk_total"] or 0) > 1:
                    _ts_print(
                        f"   ⏱️ [{t.idx}/{t.total}] {t.module_name}->{t.tgt_code} "
                        f"分片完成：{ctx['chunk_total']} 片（chunk_keys={ctx['chunk_keys'] or '?' }） | {elapsed:.2f}s"
                    )
                return

            # suppress noisy logs for single-chunk jobs
            if (ctx.get("chunk_total") or 0) <= 1 and et in ("chunk_start", "chunk_done"):
                return

            if et in ("chunk_start", "chunk_begin", "chunk_started"):
                raw_i = _pick_idx(ev)
                total = _pick_total(ev)
                nkeys = _pick_nkeys(ev)

                if raw_i is not None:
                    ctx["chunk_starts"][raw_i] = now

                i_show = _normalize_display_idx(raw_i, total or ctx.get("chunk_total"))
                n_show = total or ctx.get("chunk_total")

                _ts_print(
                    f"   ⏱️ [{t.idx}/{t.total}] {t.module_name}->{t.tgt_code} "
                    f"chunk {i_show or '?'} / {n_show or '?'} 开始（{nkeys or '?'} key） | {elapsed:.2f}s"
                )
                return

            if et in ("chunk_done", "chunk_end", "chunk_finished"):
                raw_i = _pick_idx(ev)
                total = _pick_total(ev)
                nkeys = _pick_nkeys(ev)

                started = ctx["chunk_starts"].get(raw_i) if raw_i is not None else None
                chunk_sec = (now - started) if started is not None else None

                i_show = _normalize_display_idx(raw_i, total or ctx.get("chunk_total"))
                n_show = total or ctx.get("chunk_total")
                cs = f"{chunk_sec:.2f}s" if chunk_sec is not None else "?"

                _ts_print(
                    f"   ⏱️ [{t.idx}/{t.total}] {t.module_name}->{t.tgt_code} "
                    f"chunk {i_show or '?'} / {n_show or '?'} 完成（{nkeys or '?'} key） | {cs} | {elapsed:.2f}s"
                )
                return

            if et in ("chunk_error", "chunk_retry"):
                attempt = ev.get("attempt")
                err = ev.get("error") or ev.get("message") or ""
                raw_i = _pick_idx(ev)
                total = _pick_total(ev) or ctx.get("chunk_total")
                i_show = _normalize_display_idx(raw_i, total)
                if attempt is None:
                    _ts_print(
                        f"   ⏱️ [{t.idx}/{t.total}] {t.module_name}->{t.tgt_code} "
                        f"chunk {i_show or '?'} / {total or '?'} 异常/重试 {err} | {elapsed:.2f}s"
                    )
                else:
                    _ts_print(
                        f"   ⏱️ [{t.idx}/{t.total}] {t.module_name}->{t.tgt_code} "
                        f"chunk {i_show or '?'} / {total or '?'} 异常/重试 attempt={attempt} {err} | {elapsed:.2f}s"
                    )
                return

            if et in ("chunk_split", "chunk_split_retry"):
                _ts_print(
                    f"   ⏱️ [{t.idx}/{t.total}] {t.module_name}->{t.tgt_code} "
                    f"chunk 拆分重试（减小批次） | {elapsed:.2f}s"
                )
                return

            if et == "noop":
                # json_translate.noop
                _ts_print(
                    f"✅ [{t.idx}/{t.total}] {t.module_name}->{t.tgt_code} 无需翻译（增量命中 0） | {elapsed:.2f}s"
                )
                return

            if et == "all_done":
                # json_translate all_done includes total_written
                if "total_written" in ev:
                    _ts_print(
                        f"✅ [{t.idx}/{t.total}] {t.module_name}->{t.tgt_code} 完成（写入 {ev.get('total_written', 0)} key） | {elapsed:.2f}s"
                    )
                return

        except Exception:
            return

    return cb


def run_translate(cfg: Config, incremental: bool, auto_create_targets: bool) -> int:
    if auto_create_targets:
        missing_dirs, missing_files = compute_missing(cfg)
        if missing_dirs or missing_files:
            create_missing(cfg, missing_dirs, missing_files)
            _ts_print("[translate] 已自动创建缺失目录/文件。")

    api_key = os.getenv("OPENAI_API_KEY") or str((cfg.options or {}).get("openAIKey") or "")

    prompt_en = None
    try:
        prompt_en = (cfg.prompts or {}).get("prompt_en") or (cfg.prompts or {}).get("promptEn")
    except Exception:
        prompt_en = None

    opt = _Options()
    try:
        max_chunk_items = (cfg.options or {}).get("max_chunk_items") or (cfg.options or {}).get("maxChunkItems")
        if max_chunk_items:
            opt.max_chunk_items = int(max_chunk_items)
    except Exception:
        pass

    tasks = _expected_tasks(cfg)
    if not tasks:
        _ts_print("[translate] 未发现任何目标语言文件对（targets 为空？）")
        return 0

    max_workers = int(cfg.max_workers) if int(cfg.max_workers) > 0 else min(8, max(1, len(tasks)))
    failed = 0

    t0 = time.perf_counter()
    _ts_print(f"[translate] 开始翻译：tasks={len(tasks)} incremental={incremental} workers={max_workers} model={cfg.openai_model}")

    def _job(t: _Task) -> Tuple[str, str, int]:
        if not t.source_fp.exists():
            _ts_print(f"[translate] 跳过：缺少源文件：{t.source_fp}")
            return (t.tgt_code, str(t.target_fp), 1)

        # Full mode: reset target to only meta so diff hits everything
        if not incremental:
            try:
                import json
                if t.target_fp.exists():
                    obj = json.loads(t.target_fp.read_text(encoding="utf-8") or "{}")
                    if isinstance(obj, dict):
                        meta = {k: obj[k] for k in ("@@dirty", "@@locale") if k in obj}
                    else:
                        meta = {}
                else:
                    meta = {}
                if "@@locale" not in meta:
                    meta["@@locale"] = t.tgt_code
                if "@@dirty" not in meta:
                    meta["@@dirty"] = False
                t.target_fp.parent.mkdir(parents=True, exist_ok=True)
                t.target_fp.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            except Exception:
                pass

        file_t0 = time.perf_counter()
        cb = _make_progress_cb(t)

        try:
            translate_from_to(
                sourceFilePath=str(t.source_fp),
                targetFilePath=str(t.target_fp),
                src_locale=t.src_code,
                tgt_locale=t.tgt_code,
                model=cfg.openai_model,
                api_key=api_key or None,
                prompt_en=prompt_en,
                opt=opt,
                progress_cb=cb,
            )
            dt = time.perf_counter() - file_t0
            total_elapsed = time.perf_counter() - t0
            _ts_print(
                f"🏁 [{t.idx}/{t.total}] {t.module_name}->{t.tgt_code} "
                f"文件完成 | 用时 {_fmt_secs(dt)} | 总耗时 {_fmt_secs(total_elapsed)}"
            )
            return (t.tgt_code, str(t.target_fp), 0)

        except JsonTranslateError as e:
            dt = time.perf_counter() - file_t0
            _ts_print(f"❌ [{t.idx}/{t.total}] {t.module_name}->{t.tgt_code} JSON 翻译失败（{_fmt_secs(dt)}）：{t.target_fp} ({e})")
            return (t.tgt_code, str(t.target_fp), 2)

        except Exception as e:
            dt = time.perf_counter() - file_t0
            _ts_print(f"❌ [{t.idx}/{t.total}] {t.module_name}->{t.tgt_code} 翻译失败（{_fmt_secs(dt)}）：{t.target_fp} ({type(e).__name__}: {e})")
            return (t.tgt_code, str(t.target_fp), 2)

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futs = [ex.submit(_job, t) for t in tasks]
        for fut in as_completed(futs):
            _, _, rc = fut.result()
            if rc != 0:
                failed += 1

    total_dt = time.perf_counter() - t0
    ok = len(tasks) - failed
    rate = (ok / total_dt) if total_dt > 0 else 0.0

    if failed:
        _ts_print(f"[translate] 结束：✅{ok} ❌{failed} | 总耗时 {_fmt_secs(total_dt)} | 速率 {rate:.2f} 文件/秒")
        return 2

    _ts_print(f"[translate] 结束：全部成功 ✅{ok} | 总耗时 {_fmt_secs(total_dt)} | 速率 {rate:.2f} 文件/秒")
    return 0
