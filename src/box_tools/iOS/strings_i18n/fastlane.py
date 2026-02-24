from __future__ import annotations

import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from . import data
from box_tools._share.openai_translate.translate_list import translate_list

URL_PASSTHROUGH_FILES = {"marketing_url.txt", "support_url.txt", "privacy_url.txt"}


@dataclass(frozen=True)
class _TargetTranslateTask:
    target_code: str
    target_name: str
    target_asc: str
    target_dir: Path
    src_map: Dict[str, str]
    prompt_en: Optional[str]


def _norm_api_key(v: Any) -> Optional[str]:
    if isinstance(v, str):
        s = v.strip()
        return s or None
    return None


def _get_model(cfg: data.StringsI18nConfig) -> str:
    m0 = getattr(cfg, "openai_model", None)
    if isinstance(m0, str) and m0.strip():
        return m0.strip()

    if isinstance(cfg.options, dict):
        m = (
            cfg.options.get("model")
            or cfg.options.get("openai_model")
            or cfg.options.get("openaiModel")
        )
        if isinstance(m, str) and m.strip():
            return m.strip()

    return os.environ.get("OPENAI_MODEL", "gpt-4o-mini")


def _build_prompt_en(cfg: data.StringsI18nConfig, target_code: str) -> Optional[str]:
    prompts = cfg.prompts or {}
    default_en = (prompts.get("default_en") or "").strip()
    by_locale_en = prompts.get("by_locale_en") or {}
    extra = (
        (by_locale_en.get(target_code) or "").strip()
        if isinstance(by_locale_en, dict)
        else ""
    )
    parts = [x for x in [default_en, extra] if x]
    return "\n\n".join(parts) if parts else None


def _read_text(fp: Path) -> str:
    if not fp.exists():
        return ""
    try:
        return fp.read_text(encoding="utf-8")
    except Exception:
        return ""


def _load_code_to_asc(cfg: data.StringsI18nConfig) -> Dict[str, str]:
    out: Dict[str, str] = {}
    try:
        arr = json.loads(cfg.languages_path.read_text(encoding="utf-8"))
    except Exception:
        return out

    if not isinstance(arr, list):
        return out

    for it in arr:
        if not isinstance(it, dict):
            continue
        code = str(it.get("code", "")).strip()
        asc = str(it.get("asc_code", "")).strip()
        if code and asc:
            out[code] = asc

    return out


def _asc_code(loc: data.Locale, code_to_asc: Dict[str, str]) -> str:
    cfg_asc = (loc.asc_code or "").strip()
    if cfg_asc and cfg_asc != loc.code:
        return cfg_asc
    mapped = (code_to_asc.get(loc.code) or "").strip()
    if mapped:
        return mapped
    if cfg_asc:
        return cfg_asc
    return loc.code.strip()


def _dedup_targets_by_asc(
    locales: List[data.Locale], code_to_asc: Dict[str, str]
) -> List[data.Locale]:
    seen: Set[str] = set()
    out: List[data.Locale] = []
    for loc in locales:
        asc = _asc_code(loc, code_to_asc)
        if not asc or asc in seen:
            continue
        seen.add(asc)
        out.append(loc)
    return out


def _iter_txt_rel_paths(root_dir: Path) -> List[Path]:
    if not root_dir.exists() or not root_dir.is_dir():
        return []
    files = [
        p.relative_to(root_dir)
        for p in root_dir.rglob("*")
        if p.is_file() and p.suffix.lower() == ".txt"
    ]
    return sorted(files, key=lambda x: x.as_posix().lower())


def _iter_non_txt_rel_paths(root_dir: Path) -> List[Path]:
    if not root_dir.exists() or not root_dir.is_dir():
        return []
    files = [
        p.relative_to(root_dir)
        for p in root_dir.rglob("*")
        if p.is_file() and p.suffix.lower() != ".txt"
    ]
    return sorted(files, key=lambda x: x.as_posix().lower())


def _scan_target_integrity(
    *, source_rel_paths: List[Path], target_dir: Path
) -> Tuple[List[Path], List[Path], List[Path]]:
    src_set = {p.as_posix() for p in source_rel_paths}
    tgt_txt = _iter_txt_rel_paths(target_dir)
    tgt_set = {p.as_posix() for p in tgt_txt}

    missing = sorted(src_set - tgt_set)
    redundant = sorted(tgt_set - src_set)
    non_txt = _iter_non_txt_rel_paths(target_dir)

    return (
        [Path(x) for x in missing],
        [Path(x) for x in redundant],
        non_txt,
    )


def _prepare_work_rel_paths(
    *, source_rel_paths: List[Path], target_dir: Path, incremental: bool
) -> List[Path]:
    if not incremental:
        return source_rel_paths

    out: List[Path] = []
    for rel in source_rel_paths:
        fp = (target_dir / rel).resolve()
        # 增量：仅缺失文件或空文件
        if not fp.exists():
            out.append(rel)
            continue
        if not _read_text(fp).strip():
            out.append(rel)

    return out


def _resolve_source_text(
    *, rel: Path, src_dir: Path, fallback_dir: Optional[Path]
) -> Optional[str]:
    txt = _read_text((src_dir / rel).resolve())
    if txt.strip():
        return txt
    if fallback_dir is not None:
        fb = _read_text((fallback_dir / rel).resolve())
        if fb.strip():
            return fb
    return None


def _handle_redundant_files(
    cfg: data.StringsI18nConfig,
    *,
    target_dir: Path,
    redundant: List[Path],
    allow_prompt: bool,
) -> None:
    if not redundant:
        return

    print(f"⚠️ 冗余文件（目标有、源无）：{len(redundant)}")
    for p in redundant[:20]:
        print(f"- {target_dir.name}/{p.as_posix()}")
    if len(redundant) > 20:
        print(f"- ... 还有 {len(redundant) - 20} 个")

    policy = (
        str((cfg.options or {}).get("fastlane_redundant_policy", "")).strip().lower()
    )
    if policy not in {"delete", "keep"}:
        if allow_prompt and sys.stdin.isatty():
            ans = input("是否删除这些冗余 *.txt？(y/n) [n]: ").strip().lower()
            policy = "delete" if ans in {"y", "yes"} else "keep"
        else:
            policy = "keep"

    if policy != "delete":
        return

    deleted = 0
    for rel in redundant:
        fp = (target_dir / rel).resolve()
        if fp.exists() and fp.is_file() and fp.suffix.lower() == ".txt":
            fp.unlink()
            deleted += 1
    print(f"🧹 已删除冗余文件：{deleted}")


def _compute_fastlane_workers(cfg: data.StringsI18nConfig, total_tasks: int) -> int:
    if total_tasks <= 0:
        return 1
    raw = None
    if isinstance(cfg.options, dict):
        raw = cfg.options.get("fastlane_max_workers")
        if raw is None:
            raw = cfg.options.get("fastlaneMaxWorkers")
    try:
        v = int(raw) if raw is not None else 0
    except Exception:
        v = 0
    if v > 0:
        return max(1, min(v, total_tasks))
    cpu = os.cpu_count() or 4
    guess = max(2, min(6, max(2, cpu // 2)))
    return min(guess, total_tasks)


def _run_target_translate_task(
    *,
    task: _TargetTranslateTask,
    src_locale_name: str,
    model: str,
    api_key: Optional[str],
) -> Tuple[str, int, float]:
    t0 = time.perf_counter()
    keys = list(task.src_map.keys())
    src_items = [task.src_map[k] for k in keys]
    out_items = translate_list(
        prompt_en=task.prompt_en,
        src_items=src_items,
        src_locale=src_locale_name,
        tgt_locale=task.target_name,
        model=model,
        api_key=api_key,
    )
    out = {k: v for k, v in zip(keys, out_items)}

    translated_count = 0
    for k, v in out.items():
        if k.startswith("@@"):
            continue
        if not isinstance(v, str) or not v.strip():
            continue
        rel = Path(k)
        dst_fp = (task.target_dir / rel).resolve()
        dst_fp.parent.mkdir(parents=True, exist_ok=True)
        dst_fp.write_text(v.strip() + "\n", encoding="utf-8")
        translated_count += 1
    elapsed = time.perf_counter() - t0
    return task.target_asc, translated_count, elapsed


def _translate_phase(
    *,
    cfg: data.StringsI18nConfig,
    phase_name: str,
    src_locale: data.Locale,
    targets: List[data.Locale],
    code_to_asc: Dict[str, str],
    incremental: bool,
    fallback_src_locale: Optional[data.Locale] = None,
) -> None:
    if not targets:
        print(f"⚠️ {phase_name}：目标为空，跳过。")
        return

    model = _get_model(cfg)
    api_key = _norm_api_key(getattr(cfg, "api_key", None))
    root = cfg.fastlane_metadata_root

    src_asc = _asc_code(src_locale, code_to_asc)
    src_dir = (root / src_asc).resolve()
    fallback_dir = None
    if fallback_src_locale is not None:
        fallback_dir = (root / _asc_code(fallback_src_locale, code_to_asc)).resolve()

    src_rel = set(_iter_txt_rel_paths(src_dir))
    if fallback_dir is not None:
        src_rel.update(_iter_txt_rel_paths(fallback_dir))
    source_rel_paths = sorted(src_rel, key=lambda x: x.as_posix().lower())

    print(f"\n🧩 {phase_name}")
    print(f"- src: {src_locale.code} -> {src_asc}")
    print(f"- tgt: {[f'{x.code}->{_asc_code(x, code_to_asc)}' for x in targets]}")
    print(f"- source files: {len(source_rel_paths)}")

    if not source_rel_paths:
        print(f"⚠️ {phase_name}：源目录没有可翻译的 *.txt，跳过。")
        return

    total_candidates = 0
    total_written = 0
    total_fail_targets = 0
    start = time.perf_counter()
    translate_tasks: List[_TargetTranslateTask] = []
    use_threads = not (sys.stdin.isatty() and str((cfg.options or {}).get("fastlane_redundant_policy", "")).strip() == "")

    for tgt in targets:
        tgt_asc = _asc_code(tgt, code_to_asc)
        if not tgt_asc or tgt_asc == src_asc:
            continue

        tgt_dir = (root / tgt_asc).resolve()
        tgt_dir.mkdir(parents=True, exist_ok=True)

        missing, redundant, non_txt = _scan_target_integrity(
            source_rel_paths=source_rel_paths,
            target_dir=tgt_dir,
        )
        if non_txt:
            print(f"⚠️ {tgt_asc} 下发现非 *.txt 文件：{len(non_txt)}")
            for p in non_txt[:10]:
                print(f"- {tgt_asc}/{p.as_posix()}")
            if len(non_txt) > 10:
                print(f"- ... 还有 {len(non_txt) - 10} 个")

        _handle_redundant_files(
            cfg,
            target_dir=tgt_dir,
            redundant=redundant,
            allow_prompt=not use_threads,
        )

        work_rel_paths = _prepare_work_rel_paths(
            source_rel_paths=source_rel_paths,
            target_dir=tgt_dir,
            incremental=incremental,
        )

        if not work_rel_paths:
            print(f"✅ {tgt_asc} 无需翻译（目标文件已齐全）。")
            continue

        total_candidates += len(work_rel_paths)

        passthrough_count = 0
        src_map: Dict[str, str] = {}
        for rel in work_rel_paths:
            src_txt = _resolve_source_text(
                rel=rel, src_dir=src_dir, fallback_dir=fallback_dir
            )
            if not src_txt or not src_txt.strip():
                continue

            if rel.name in URL_PASSTHROUGH_FILES:
                dst_fp = (tgt_dir / rel).resolve()
                dst_fp.parent.mkdir(parents=True, exist_ok=True)
                dst_fp.write_text(src_txt.strip() + "\n", encoding="utf-8")
                passthrough_count += 1
                total_written += 1
                continue

            src_map[rel.as_posix()] = src_txt.strip()

        if src_map:
            translate_tasks.append(
                _TargetTranslateTask(
                    target_code=tgt.code,
                    target_name=tgt.name_en,
                    target_asc=tgt_asc,
                    target_dir=tgt_dir,
                    src_map=src_map,
                    prompt_en=_build_prompt_en(cfg, target_code=tgt.code),
                )
            )
            print(
                f"⏳ ({phase_name}) {src_locale.code}->{tgt.code} [{src_asc}->{tgt_asc}] "
                f"{len(src_map)} file(s) 已加入队列"
            )
        print(
            f"ℹ️ {tgt_asc} 预检查: passthrough={passthrough_count}, missing={len(missing)}, queued={len(src_map)}"
        )

    if translate_tasks:
        max_workers = _compute_fastlane_workers(cfg, len(translate_tasks))
        print(f"- 并发: {max_workers} workers（target-level）")
        done_targets = 0
        done_files = 0
        sum_task_elapsed = 0.0
        translate_wall_start = time.perf_counter()
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            fut_map = {
                ex.submit(
                    _run_target_translate_task,
                    task=t,
                    src_locale_name=src_locale.name_en,
                    model=model,
                    api_key=api_key,
                ): t
                for t in translate_tasks
            }
            total_targets = len(translate_tasks)
            for fut in as_completed(fut_map):
                t = fut_map[fut]
                done_targets += 1
                try:
                    tgt_asc, translated_count, task_elapsed = fut.result()
                    done_files += translated_count
                    total_written += translated_count
                    sum_task_elapsed += task_elapsed
                    print(
                        f"✅ {tgt_asc} 翻译完成: +{translated_count} file(s) "
                        f"| 耗时 {task_elapsed:.2f}s | 进度 {done_targets}/{total_targets}"
                    )
                except Exception as e:
                    total_fail_targets += 1
                    print(
                        f"❌ {t.target_asc} 翻译失败: {e} "
                        f"| 进度 {done_targets}/{total_targets}"
                    )
                print(
                    f"📈 累计进度: translated_files={done_files}, "
                    f"failed_targets={total_fail_targets}, total_targets={total_targets}"
                )
        translate_wall_elapsed = time.perf_counter() - translate_wall_start
        print(
            f"⏱️ 翻译阶段耗时: wall={translate_wall_elapsed:.2f}s, "
            f"sum_tasks={sum_task_elapsed:.2f}s"
        )
    else:
        print("ℹ️ 没有可翻译任务（所有目标都已是最新）。")

    elapsed = time.perf_counter() - start
    print(
        f"🎉 {phase_name} 完成：written={total_written}, "
        f"candidates={total_candidates}, failed_targets={total_fail_targets}, elapsed={elapsed:.2f}s"
    )


def translate_base_to_core(
    cfg: data.StringsI18nConfig, incremental: bool = True
) -> None:
    code_to_asc = _load_code_to_asc(cfg)
    src_asc = _asc_code(cfg.base_locale, code_to_asc)

    targets = [x for x in cfg.core_locales if _asc_code(x, code_to_asc) != src_asc]
    targets = _dedup_targets_by_asc(targets, code_to_asc)

    _translate_phase(
        cfg=cfg,
        phase_name="Phase 1: base_locale -> core_locales",
        src_locale=cfg.base_locale,
        targets=targets,
        code_to_asc=code_to_asc,
        incremental=incremental,
    )


def translate_source_to_target(
    cfg: data.StringsI18nConfig, incremental: bool = True
) -> None:
    code_to_asc = _load_code_to_asc(cfg)
    src_asc = _asc_code(cfg.source_locale, code_to_asc)
    base_asc = _asc_code(cfg.base_locale, code_to_asc)

    targets = [
        x
        for x in cfg.target_locales
        if _asc_code(x, code_to_asc) not in {src_asc, base_asc}
    ]
    targets = _dedup_targets_by_asc(targets, code_to_asc)

    _translate_phase(
        cfg=cfg,
        phase_name="Phase 2: source_locale -> target_locales",
        src_locale=cfg.source_locale,
        targets=targets,
        code_to_asc=code_to_asc,
        incremental=incremental,
        fallback_src_locale=cfg.base_locale,
    )


def run_fastlane(cfg: data.StringsI18nConfig, incremental: bool = True) -> None:
    mode = "增量" if incremental else "全量"
    print("🚀 fastlane metadata translate")
    print(f"- 模式: {mode}")
    print(f"- metadata root: {cfg.fastlane_metadata_root}")

    legacy_en_dir = (cfg.fastlane_metadata_root / "en").resolve()
    if legacy_en_dir.exists() and legacy_en_dir.is_dir():
        print(f"⚠️ 检测到旧目录：{legacy_en_dir}（建议迁移到 en-US）")

    if sys.stdin.isatty():
        while True:
            print("\n=== fastlane phases ===")
            print("1. base_locale -> core_locales")
            print("2. source_locale -> target_locales")
            print("3. 回退")
            print("0. 退出")
            choice = input("> ").strip()
            if choice == "1":
                translate_base_to_core(cfg, incremental=incremental)
            elif choice == "2":
                translate_source_to_target(cfg, incremental=incremental)
            elif choice == "3":
                return
            elif choice == "0":
                raise SystemExit(0)
            else:
                print("请输入 1/2/3/0")
    else:
        translate_base_to_core(cfg, incremental=incremental)
        translate_source_to_target(cfg, incremental=incremental)
