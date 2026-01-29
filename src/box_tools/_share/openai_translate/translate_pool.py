from __future__ import annotations

import os
import sys
import time
import threading
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Union, Literal, Tuple

from .models import OpenAIModel, load_map
from .translate_file import translate_from_to, FileProgress, ProgressCallback


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
# Internal: clean progress panel (TTY)
# =========================================================

class _Ansi:
    ESC = "\033"

    @staticmethod
    def clear_screen() -> str:
        return _Ansi.ESC + "[2J" + _Ansi.ESC + "[H"

    @staticmethod
    def home() -> str:
        return _Ansi.ESC + "[H"

    @staticmethod
    def hide_cursor() -> str:
        return _Ansi.ESC + "[?25l"

    @staticmethod
    def show_cursor() -> str:
        return _Ansi.ESC + "[?25h"

    @staticmethod
    def clear_line() -> str:
        return _Ansi.ESC + "[2K"


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
    tgt_locale: str
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


class _ProgressPanel:
    """
    A tidy 3-section panel:
    - Done (top)
    - Running (middle, 1 line per worker)
    - Pending (bottom)
    Refreshes in-place (TTY only).
    """
    def __init__(
            self,
            *,
            total_workers: int,
            pending_brief_lines: int = 3,
            refresh_min_interval_s: float = 0.08,
            stream = None,
    ) -> None:
        self.total_workers = total_workers
        self.pending_brief_lines = max(0, int(pending_brief_lines))
        self.refresh_min_interval_s = max(0.01, float(refresh_min_interval_s))
        self.stream = stream or sys.stdout

        self._lock = threading.Lock()
        self._started_at = _now()
        self._last_render_at = 0.0
        self._enabled = bool(getattr(self.stream, "isatty", lambda: False)())

        self._lines: Dict[str, _WorkerLine] = {}
        self._done_order: List[str] = []  # newest done first
        self._running_order: List[str] = []  # stable insertion order
        self._pending_order: List[str] = []  # stable

        self._cursor_hidden = False

    @property
    def enabled(self) -> bool:
        return self._enabled

    def init_jobs(self, jobs: List[Tuple[str, TranslateJob, int]]) -> None:
        """
        jobs: list of (job_id, job, todo)
        """
        with self._lock:
            self._lines.clear()
            self._done_order.clear()
            self._running_order.clear()
            self._pending_order.clear()

            for job_id, job, todo in jobs:
                name = job.name or _basename(job.target_file_path)
                self._lines[job_id] = _WorkerLine(job_id=job_id, name=name, tgt_locale=job.tgt_locale, todo=todo, stage="pending")
                self._pending_order.append(job_id)

            if self._enabled:
                self._hide_cursor()
                self._render(force=True)
            else:
                # Non-TTY: print a compact header once
                self.stream.write(f"[translate_pool] workers={self.total_workers} files={len(jobs)} start={time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(self._started_at))}\n")
                self.stream.flush()

    def mark_running(self, job_id: str) -> None:
        with self._lock:
            line = self._lines[job_id]
            line.stage = "running"
            line.started_at = line.started_at or _now()
            if job_id in self._pending_order:
                self._pending_order.remove(job_id)
            if job_id not in self._running_order:
                self._running_order.append(job_id)
            self._render()

    def apply_progress(self, job_id: str, p: FileProgress) -> None:
        with self._lock:
            line = self._lines[job_id]
            # translate_from_to emits total/done based on jobs; trust it for real-time display
            if p.total >= 0:
                line.todo = p.total
            if p.done >= 0:
                line.done = p.done
            line.message = p.message

            if p.stage == "start":
                line.stage = "running"
                line.started_at = line.started_at or _now()
                if job_id in self._pending_order:
                    self._pending_order.remove(job_id)
                if job_id not in self._running_order:
                    self._running_order.append(job_id)

            elif p.stage == "done":
                line.stage = "done"
                line.finished_at = _now()
                # move to done top
                if job_id in self._running_order:
                    self._running_order.remove(job_id)
                if job_id in self._pending_order:
                    self._pending_order.remove(job_id)
                if job_id in self._done_order:
                    self._done_order.remove(job_id)
                self._done_order.insert(0, job_id)

            elif p.stage == "error":
                line.stage = "error"
                line.error = p.message or "error"
                line.finished_at = _now()
                if job_id in self._running_order:
                    self._running_order.remove(job_id)
                if job_id in self._pending_order:
                    self._pending_order.remove(job_id)
                if job_id in self._done_order:
                    self._done_order.remove(job_id)
                self._done_order.insert(0, job_id)

            self._render()

    def finalize(self, results: List[JobResult]) -> None:
        with self._lock:
            if self._enabled:
                self._render(force=True)
                self._show_cursor()
            # Always print a final summary (TTY or not)
            ok = sum(1 for r in results if r.ok)
            fail = len(results) - ok
            total_elapsed = 0.0
            if results:
                start = min(r.started_at for r in results)
                end = max(r.finished_at for r in results)
                total_elapsed = max(0.0, end - start)
            self.stream.write("\n")
            self.stream.write(f"[translate_pool] done: ok={ok} fail={fail} total_elapsed={_fmt_hms(total_elapsed)}\n")
            for r in results:
                status = "OK" if r.ok else "FAIL"
                name = r.job.name or _basename(r.job.target_file_path)
                msg = f"{status:<4} {name} ({r.job.tgt_locale}) {r.translated}/{r.todo} elapsed={_fmt_hms(r.elapsed_s)}"
                if r.error:
                    msg += f" error={r.error}"
                self.stream.write(msg + "\n")
            self.stream.flush()

    def _hide_cursor(self) -> None:
        if self._cursor_hidden or not self._enabled:
            return
        self.stream.write(_Ansi.hide_cursor())
        self.stream.flush()
        self._cursor_hidden = True

    def _show_cursor(self) -> None:
        if (not self._cursor_hidden) or (not self._enabled):
            return
        self.stream.write(_Ansi.show_cursor())
        self.stream.flush()
        self._cursor_hidden = False

    def _render(self, force: bool = False) -> None:
        if not self._enabled:
            return

        now = _now()
        if (not force) and (now - self._last_render_at) < self.refresh_min_interval_s:
            return
        self._last_render_at = now

        started_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self._started_at))
        elapsed = _fmt_hms(now - self._started_at)
        total_files = len(self._lines)
        done_files = sum(1 for lid in self._done_order if self._lines[lid].stage in ("done", "error"))
        running_files = len(self._running_order)
        pending_files = len(self._pending_order)

        out: List[str] = []
        out.append(f"workers={self.total_workers} | files={total_files} | done={done_files} running={running_files} pending={pending_files} | start={started_str} | elapsed={elapsed}")
        out.append("")
        out.append("DONE (completed first)")
        if self._done_order:
            for lid in self._done_order[: max(1, min(12, len(self._done_order)))]:
                line = self._lines[lid]
                status = "OK " if line.stage == "done" else "ERR"
                el = _fmt_hms(line.elapsed_s(now))
                out.append(f"  {status} {line.name:<18} | {line.tgt_locale:<16} | {line.done:>5}/{line.todo:<5} | {el}")
        else:
            out.append("  (none)")
        out.append("")
        out.append("RUNNING (1 line per worker, auto-refresh)")
        if self._running_order:
            for lid in self._running_order:
                line = self._lines[lid]
                el = _fmt_hms(line.elapsed_s(now))
                out.append(f"  loading  {line.name:<18} | {line.tgt_locale:<16} | {line.done:>5}/{line.todo:<5} | {el}")
        else:
            out.append("  (none)")
        out.append("")
        out.append("PENDING (queue)")
        if self._pending_order:
            brief = self._pending_order[: self.pending_brief_lines]
            for lid in brief:
                line = self._lines[lid]
                out.append(f"  wait     {line.name:<18} | {line.tgt_locale:<16} | todo={line.todo}")
            remain = len(self._pending_order) - len(brief)
            if remain > 0:
                out.append(f"  ... and {remain} more pending")
        else:
            out.append("  (none)")

        self.stream.write(_Ansi.home() + _Ansi.clear_screen())
        self.stream.write("\n".join(out) + "\n")
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
        pending_brief_lines: int = 3,
        fail_fast: bool = False,
) -> PoolResult:
    """
    Translate many files concurrently (file-level parallelism).
    Within each file, translation remains serial (chunked) inside translate_from_to.

    Args:
        jobs: list of TranslateJob
        api_key: OpenAI API key (or env OPENAI_API_KEY)
        model: model enum or string
        max_workers: maximum concurrent file translations
        pending_brief_lines: how many pending items to show in panel
        fail_fast: if True, stop scheduling new jobs after first failure (running tasks still finish)

    Returns:
        PoolResult containing per-job results.
    """
    if not jobs:
        return PoolResult(results=[])

    max_workers = max(1, int(max_workers))
    max_workers = min(max_workers, len(jobs))

    # Precompute todo counts (used for initial panel + pending list)
    planned: List[Tuple[str, TranslateJob, int]] = []
    for idx, j in enumerate(jobs):
        job_id = f"job{idx+1}"
        todo = _count_incremental_todo(j.source_file_path, j.target_file_path)
        planned.append((job_id, j, todo))

    panel = _ProgressPanel(total_workers=max_workers, pending_brief_lines=pending_brief_lines)
    panel.init_jobs(planned)

    # ThreadPoolExecutor import locally to keep module import light
    from concurrent.futures import ThreadPoolExecutor, Future, wait, FIRST_COMPLETED

    results: Dict[str, JobResult] = {}
    lock = threading.Lock()

    # Validate: avoid same target file being written by multiple workers (danger!)
    seen_targets: Dict[str, str] = {}
    for job_id, job, _ in planned:
        tgt = os.path.abspath(job.target_file_path)
        if tgt in seen_targets:
            raise ValueError(f"duplicate target_file_path detected: {job.target_file_path} (jobs: {seen_targets[tgt]}, {job_id})")
        seen_targets[tgt] = job_id

    pending_ids = [job_id for job_id, _, _ in planned]
    todo_by_id = {job_id: todo for job_id, _, todo in planned}
    job_by_id = {job_id: job for job_id, job, _ in planned}

    def make_progress_cb(job_id: str) -> ProgressCallback:
        def _cb(p: FileProgress) -> None:
            panel.apply_progress(job_id, p)
        return _cb

    def run_one(job_id: str) -> None:
        job = job_by_id[job_id]
        started = _now()
        translated = 0
        err: Optional[str] = None
        ok = True

        # Mark running ASAP (so it appears in "running list" immediately)
        panel.mark_running(job_id)

        try:
            def cb(p: FileProgress) -> None:
                nonlocal translated
                # keep a best-effort translated count
                if p.stage in ("progress", "done"):
                    translated = max(translated, int(p.done))
                panel.apply_progress(job_id, p)

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
            # translate_from_to already emitted error progress; ensure translated stays
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

    # Scheduling with "dynamic refill": keep at most max_workers running.
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
        # Fill initial workers
        for _ in range(max_workers):
            submit_next(ex)

        while in_flight:
            done_set, _ = wait(list(in_flight.keys()), return_when=FIRST_COMPLETED)
            for fut in done_set:
                jid = in_flight.pop(fut)
                # Bubble exception if any (it should already be captured in JobResult)
                try:
                    fut.result()
                except Exception:
                    # should not happen because run_one catches; still guard.
                    pass

                # If fail_fast and this job failed => stop scheduling new ones
                if fail_fast:
                    r = results.get(jid)
                    if r is not None and (not r.ok):
                        stop_scheduling = True

                # Refill one slot
                submit_next(ex)

            # If fail_fast triggered: drain in-flight (no new submits)
            if stop_scheduling and pending_ids:
                pending_ids.clear()

    # Preserve original job order in final summary
    ordered_results: List[JobResult] = []
    for job_id, job, _ in planned:
        r = results.get(job_id)
        if r is None:
            # shouldn't happen; create a placeholder failure
            now = _now()
            ordered_results.append(JobResult(job=job, ok=False, todo=todo_by_id.get(job_id, 0), translated=0, started_at=now, finished_at=now, error="not executed"))
        else:
            ordered_results.append(r)

    panel.finalize(ordered_results)
    return PoolResult(results=ordered_results)
