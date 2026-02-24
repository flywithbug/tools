from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from . import data
from box_tools._share.openai_translate.translate_list import translate_list

URL_PASSTHROUGH_FILES = {"marketing_url.txt", "support_url.txt", "privacy_url.txt"}


@dataclass(frozen=True)
class _FileTask:
    target_code: str
    target_name: str
    target_asc: str
    target_dir: Path
    rel_path: Path
    source_text: str
    src_locale_name: str
    passthrough: bool
    prompt_en: Optional[str]


@dataclass(frozen=True)
class _FileResult:
    task: _FileTask
    ok: bool
    written: bool
    retries_used: int
    elapsed_sec: float
    error: Optional[str] = None


@dataclass(frozen=True)
class _PhaseStats:
    phase_name: str
    src_asc: str
    total_targets: int
    source_files: int
    planned_files: int
    written_files: int
    failed_files: int
    skipped_unresolved: int
    passthrough_files: int
    deleted_redundant: int
    scan_sec: float
    plan_sec: float
    exec_sec: float
    report_sec: float
    total_sec: float
    report_path: Optional[Path]


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

    return ([Path(x) for x in missing], [Path(x) for x in redundant], non_txt)


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


def _decide_redundant_policy(cfg: data.StringsI18nConfig) -> str:
    policy = str((cfg.options or {}).get("fastlane_redundant_policy", "")).strip().lower()
    if policy in {"delete", "keep"}:
        return policy
    if sys.stdin.isatty():
        ans = input("检测到冗余 *.txt，是否删除？(y/n) [n]: ").strip().lower()
        return "delete" if ans in {"y", "yes"} else "keep"
    return "keep"


def _delete_redundant_files(target_dir: Path, redundant: List[Path]) -> int:
    deleted = 0
    for rel in redundant:
        fp = (target_dir / rel).resolve()
        if fp.exists() and fp.is_file() and fp.suffix.lower() == ".txt":
            fp.unlink()
            deleted += 1
    return deleted


def _compute_file_workers(cfg: data.StringsI18nConfig, total_tasks: int) -> int:
    if total_tasks <= 0:
        return 1

    raw = None
    if isinstance(cfg.options, dict):
        raw = cfg.options.get("fastlane_file_workers")
        if raw is None:
            raw = cfg.options.get("fastlaneFileWorkers")
        if raw is None:
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
    guess = max(2, min(8, max(2, cpu // 2)))
    return min(guess, total_tasks)


def _get_retry_times(cfg: data.StringsI18nConfig) -> int:
    raw = None
    if isinstance(cfg.options, dict):
        raw = cfg.options.get("fastlane_retry_times")
        if raw is None:
            raw = cfg.options.get("fastlaneRetryTimes")
    try:
        n = int(raw) if raw is not None else 2
    except Exception:
        n = 2
    return max(0, n)


def _execute_file_task(
    *,
    task: _FileTask,
    model: str,
    api_key: Optional[str],
    retry_times: int,
) -> _FileResult:
    t0 = time.perf_counter()

    if task.passthrough:
        dst = (task.target_dir / task.rel_path).resolve()
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(task.source_text.strip() + "\n", encoding="utf-8")
        return _FileResult(
            task=task,
            ok=True,
            written=True,
            retries_used=0,
            elapsed_sec=time.perf_counter() - t0,
            error=None,
        )

    last_err: Optional[str] = None
    for attempt in range(retry_times + 1):
        try:
            out_items = translate_list(
                prompt_en=task.prompt_en,
                src_items=[task.source_text],
                src_locale=task.src_locale_name,
                tgt_locale=task.target_name,
                model=model,
                api_key=api_key,
            )
            translated = out_items[0].strip() if out_items else ""
            if not translated:
                raise RuntimeError("empty translation")

            dst = (task.target_dir / task.rel_path).resolve()
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_text(translated + "\n", encoding="utf-8")
            return _FileResult(
                task=task,
                ok=True,
                written=True,
                retries_used=attempt,
                elapsed_sec=time.perf_counter() - t0,
                error=None,
            )
        except Exception as e:
            last_err = str(e)
            if attempt < retry_times:
                time.sleep(min(1.5, 0.25 * (2**attempt)))

    # 最终失败：不写文件，留空
    return _FileResult(
        task=task,
        ok=False,
        written=False,
        retries_used=retry_times,
        elapsed_sec=time.perf_counter() - t0,
        error=last_err or "unknown error",
    )


def _write_phase_report(
    cfg: data.StringsI18nConfig,
    *,
    phase_name: str,
    lines: List[str],
) -> Optional[Path]:
    try:
        report_dir = (cfg.fastlane_metadata_root / ".box_strings_i18n_reports").resolve()
        report_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        slug = phase_name.lower().replace(" ", "_").replace(":", "").replace("->", "to")
        fp = report_dir / f"fastlane_{slug}_{ts}.txt"
        fp.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
        return fp
    except Exception:
        return None


def _run_phase(
    *,
    cfg: data.StringsI18nConfig,
    phase_name: str,
    src_locale: data.Locale,
    targets: List[data.Locale],
    code_to_asc: Dict[str, str],
    incremental: bool,
    fallback_src_locale: Optional[data.Locale] = None,
) -> _PhaseStats:
    phase_t0 = time.perf_counter()

    # -------------------- 1) 扫描 --------------------
    scan_t0 = time.perf_counter()
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
    scan_sec = time.perf_counter() - scan_t0

    print(f"\n🧩 {phase_name}")
    print(f"[扫描] src={src_locale.code}->{src_asc}, source_files={len(source_rel_paths)}")

    if not targets or not source_rel_paths:
        total_sec = time.perf_counter() - phase_t0
        return _PhaseStats(
            phase_name=phase_name,
            src_asc=src_asc,
            total_targets=len(targets),
            source_files=len(source_rel_paths),
            planned_files=0,
            written_files=0,
            failed_files=0,
            skipped_unresolved=0,
            passthrough_files=0,
            deleted_redundant=0,
            scan_sec=scan_sec,
            plan_sec=0.0,
            exec_sec=0.0,
            report_sec=0.0,
            total_sec=total_sec,
            report_path=None,
        )

    # -------------------- 2) 计划 --------------------
    plan_t0 = time.perf_counter()
    plan_tasks: List[_FileTask] = []
    redundant_map: Dict[str, List[Path]] = {}
    non_txt_map: Dict[str, List[Path]] = {}
    missing_map: Dict[str, List[Path]] = {}
    deleted_redundant = 0
    skipped_unresolved = 0
    passthrough_files = 0

    policy = _decide_redundant_policy(cfg)

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
        if missing:
            missing_map[tgt_asc] = missing
        if redundant:
            redundant_map[tgt_asc] = redundant
            if policy == "delete":
                deleted_redundant += _delete_redundant_files(tgt_dir, redundant)
        if non_txt:
            non_txt_map[tgt_asc] = non_txt

        work_rel = _prepare_work_rel_paths(
            source_rel_paths=source_rel_paths,
            target_dir=tgt_dir,
            incremental=incremental,
        )

        prompt = _build_prompt_en(cfg, target_code=tgt.code)
        for rel in work_rel:
            src_txt = _resolve_source_text(rel=rel, src_dir=src_dir, fallback_dir=fallback_dir)
            if not src_txt or not src_txt.strip():
                skipped_unresolved += 1
                continue
            passthrough = rel.name in URL_PASSTHROUGH_FILES
            if passthrough:
                passthrough_files += 1
            plan_tasks.append(
                _FileTask(
                    target_code=tgt.code,
                    target_name=tgt.name_en,
                    target_asc=tgt_asc,
                    target_dir=tgt_dir,
                    rel_path=rel,
                    source_text=src_txt.strip(),
                    src_locale_name=src_locale.name_en,
                    passthrough=passthrough,
                    prompt_en=prompt,
                )
            )

    plan_sec = time.perf_counter() - plan_t0
    print(
        f"[计划] targets={len(targets)}, planned_files={len(plan_tasks)}, "
        f"skipped_unresolved={skipped_unresolved}, deleted_redundant={deleted_redundant}"
    )

    # -------------------- 3) 执行 --------------------
    exec_t0 = time.perf_counter()
    written_files = 0
    failed_files = 0
    failed_items: List[_FileResult] = []

    if plan_tasks:
        workers = _compute_file_workers(cfg, len(plan_tasks))
        retry_times = _get_retry_times(cfg)
        print(f"[执行] file_workers={workers}, retry_times={retry_times}, tasks={len(plan_tasks)}")

        done = 0
        total = len(plan_tasks)
        start_exec = time.perf_counter()

        with ThreadPoolExecutor(max_workers=workers) as ex:
            fut_map = {
                ex.submit(
                    _execute_file_task,
                    task=t,
                    model=_get_model(cfg),
                    api_key=_norm_api_key(getattr(cfg, "api_key", None)),
                    retry_times=retry_times,
                ): t
                for t in plan_tasks
            }

            for fut in as_completed(fut_map):
                done += 1
                res = fut.result()
                if res.ok and res.written:
                    written_files += 1
                else:
                    failed_files += 1
                    failed_items.append(res)

                elapsed = time.perf_counter() - start_exec
                remain = total - done
                eta = (elapsed / done * remain) if done > 0 else 0.0
                rel = res.task.rel_path.as_posix()
                if res.ok:
                    print(
                        f"✅ [{done}/{total}] {res.task.target_asc}/{rel} "
                        f"(retry={res.retries_used}, {res.elapsed_sec:.2f}s) | 剩余 {remain} | ETA {eta:.1f}s"
                    )
                else:
                    print(
                        f"❌ [{done}/{total}] {res.task.target_asc}/{rel} "
                        f"(retry={res.retries_used}, {res.elapsed_sec:.2f}s) {res.error} | 剩余 {remain} | ETA {eta:.1f}s"
                    )
    else:
        print("[执行] 无任务可执行")

    exec_sec = time.perf_counter() - exec_t0

    # -------------------- 4) 报告 --------------------
    report_t0 = time.perf_counter()
    report_lines: List[str] = []
    report_lines.append(f"Phase: {phase_name}")
    report_lines.append(f"Mode: {'incremental' if incremental else 'full'}")
    report_lines.append(f"Source: {src_locale.code} -> {src_asc}")
    report_lines.append("")
    report_lines.append("Summary:")
    report_lines.append(f"- targets: {len(targets)}")
    report_lines.append(f"- source_files: {len(source_rel_paths)}")
    report_lines.append(f"- planned_files: {len(plan_tasks)}")
    report_lines.append(f"- written_files: {written_files}")
    report_lines.append(f"- failed_files: {failed_files}")
    report_lines.append(f"- skipped_unresolved: {skipped_unresolved}")
    report_lines.append(f"- passthrough_files: {passthrough_files}")
    report_lines.append(f"- deleted_redundant: {deleted_redundant}")
    report_lines.append("")
    report_lines.append("Timing:")
    report_lines.append(f"- scan_sec: {scan_sec:.2f}")
    report_lines.append(f"- plan_sec: {plan_sec:.2f}")
    report_lines.append(f"- exec_sec: {exec_sec:.2f}")

    if non_txt_map:
        report_lines.append("")
        report_lines.append("Non-txt Files In Targets:")
        for loc in sorted(non_txt_map.keys()):
            report_lines.append(f"[{loc}] count={len(non_txt_map[loc])}")
            for p in non_txt_map[loc][:30]:
                report_lines.append(f"- {p.as_posix()}")

    if redundant_map:
        report_lines.append("")
        report_lines.append("Redundant Txt Files (target has, source missing):")
        for loc in sorted(redundant_map.keys()):
            report_lines.append(f"[{loc}] count={len(redundant_map[loc])}")
            for p in redundant_map[loc][:30]:
                report_lines.append(f"- {p.as_posix()}")

    if failed_items:
        report_lines.append("")
        report_lines.append("Failed Files:")
        for r in failed_items[:200]:
            report_lines.append(
                f"- {r.task.target_asc}/{r.task.rel_path.as_posix()} | retry={r.retries_used} | err={r.error}"
            )

    report_path = _write_phase_report(cfg, phase_name=phase_name, lines=report_lines)
    report_sec = time.perf_counter() - report_t0
    total_sec = time.perf_counter() - phase_t0

    print(
        f"[报告] written={written_files}, failed={failed_files}, "
        f"redundant={sum(len(v) for v in redundant_map.values())}, "
        f"non_txt={sum(len(v) for v in non_txt_map.values())}, elapsed={total_sec:.2f}s"
    )
    if report_path is not None:
        print(f"📄 report: {report_path}")

    return _PhaseStats(
        phase_name=phase_name,
        src_asc=src_asc,
        total_targets=len(targets),
        source_files=len(source_rel_paths),
        planned_files=len(plan_tasks),
        written_files=written_files,
        failed_files=failed_files,
        skipped_unresolved=skipped_unresolved,
        passthrough_files=passthrough_files,
        deleted_redundant=deleted_redundant,
        scan_sec=scan_sec,
        plan_sec=plan_sec,
        exec_sec=exec_sec,
        report_sec=report_sec,
        total_sec=total_sec,
        report_path=report_path,
    )


def translate_base_to_core(cfg: data.StringsI18nConfig, incremental: bool = True) -> None:
    code_to_asc = _load_code_to_asc(cfg)
    src_asc = _asc_code(cfg.base_locale, code_to_asc)
    targets = [x for x in cfg.core_locales if _asc_code(x, code_to_asc) != src_asc]
    targets = _dedup_targets_by_asc(targets, code_to_asc)

    _run_phase(
        cfg=cfg,
        phase_name="Phase 1: base_locale -> core_locales",
        src_locale=cfg.base_locale,
        targets=targets,
        code_to_asc=code_to_asc,
        incremental=incremental,
    )


def translate_source_to_target(cfg: data.StringsI18nConfig, incremental: bool = True) -> None:
    code_to_asc = _load_code_to_asc(cfg)
    src_asc = _asc_code(cfg.source_locale, code_to_asc)
    base_asc = _asc_code(cfg.base_locale, code_to_asc)

    targets = [
        x
        for x in cfg.target_locales
        if _asc_code(x, code_to_asc) not in {src_asc, base_asc}
    ]
    targets = _dedup_targets_by_asc(targets, code_to_asc)

    _run_phase(
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
    print(f"- file_workers(auto): { _compute_file_workers(cfg, 9999) }")
    print(f"- retry_times: { _get_retry_times(cfg) }")

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
