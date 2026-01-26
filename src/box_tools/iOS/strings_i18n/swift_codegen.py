from __future__ import annotations
from pathlib import Path
from . import data


def run_gen_swift(cfg: data.StringsI18nConfig) -> None:
    if not cfg.swift_codegen.enabled:
        print("⚠️ swift_codegen.enabled = false，跳过")
        return

    out = cfg.swift_codegen.output_file
    if not out.strip():
        print("❌ swift_codegen.output_file 为空")
        return

    out_path = (cfg.project_root / Path(out)).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # 框架版先写一个最小文件头，后续再按 PRD 生成完整 enum + 注释映射
    text = (
        f"// Auto-generated from {cfg.base_folder}/{cfg.swift_codegen.input_file}\n"
        "import Foundation\n"
    )
    out_path.write_text(text, encoding="utf-8")
    print(f"✅ gen-swift 完成（框架版）：{out_path}")
