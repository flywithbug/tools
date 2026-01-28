from __future__ import annotations

import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import List, Tuple, Dict, Any

from .data import Config, compute_missing, create_missing, detect_mode, list_modules
from box_tools._share.openai_translate.json_translate import translate_from_to, JsonTranslateError
from box_tools._share.openai_translate.translate import _Options


def _expected_pair_paths(cfg: Config) -> List[Tuple[Path, Path, str]]:
    """Return (source_fp, target_fp, tgt_locale) pairs for all targets."""
    mode = detect_mode(cfg)
    suffix = cfg.file_suffix or ""
    pairs: List[Tuple[Path, Path, str]] = []

    if mode == "root":
        src_name = cfg.layout.root.pattern.format(code=cfg.source.code, suffix=suffix)
        source_fp = cfg.i18n_dir / src_name
        for t in cfg.targets:
            tgt_name = cfg.layout.root.pattern.format(code=t.code, suffix=suffix)
            pairs.append((source_fp, cfg.i18n_dir / tgt_name, t.code))
    else:
        for folder in list_modules(cfg):
            src_name = cfg.layout.module.pattern.format(folder=folder, code=cfg.source.code, suffix=suffix)
            source_fp = cfg.i18n_dir / folder / src_name
            for t in cfg.targets:
                tgt_name = cfg.layout.module.pattern.format(folder=folder, code=t.code, suffix=suffix)
                pairs.append((source_fp, cfg.i18n_dir / folder / tgt_name, t.code))

    return pairs


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


def run_translate(cfg: Config, incremental: bool, auto_create_targets: bool) -> int:
    if auto_create_targets:
        missing_dirs, missing_files = compute_missing(cfg)
        if missing_dirs or missing_files:
            create_missing(cfg, missing_dirs, missing_files)
            print("[translate] 已自动创建缺失目录/文件。")

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

    pairs = _expected_pair_paths(cfg)
    if not pairs:
        print("[translate] 未发现任何目标语言文件对（targets 为空？）")
        return 0

    max_workers = int(cfg.max_workers) if int(cfg.max_workers) > 0 else min(8, max(1, len(pairs)))
    failed = 0

    t0 = time.perf_counter()
    print(f"[translate] 开始翻译：pairs={len(pairs)} incremental={incremental} workers={max_workers} model={cfg.openai_model}")

    done_files = 0

    def _job(source_fp: Path, target_fp: Path, tgt_locale: str) -> Tuple[str, str, int]:
        nonlocal done_files

        if not source_fp.exists():
            print(f"[translate] 跳过：缺少源文件：{source_fp}")
            done_files += 1
            return (tgt_locale, str(target_fp), 1)

        if not incremental:
            try:
                import json
                if target_fp.exists():
                    obj = json.loads(target_fp.read_text(encoding="utf-8") or "{}")
                    if isinstance(obj, dict):
                        meta = {k: obj[k] for k in ("@@dirty", "@@locale") if k in obj}
                    else:
                        meta = {}
                else:
                    meta = {}
                if "@@locale" not in meta:
                    meta["@@locale"] = tgt_locale
                if "@@dirty" not in meta:
                    meta["@@dirty"] = False
                target_fp.parent.mkdir(parents=True, exist_ok=True)
                target_fp.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            except Exception:
                pass

        file_t0 = time.perf_counter()
        last_print = {"t": 0.0}

        def progress_cb(payload: Dict[str, Any]) -> None:
            ev = payload.get("event")
            now = time.perf_counter()

            if ev == "diff":
                src_keys = payload.get("src_keys")
                todo_keys = payload.get("todo_keys")
                target_keys = payload.get("target_keys")
                print(f"[translate] {tgt_locale}: diff src={src_keys} target={target_keys} todo={todo_keys} -> {target_fp.name}")
                return

            if now - last_print["t"] < 0.2 and ev not in ("all_done", "noop"):
                return
            last_print["t"] = now

            if ev == "chunk_done":
                done = payload.get("done", 0)
                total = payload.get("total", 0)
                chunk_idx = payload.get("chunk_index")
                chunk_items = payload.get("chunk_items")
                print(f"[translate] {tgt_locale}: chunk {chunk_idx} +{chunk_items} ({done}/{total})")
                return

            if ev == "noop":
                print(f"[translate] {tgt_locale}: 无需翻译（增量命中 0）")
                return

            if ev == "all_done":
                total_written = payload.get("total_written", 0)
                print(f"[translate] {tgt_locale}: 完成，写入 {total_written} 个 key")
                return

        try:
            translate_from_to(
                sourceFilePath=str(source_fp),
                targetFilePath=str(target_fp),
                src_locale=cfg.source.code,
                tgt_locale=tgt_locale,
                model=cfg.openai_model,
                api_key=api_key or None,
                prompt_en=prompt_en,
                opt=opt,
                progress_cb=progress_cb,
            )
            dt = time.perf_counter() - file_t0
            done_files += 1
            elapsed = time.perf_counter() - t0
            print(f"[translate] {tgt_locale}: ✅ 用时 {_fmt_secs(dt)} | 总进度 {done_files}/{len(pairs)} | 总耗时 {_fmt_secs(elapsed)}")
            return (tgt_locale, str(target_fp), 0)

        except JsonTranslateError as e:
            dt = time.perf_counter() - file_t0
            done_files += 1
            print(f"[translate] {tgt_locale}: ❌ JSON 翻译失败（{_fmt_secs(dt)}）：{target_fp} ({e})")
            return (tgt_locale, str(target_fp), 2)

        except Exception as e:
            dt = time.perf_counter() - file_t0
            done_files += 1
            print(f"[translate] {tgt_locale}: ❌ 翻译失败（{_fmt_secs(dt)}）：{target_fp} ({type(e).__name__}: {e})")
            return (tgt_locale, str(target_fp), 2)

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futs = [ex.submit(_job, s, t, loc) for (s, t, loc) in pairs]
        for fut in as_completed(futs):
            tgt_locale, target_fp, rc = fut.result()
            if rc != 0:
                failed += 1

    total_dt = time.perf_counter() - t0
    ok = len(pairs) - failed
    rate = (ok / total_dt) if total_dt > 0 else 0.0

    if failed:
        print(f"[translate] 结束：✅{ok} ❌{failed} | 总耗时 {_fmt_secs(total_dt)} | 速率 {rate:.2f} 文件/秒")
        return 2

    print(f"[translate] 结束：全部成功 ✅{ok} | 总耗时 {_fmt_secs(total_dt)} | 速率 {rate:.2f} 文件/秒")
    return 0
