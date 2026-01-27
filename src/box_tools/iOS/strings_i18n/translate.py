from __future__ import annotations

import concurrent.futures as cf
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Tuple, Optional

from . import data


@dataclass(frozen=True)
class TranslateTask:
    locale: data.Locale
    src_file: Path
    target_file: Path
    # 用于日志
    phase: str  # "base->core" | "core->target"


def run_translate(cfg: data.StringsI18nConfig, incremental: bool = True) -> None:
    """翻译模块（增量）

    需求：
    1) 增量翻译：Base.lproj -> core_locales
    2) 增量翻译：core(source_locale) -> target_locales

    约定：
    - Base.lproj/*.strings 为 key 的金标准
    - core->target 的源语言使用 cfg.source_locale（它必须属于 core_locales 的范围内）
      * 若 source_locale 某个 key 缺失/空值，则回退到 Base 对应 value
    - 写回由主线程完成（避免并发写文件损坏）
    """
    mode = "增量" if incremental else "全量"
    print("🌍 translate")
    print(f"- 模式: {mode}")
    print(f"- Base: {cfg.base_locale.code} (Base.lproj)")
    print(f"- Source(core pivot): {cfg.source_locale.code} ({cfg.source_locale.name_en})")
    print(f"- Core: {[x.code for x in cfg.core_locales]}")
    print(f"- Targets: {len(cfg.target_locales)}")

    # Base 读取
    base_dir = (cfg.lang_root / cfg.base_folder).resolve()
    if not base_dir.exists():
        raise data.ConfigError(f"未找到 base_folder: {base_dir}")
    base_files = sorted(base_dir.glob("*.strings"))
    if not base_files:
        print(f"⚠️ Base.lproj 下未找到任何 .strings：{base_dir}")
        return

    # 0) 确保目录/文件完整性（复用 sort 的完整性逻辑）
    #    translate 前先补齐文件，避免后面反复判断
    try:
        data.ensure_file_integrity(cfg)
    except Exception:
        # 旧版本可能没有这个函数：保持兼容
        pass

    # 1) phase A：Base -> Core（排除 base 自身）
    core_targets = [loc for loc in cfg.core_locales if loc.code != cfg.base_locale.code]
    tasks_a: List[TranslateTask] = []
    for loc in core_targets:
        lproj = (cfg.lang_root / f"{loc.code}.lproj").resolve()
        lproj.mkdir(parents=True, exist_ok=True)
        for bf in base_files:
            tf = lproj / bf.name
            if not tf.exists():
                tf.write_text("", encoding="utf-8")
            tasks_a.append(TranslateTask(locale=loc, src_file=bf, target_file=tf, phase="base->core"))

    # 2) phase B：Core(source_locale pivot) -> Target
    pivot_dir = (cfg.lang_root / f"{cfg.source_locale.code}.lproj").resolve()
    pivot_dir.mkdir(parents=True, exist_ok=True)
    tasks_b: List[TranslateTask] = []
    for loc in cfg.target_locales:
        # 目标语言本身若等于 pivot/source，就不需要翻译
        if loc.code == cfg.source_locale.code:
            continue
        lproj = (cfg.lang_root / f"{loc.code}.lproj").resolve()
        lproj.mkdir(parents=True, exist_ok=True)
        for bf in base_files:
            srcf = pivot_dir / bf.name
            # pivot 文件可能不存在：先创建空文件；缺 key 会回退到 Base value
            if not srcf.exists():
                srcf.write_text("", encoding="utf-8")
            tf = lproj / bf.name
            if not tf.exists():
                tf.write_text("", encoding="utf-8")
            tasks_b.append(TranslateTask(locale=loc, src_file=srcf, target_file=tf, phase="core->target"))

    # 3) 并发执行两阶段任务（都走同一个 worker）
    max_workers = int(cfg.options.get("max_workers", 8)) if isinstance(cfg.options, dict) else 8

    def _run_tasks(tasks: List[TranslateTask]) -> Tuple[List[Tuple[TranslateTask, int]], int]:
        changed: List[Tuple[TranslateTask, int]] = []
        skipped = 0
        with cf.ThreadPoolExecutor(max_workers=max_workers) as ex:
            fut_map = {ex.submit(_translate_one_file, cfg, t, incremental, base_files_map=None): t for t in tasks}
            for fut in cf.as_completed(fut_map):
                t = fut_map[fut]
                try:
                    delta = fut.result()
                    if delta > 0:
                        changed.append((t, delta))
                    else:
                        skipped += 1
                except Exception as e:
                    print(f"❌ translate 失败: [{t.phase}] {t.locale.code} / {t.target_file.name}: {e}")
        return changed, skipped

    # 为了减少重复解析 Base，每次 _translate_one_file 内部会读取 base 文件
    # （实现简单可靠；后续性能需要再做 cache）

    changed_a, skipped_a = _run_tasks(tasks_a)
    changed_b, skipped_b = _run_tasks(tasks_b)

    total_changed = changed_a + changed_b
    total_added = sum(n for _, n in total_changed)
    print(
        f"✅ translate 任务完成：修改 {len(total_changed)} 个文件，新增/更新 {total_added} 个 key；未改动 {skipped_a + skipped_b} 个文件"
    )
    print("🔧 translate 后执行 sort（保证格式一致）...")
    data.run_sort(cfg)


def _translate_one_file(
    cfg: data.StringsI18nConfig,
    task: TranslateTask,
    incremental: bool,
    base_files_map: Optional[Dict[str, Path]] = None,
) -> int:
    """生成并写回某个 (locale, file) 的翻译结果。返回新增/更新 key 数。"""
    # Base 对应文件（用于 key 金标准 + 回退）
    base_file = (cfg.lang_root / cfg.base_folder / task.target_file.name).resolve()
    base_preamble, base_entries = data.parse_strings_file(base_file)
    base_map: Dict[str, str] = {e.key: e.value for e in base_entries if not e.key.startswith("@@")}

    # 源文件内容（base->core 时 src_file == base_file；core->target 时 src_file == pivot 文件）
    src_preamble, src_entries = data.parse_strings_file(task.src_file)
    src_map: Dict[str, str] = {e.key: e.value for e in src_entries if not e.key.startswith("@@")}

    # 目标文件当前内容
    tgt_preamble, tgt_entries = data.parse_strings_file(task.target_file)
    tgt_map: Dict[str, data.StringsEntry] = {e.key: e for e in tgt_entries}

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

        # 选择源文案：优先 src_map（pivot），缺失则回退 base
        src_val = src_map.get(key)
        if src_val is None or str(src_val).strip() == "":
            src_val = base_val

        # 真正的翻译引擎
        new_val = _translate_text(
            src_text=str(src_val),
            target_locale=task.locale,
            source_locale=cfg.source_locale if task.phase == "core->target" else cfg.base_locale,
            cfg=cfg,
            key=key,
            phase=task.phase,
        )

        comments = existing.comments if existing else []
        tgt_map[key] = data.StringsEntry(key=key, value=new_val, comments=comments)
        changed += 1

    if changed == 0:
        return 0

    new_entries_sorted = sorted(tgt_map.values(), key=lambda e: e.key)
    data.write_strings_file(task.target_file, tgt_preamble, new_entries_sorted, group_by_prefix=False)
    return changed


def _translate_text(
    *,
    src_text: str,
    target_locale: data.Locale,
    source_locale: data.Locale,
    cfg: data.StringsI18nConfig,
    key: str,
    phase: str,
) -> str:
    """翻译引擎占位实现（后续替换为真实 LLM/翻译服务）。

    现在的策略：输出一个明显可检索的占位结果，避免误把源文案当成翻译。
    """
    # 例：[[ja|core->target]] Hello
    return f"[[{target_locale.code}|{phase}]] {src_text}"
