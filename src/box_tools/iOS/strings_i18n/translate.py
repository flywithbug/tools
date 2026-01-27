from __future__ import annotations

import concurrent.futures as cf
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Tuple, Optional

from . import data


@dataclass(frozen=True)
class TranslateTask:
    locale: data.Locale
    base_file: Path
    target_file: Path


def run_translate(cfg: data.StringsI18nConfig, incremental: bool = True) -> None:
    """翻译（骨架 -> 可运行的增量写回框架）

    目标：
    - 以 Base.lproj/*.strings 为金标准
    - 对 core_locales + target_locales 进行增量补齐（默认）
    - 并发执行“翻译任务”，主线程写回文件
    - 写回后复用 sort（保证格式/注释/分组规则一致）

    说明：
    - 当前版本包含完整的数据流与写回逻辑
    - 真正的 LLM/翻译引擎接入留在 _translate_text()（后续替换即可）
    """
    mode = "增量" if incremental else "全量"
    print("🌍 translate") 
    print(f"- 模式: {mode}")
    print(f"- Source: {cfg.source_locale.code} ({cfg.source_locale.name_en})")
    print(f"- Core: {[x.code for x in cfg.core_locales]}")
    print(f"- Targets: {len(cfg.target_locales)}")

    # 1) Base 读取
    base_dir = (cfg.lang_root / cfg.base_folder).resolve()
    if not base_dir.exists():
        raise data.ConfigError(f"未找到 base_folder: {base_dir}")
    base_files = sorted(base_dir.glob("*.strings"))
    if not base_files:
        print(f"⚠️ Base.lproj 下未找到任何 .strings：{base_dir}")
        return

    # 2) 目标语言（core + target；去重保序；不包含 source/base）
    all_targets = data._dedup_locales_preserve_order(cfg.core_locales + cfg.target_locales)
    all_targets = [x for x in all_targets if x.code not in {cfg.source_locale.code, cfg.base_locale.code}]
    if not all_targets:
        print("⚠️ 没有需要翻译的目标语言（core/target 为空或与 source/base 重合）")
        return

    # 3) 生成任务
    tasks: List[TranslateTask] = []
    for loc in all_targets:
        lproj = (cfg.lang_root / f"{loc.code}.lproj").resolve()
        lproj.mkdir(parents=True, exist_ok=True)
        for bf in base_files:
            tf = lproj / bf.name
            if not tf.exists():
                tf.write_text("", encoding="utf-8")
            tasks.append(TranslateTask(locale=loc, base_file=bf, target_file=tf))

    # 4) 并发执行：每个 task 计算应写回的 entries（不直接写文件）
    #    注意：主线程写回，避免并发写文件导致损坏
    max_workers = int(cfg.options.get("max_workers", 8)) if isinstance(cfg.options, dict) else 8
    changed: List[Tuple[TranslateTask, int]] = []
    skipped: int = 0

    with cf.ThreadPoolExecutor(max_workers=max_workers) as ex:
        fut_map = {ex.submit(_translate_one_file, cfg, t, incremental): t for t in tasks}
        for fut in cf.as_completed(fut_map):
            t = fut_map[fut]
            try:
                delta = fut.result()
                if delta > 0:
                    changed.append((t, delta))
                else:
                    skipped += 1
            except Exception as e:
                print(f"❌ translate 失败: {t.locale.code} / {t.base_file.name}: {e}")

    # 5) 汇总与写回后的排序（复用现有 sort）
    total_added = sum(n for _, n in changed)
    print(f"✅ translate 任务完成：修改 {len(changed)} 个文件，新增/更新 {total_added} 个 key；未改动 {skipped} 个文件")
    print("🔧 translate 后执行 sort（保证格式一致）...")
    data.run_sort(cfg)


def _translate_one_file(cfg: data.StringsI18nConfig, task: TranslateTask, incremental: bool) -> int:
    """生成并写回某个 (locale, file) 的翻译结果。返回新增/更新 key 数。"""
    base_preamble, base_entries = data.parse_strings_file(task.base_file)
    # base key->value
    base_map: Dict[str, str] = {e.key: e.value for e in base_entries if not e.key.startswith("@@")}

    # 目标文件当前内容
    tgt_preamble, tgt_entries = data.parse_strings_file(task.target_file)
    tgt_map: Dict[str, data.StringsEntry] = {e.key: e for e in tgt_entries}

    updates: List[data.StringsEntry] = []
    changed = 0

    for key, base_val in base_map.items():
        if base_val is None or base_val == "":
            continue

        existing = tgt_map.get(key)
        need = True
        if incremental:
            if existing and (existing.value is not None) and (existing.value.strip() != ""):
                need = False

        if not need:
            continue

        new_val = _translate_text(
            src_text=base_val,
            target_locale=task.locale,
            source_locale=cfg.source_locale,
            cfg=cfg,
            key=key,
        )

        # 保留既有注释（如有）；没有则不加
        comments = existing.comments if existing else []
        new_entry = data.StringsEntry(key=key, value=new_val, comments=comments)

        tgt_map[key] = new_entry
        changed += 1

    if changed == 0:
        return 0

    # 写回：保持 target 的 preamble，不强行替换成 base 的
    new_entries = list(tgt_map.values())

    # 其他语言只需按 key 排序（不分组），但这里写回后会跑 sort；
    # 为了减少 diff，这里先做一个简单排序。
    new_entries_sorted = sorted(new_entries, key=lambda e: e.key)

    data.write_strings_file(task.target_file, tgt_preamble, new_entries_sorted, group_by_prefix=False)
    return changed


def _translate_text(*, src_text: str, target_locale: data.Locale, source_locale: data.Locale, cfg: data.StringsI18nConfig, key: str) -> str:
    """翻译引擎占位实现（后续替换为真实 LLM/翻译服务）。

    现在的策略：输出一个明显可检索的占位结果，避免误把 Base 文案当成翻译。
    """
    # 例：[[ja]] Hello -> 便于全局搜索/清理
    return f"[[{target_locale.code}]] {src_text}"
