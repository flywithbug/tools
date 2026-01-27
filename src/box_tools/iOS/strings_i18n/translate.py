from __future__ import annotations

from . import data


def run_translate(cfg: data.StringsI18nConfig, incremental: bool = True) -> None:
    """
    翻译骨架：
    - 未来应支持：
      - 增量/全量
      - core_locales 与 target_locales 分批/分组
      - 并发批处理
      - 忽略 meta 字段/注释行
      - 主线程写回与排序

    当前版本仅保留命令入口与参数（尚未实现 .strings 解析与写回）。
    """
    mode = "增量" if incremental else "全量"
    print("🌍 translate（骨架）")
    print(f"- 模式: {mode}")
    print(f"- Source: {cfg.source_locale.code} ({cfg.source_locale.name_en})")
    print(f"- Core: {[x.code for x in cfg.core_locales]}")
    print(f"- Targets: {[x.code for x in cfg.target_locales]}")
    print(f"- lang_root: {cfg.lang_root}")
    print("⚠️ translate：骨架版本尚未实现（TODO：解析 Base.lproj 与各语言 .lproj 下的 .strings 文件并写回）")
