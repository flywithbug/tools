from __future__ import annotations

from .data import Config, compute_missing, create_missing


def run_translate(cfg: Config, incremental: bool, auto_create_targets: bool) -> int:
    if auto_create_targets:
        missing_dirs, missing_files = compute_missing(cfg)
        if missing_dirs or missing_files:
            create_missing(cfg, missing_dirs, missing_files)
            print("[translate] 已自动创建缺失目录/文件。")

    # TODO: OpenAI 翻译实现（并发/增量/主线程写回/post-sort）
    print(f"[translate] (skeleton) model={cfg.openai_model} incremental={incremental}")
    return 0
