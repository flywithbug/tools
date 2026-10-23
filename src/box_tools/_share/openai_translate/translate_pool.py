from __future__ import annotations

import os
import sys
import time
import threading
from dataclasses import dataclass
from typing import Dict, List, Optional, Union, Literal, Tuple

from .models import OpenAIModel, load_map
from .translate_file import translate_from_to, FileProgress


# =========================================================
# Public API: multi-file translation pool
# =========================================================


@dataclass(frozen=True)
class TranslateJob:
    """
    A single file translation task.

    Notes:
    - source/target file paths can be .json or .strings
    - per-file translation is still internally serial (chunked) in translate_from_to
    """

    source_file_path: str
    target_file_path: str
    src_locale: str
    tgt_locale: str

    # optional overrides
    prompt_en: Optional[str] = None
    batch_size: int = 40
    pre_sort: bool = True

    # optional UI label (defaults to basename(target_file_path))
    name: Optional[str] = None


@dataclass
class JobResult:
    job: TranslateJob
    ok: bool
    todo: int
    translated: int
    started_at: float
    finished_at: float
    error: Optional[str] = None

    @property
    def elapsed_s(self) -> float:
        return max(0.0, self.finished_at - self.started_at)


@dataclass
class PoolResult:
    results: List[JobResult]

    @property
    def ok_count(self) -> int:
        return sum(1 for r in self.results if r.ok)

    @property
    def fail_count(self) -> int:
        return sum(1 for r in self.results if not r.ok)

    @property
    def total_todo(self) -> int:
        return sum(r.todo for r in self.results)

    @property
    def total_translated(self) -> int:
        return sum(r.translated for r in self.results)

    @property
    def total_elapsed_s(self) -> float:
        if not self.results:
            return 0.0
        start = min(r.started_at for r in self.results)
        end = max(r.finished_at for r in self.results)
        return max(0.0, end - start)


# =========================================================
# Internal: plan/todo (mirror translate_file._incremental_jobs)
# =========================================================


def _count_incremental_todo(source_file_path: str, target_file_path: str) -> int:
    """
    Mirror translate_file._incremental_jobs rules to compute 'todo' without importing private helpers.
    Rules:
    - source value empty => just sync key (no translation)
    - if key missing in target OR target[key] == "" => needs translation
    """
    src = load_map(source_file_path)
    tgt = load_map(target_file_path)
    todo = 0
    for k, src_text in src.items():
        if (src_text or "").strip() == "":
            continue
        if k not in tgt or (tgt.get(k, "") == ""):
            todo += 1
    return todo


# =========================================================
# Internal: time utils
# =========================================================


def _fmt_hms(seconds: float) -> str:
    s = max(0, int(seconds))
    h = s // 3600
    m = (s % 3600) // 60
    ss = s % 60
    if h > 0:
        return f"{h}:{m:02d}:{ss:02d}"
    return f"{m:02d}:{ss:02d}"


def _now() -> float:
    return time.time()


def _basename(path: str) -> str:
    return os.path.basename(path.rstrip("/")) or path


@dataclass
class _WorkerLine:
    job_id: str
    name: str
    todo: int
    done: int = 0
    stage: Literal["pending", "running", "done", "error"] = "pending"
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    message: Optional[str] = None
    error: Optional[str] = None

    def elapsed_s(self, now: float) -> float:
        if self.started_at is None:
            return 0.0
        end = self.finished_at if self.finished_at is not None else now
        return max(0.0, end - self.started_at)


class _LinearLogger:
    """
    线性日志（追加写入）进度输出（无面板重绘）：
      START：worker 开始处理文件
      PROG ：进度推进（节流）
      DONE/ERR：结束（失败带原因摘要）

    关键修复点：
    - 不要求 _WorkerLine 具备 tgt_locale 字段
    - logger 自己维护 job_id -> tgt_locale 映射，避免版本不一致导致 TypeError
    """

    def __init__(
        self,
        *,
        total_workers: int,
        progress_every_keys: int = 40,
        progress_every_seconds: float = 1.5,
        stream=None,
    ) -> None:
        self.total_workers = total_workers
        self.progress_every_keys = max(1, int(progress_every_keys))
        self.progress_every_seconds = max(0.1, float(progress_every_seconds))
        self.stream = stream or sys.stdout

        self._lock = threading.Lock()
        self._t0 = _now()

        self._worker_for: Dict[str, int] = {}
        self._free_workers: List[int] = list(range(1, total_workers + 1))

        self._last_done: Dict[str, int] = {}
        self._last_print_at: Dict[str, float] = {}

        self._line: Dict[str, _WorkerLine] = {}
        self._tgt_locale: Dict[str, str] = {}  # ✅ fix: store tgt locale here

    def init_jobs(self, jobs: List[Tuple[str, TranslateJob, int]]) -> None:
        with self._lock:
            self._line.clear()
            self._tgt_locale.clear()
            self._worker_for.clear()
            self._free_workers = list(range(1, self.total_workers + 1))
            self._last_done.clear()
            self._last_print_at.clear()

            for job_id, job, todo in jobs:
                name = job.name or _basename(job.target_file_path)
                self._line[job_id] = _WorkerLine(
                    job_id=job_id,
                    name=name,
                    todo=todo,
                    stage="pending",
                )
                self._tgt_locale[job_id] = job.tgt_locale
                self._last_done[job_id] = 0
                self._last_print_at[job_id] = self._t0

            start_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self._t0))
            self._w(
                f"[translate_pool] start={start_str} workers={self.total_workers} files={len(jobs)}"
            )
            self._w("[translate_pool] plan (first 8): name | target | todo")
            for job_id, job, todo in jobs[:8]:
                name = job.name or _basename(job.target_file_path)
                self._w(f"  - {name} | {job.tgt_locale} | todo={todo}")
            remain = len(jobs) - min(8, len(jobs))
            if remain > 0:
                self._w(f"  ... +{remain} more")
            self._w("")

    def mark_running(self, job_id: str) -> None:
        with self._lock:
            line = self._line[job_id]
            if job_id not in self._worker_for:
                slot = self._free_workers.pop(0) if self._free_workers else 0
                self._worker_for[job_id] = slot
            line.stage = "running"
            line.started_at = line.started_at or _now()
            self._print_start(job_id)

    def apply_progress(self, job_id: str, p: FileProgress) -> None:
        with self._lock:
            line = self._line[job_id]

            # keep todo stable: avoid 0/0 caused by early error emits
            if p.total and p.total > 0:
                line.todo = p.total
            if p.done >= 0:
                line.done = p.done
            if p.message:
                line.message = p.message

            if p.stage == "start":
                if job_id not in self._worker_for:
                    slot = self._free_workers.pop(0) if self._free_workers else 0
                    self._worker_for[job_id] = slot
                line.stage = "running"
                line.started_at = line.started_at or _now()
                self._print_start(job_id)
                return

            if p.stage == "progress":
                self._maybe_print_progress(job_id)
                return

            if p.stage == "done":
                line.stage = "done"
                line.finished_at = _now()
                self._print_done(job_id, ok=True, err=None)
                self._release_worker(job_id)
                return

            if p.stage == "error":
                line.stage = "error"
                line.finished_at = _now()
                line.error = p.message or "error"
                self._print_done(job_id, ok=False, err=line.error)
                self._release_worker(job_id)
                return

    def on_exception(self, job_id: str, err: str) -> None:
        """兜底：确保异常也一定输出 ERR 行（带原因摘要）。"""
        with self._lock:
            line = self._line.get(job_id)
            if not line or line.stage in ("done", "error"):
                return
            line.stage = "error"
            line.finished_at = _now()
            line.error = err or "error"
            self._print_done(job_id, ok=False, err=line.error)
            self._release_worker(job_id)

    def finalize(self, results: List[JobResult]) -> None:
        with self._lock:
            ok = sum(1 for r in results if r.ok)
            fail = len(results) - ok
            total_elapsed = 0.0
            if results:
                start = min(r.started_at for r in results)
                end = max(r.finished_at for r in results)
                total_elapsed = max(0.0, end - start)

            self._w("")
            self._w(
                f"[translate_pool] summary ok={ok} fail={fail} total_elapsed={_fmt_hms(total_elapsed)}"
            )
            for r in results:
                status = "OK" if r.ok else "FAIL"
                name = r.job.name or _basename(r.job.target_file_path)
                msg = f"  {status:<4} {name:<18} | {r.job.tgt_locale:<16} | "
                f"{r.translated:>5}/{r.todo:<5} | {_fmt_hms(r.elapsed_s)}"
                if r.error:
                    msg += f" | error={self._short(r.error)}"
                self._w(msg)

    def _release_worker(self, job_id: str) -> None:
        slot = self._worker_for.pop(job_id, None)
        if slot and slot not in self._free_workers:
            self._free_workers.append(slot)
            self._free_workers.sort()

    def _print_start(self, job_id: str) -> None:
        line = self._line[job_id]
        slot = self._worker_for.get(job_id, 0)
        self._last_done[job_id] = max(self._last_done.get(job_id, 0), line.done)
        self._last_print_at[job_id] = _now()
        self._w(self._fmt_line(slot, job_id, "START", line, extra=None))

    def _maybe_print_progress(self, job_id: str) -> None:
        line = self._line[job_id]
        slot = self._worker_for.get(job_id, 0)
        now = _now()

        last_done = self._last_done.get(job_id, 0)
        last_at = self._last_print_at.get(job_id, self._t0)

        bumped_enough = (line.done - last_done) >= self.progress_every_keys
        waited_enough = (now - last_at) >= self.progress_every_seconds

        if (line.done > last_done) and (bumped_enough or waited_enough):
            self._last_done[job_id] = line.done
            self._last_print_at[job_id] = now
            self._w(self._fmt_line(slot, job_id, "PROG ", line, extra=None))

    def _print_done(self, job_id: str, *, ok: bool, err: Optional[str]) -> None:
        line = self._line[job_id]
        slot = self._worker_for.get(job_id, 0)
        tag = "DONE " if ok else "ERR  "
        extra = self._short(err) if err else None
        self._w(self._fmt_line(slot, job_id, tag, line, extra=extra))

    def _fmt_line(
        self, slot: int, job_id: str, tag: str, line: _WorkerLine, extra: Optional[str]
    ) -> str:
        t = _fmt_hms(_now() - self._t0)
        el = _fmt_hms(line.elapsed_s(_now()))
        w = f"W{slot}" if slot else "W?"
        tgt = self._tgt_locale.get(job_id, "")
        base = f"[{t}] {w} {tag} {line.name:<18} | {tgt:<16} | {line.done:>5}/{line.todo:<5} | {el}"
        if extra:
            return base + f" | {extra}"
        return base

    def _short(self, s: Optional[str]) -> str:
        if not s:
            return ""
        s2 = str(s).replace("\n", " ").strip()
        if len(s2) > 140:
            return s2[:137] + "..."
        return s2

    def _w(self, s: str) -> None:
        self.stream.write(s + "\n")
        self.stream.flush()


# =========================================================
# Public function: translate_files
# =========================================================


def translate_files(
    *,
    jobs: List[TranslateJob],
    api_key: Optional[str] = None,
    model: Optional[Union[OpenAIModel, str]] = None,
    max_workers: int = 4,
    pending_brief_lines: int = 3,  # kept for backwards compat (unused in linear logger)
    fail_fast: bool = False,
) -> PoolResult:
    """
    Translate many files concurrently (file-level parallelism).
    Within each file, translation remains serial (chunked) inside translate_from_to.
    """
    if not jobs:
        return PoolResult(results=[])

    max_workers = max(1, int(max_workers))
    max_workers = min(max_workers, len(jobs))

    planned: List[Tuple[str, TranslateJob, int]] = []
    for idx, j in enumerate(jobs):
        job_id = f"job{idx+1}"
        todo = _count_incremental_todo(j.source_file_path, j.target_file_path)
        planned.append((job_id, j, todo))

    logger = _LinearLogger(
        total_workers=max_workers, progress_every_keys=40, progress_every_seconds=1.5
    )
    logger.init_jobs(planned)

    from concurrent.futures import ThreadPoolExecutor, Future, wait, FIRST_COMPLETED

    results: Dict[str, JobResult] = {}
    lock = threading.Lock()

    # Validate: avoid same target file being written by multiple workers (danger!)
    seen_targets: Dict[str, str] = {}
    for job_id, job, _ in planned:
        tgt = os.path.abspath(job.target_file_path)
        if tgt in seen_targets:
            raise ValueError(
                f"duplicate target_file_path detected: {job.target_file_path} "
                f"(jobs: {seen_targets[tgt]}, {job_id})"
            )
        seen_targets[tgt] = job_id

    pending_ids = [job_id for job_id, _, _ in planned]
    todo_by_id = {job_id: todo for job_id, _, todo in planned}
    job_by_id = {job_id: job for job_id, job, _ in planned}

    def run_one(job_id: str) -> None:
        job = job_by_id[job_id]
        started = _now()
        translated = 0
        err: Optional[str] = None
        ok = True

        logger.mark_running(job_id)

        try:

            def cb(p: FileProgress) -> None:
                nonlocal translated
                if p.stage in ("progress", "done"):
                    translated = max(translated, int(p.done))
                logger.apply_progress(job_id, p)

            translate_from_to(
                source_file_path=job.source_file_path,
                target_file_path=job.target_file_path,
                src_locale=job.src_locale,
                tgt_locale=job.tgt_locale,
                model=model,
                api_key=api_key,
                prompt_en=job.prompt_en,
                progress=cb,
                batch_size=job.batch_size,
                pre_sort=job.pre_sort,
            )
        except Exception as e:
            ok = False
            err = str(e)
            # 兜底：确保线性日志一定打印失败原因（即使 translate_from_to 没 emit）
            logger.on_exception(job_id, err)
        finally:
            finished = _now()
            res = JobResult(
                job=job,
                ok=ok,
                todo=todo_by_id.get(job_id, 0),
                translated=translated,
                started_at=started,
                finished_at=finished,
                error=err,
            )
            with lock:
                results[job_id] = res

    in_flight: Dict[Future[None], str] = {}
    stop_scheduling = False

    def submit_next(ex: ThreadPoolExecutor) -> None:
        nonlocal stop_scheduling
        if stop_scheduling:
            return
        if not pending_ids:
            return
        jid = pending_ids.pop(0)
        fut = ex.submit(run_one, jid)
        in_flight[fut] = jid

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        for _ in range(max_workers):
            submit_next(ex)

        while in_flight:
            done_set, _ = wait(list(in_flight.keys()), return_when=FIRST_COMPLETED)
            for fut in done_set:
                jid = in_flight.pop(fut)
                try:
                    fut.result()
                except Exception:
                    # run_one catches, but guard anyway
                    pass

                if fail_fast:
                    r = results.get(jid)
                    if r is not None and (not r.ok):
                        stop_scheduling = True

                submit_next(ex)

            if stop_scheduling and pending_ids:
                pending_ids.clear()

    ordered_results: List[JobResult] = []
    for job_id, job, _ in planned:
        r = results.get(job_id)
        if r is None:
            now = _now()
            ordered_results.append(
                JobResult(
                    job=job,
                    ok=False,
                    todo=todo_by_id.get(job_id, 0),
                    translated=0,
                    started_at=now,
                    finished_at=now,
                    error="not executed",
                )
            )
        else:
            ordered_results.append(r)

    logger.finalize(ordered_results)
    return PoolResult(results=ordered_results)
