from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import List, Tuple

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


def run_translate(cfg: Config, incremental: bool, auto_create_targets: bool) -> int:
    if auto_create_targets:
        missing_dirs, missing_files = compute_missing(cfg)
        if missing_dirs or missing_files:
            create_missing(cfg, missing_dirs, missing_files)
            print("[translate] 已自动创建缺失目录/文件。")

    # OpenAI key：优先用环境变量；也允许在 cfg.options 里放 apiKey
    api_key = os.getenv("OPENAI_API_KEY") or str((cfg.options or {}).get("openAIKey") or "")

    # prompt：可选。若你的 yaml 里有 prompts.prompt_en，就会传进去
    prompt_en = None
    try:
        prompt_en = (cfg.prompts or {}).get("prompt_en") or (cfg.prompts or {}).get("promptEn")
    except Exception:
        prompt_en = None

    # options：把 cfg.max_workers 也映射到并发池；OpenAI translate 内部也有 chunk 相关配置
    opt = _Options()
    # 如果你的 yaml 里有 options.max_chunk_items / maxChunkItems，可覆盖默认
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

    # incremental=True => translate_from_to 自带 diff 逻辑（缺失/为空/等于源文本才翻译）
    # incremental=False => 我们仍用 translate_from_to，但先清空目标文件（只保留 meta），等价全量翻译
    def _job(source_fp: Path, target_fp: Path, tgt_locale: str) -> Tuple[str, str, int]:
        if not source_fp.exists():
            return (tgt_locale, str(target_fp), 1)

        # 全量翻译：把 target 先重置成仅 meta（若有），让 diff 全部命中
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
                # 忽略重置失败，继续走增量逻辑
                pass

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
            )
            return (tgt_locale, str(target_fp), 0)
        except JsonTranslateError as e:
            print(f"[translate] JSON 翻译失败：{target_fp} ({e})")
            return (tgt_locale, str(target_fp), 2)
        except Exception as e:
            print(f"[translate] 翻译失败：{target_fp} ({type(e).__name__}: {e})")
            return (tgt_locale, str(target_fp), 2)

    max_workers = int(cfg.max_workers) if int(cfg.max_workers) > 0 else min(8, max(1, len(pairs)))
    failed = 0

    print(f"[translate] 开始翻译：pairs={len(pairs)} incremental={incremental} workers={max_workers} model={cfg.openai_model}")

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futs = [ex.submit(_job, s, t, loc) for (s, t, loc) in pairs]
        for fut in as_completed(futs):
            tgt_locale, target_fp, rc = fut.result()
            if rc != 0:
                failed += 1

    if failed:
        print(f"[translate] 完成但有失败：failed={failed}/{len(pairs)}")
        return 2

    print("[translate] 完成：全部成功。")
    return 0
