from __future__ import annotations

from .data import Config, compute_missing, create_missing


def run_translate(cfg: Config, incremental: bool, auto_create_targets: bool, yes: bool) -> int:
    """
    你要求：
    - translate 时如果没有目标文件夹和文件，自动创建
    这里实现为：
    - auto_create_targets=True：无论 yes 与否，先创建缺失目录/文件（更符合“自动创建”）
    - 若你希望仍受 --yes 控制，把条件改为：if yes: create_missing(...)
    """
    if auto_create_targets:
        missing_dirs, missing_files = compute_missing(cfg)
        if missing_dirs or missing_files:
            create_missing(cfg, missing_dirs, missing_files)
            print("[translate] 已自动创建缺失目录/文件。")

    # TODO:
    # 1) 扫描资源文件（按 layout）
    # 2) 读取 base/source
    # 3) 计算每个 target 需要翻译 keys（incremental 控制）
    # 4) 调 OpenAI（并发 + 批处理）
    # 5) 主线程写回
    # 6) post sort（cfg.options.post_sort_after_translate）
    print(f"[translate] (skeleton) model={cfg.openai_model} incremental={incremental}")
    return 0
