"""The worker: takes jobs from the queue and runs them.

Run it inside the API process (DOCINTEL_WORKERS=1, a thread), or as separate processes with
`docintel worker`. Any number of workers can share one database; the queue never hands the same job to two.
"""

from __future__ import annotations

import logging
import os
import socket
import threading
import time
from typing import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from ..config import Settings
from ..db.engine import session_scope
from ..db.models import WorkerState, utcnow
from ..extraction.llm import LLMExtractor
from ..pipeline.ocr import OcrPool
from . import queue as Q
from .runner import JobContext, process_job

log = logging.getLogger(__name__)


class LazyOcrPool:
    """Starts the OCR processes the first time a page needs them, so text-only workloads never pay for them."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self._pool: OcrPool | None = None
        self._lock = threading.Lock()

    @property
    def n(self) -> int:
        return self.settings.ocr_process_count

    def _get(self) -> OcrPool:
        with self._lock:
            if self._pool is None:
                self._pool = OcrPool(self.settings)
            return self._pool

    def submit(self, task):
        return self._get().submit(task)

    def restart(self) -> None:
        with self._lock:
            if self._pool is not None:
                self._pool.restart()

    def close(self, wait: bool = True) -> None:
        with self._lock:
            if self._pool is not None:
                self._pool.close(wait=wait)
                self._pool = None


class Worker:
    def __init__(self, settings: Settings, factory: sessionmaker[Session], *, name: str | None = None, llm: LLMExtractor | None = None, pool=None):
        self.settings, self.factory = settings, factory
        self.worker_id = name or f"{socket.gethostname()}-{os.getpid()}-{threading.get_ident() % 10000}"
        self.stop = threading.Event()
        self.pool = pool or LazyOcrPool(settings)
        self.llm = llm if llm is not None else (LLMExtractor(settings) if settings.llm_enabled else None)
        self.ctx = JobContext(settings, factory, self.pool, self.llm, self.stop)
        self.done = 0
        self.failed = 0
        self._last_recover = 0.0

    # -- bookkeeping

    def _state(self, current: str | None) -> None:
        try:
            with session_scope(self.factory) as s:
                row = s.get(WorkerState, self.worker_id)
                if row is None:
                    row = WorkerState(worker_id=self.worker_id, pid=os.getpid())
                    s.add(row)
                row.heartbeat_at, row.current_job, row.jobs_done, row.jobs_failed = utcnow(), current, self.done, self.failed
        except Exception:  # noqa: BLE001
            log.debug("could not update worker state", exc_info=True)

    def _heartbeat_loop(self, job_id: str, stop: threading.Event) -> None:
        while not stop.wait(self.settings.job_heartbeat_seconds):
            try:
                Q.heartbeat(self.factory, job_id)
                self._state(job_id)
            except Exception:  # noqa: BLE001
                log.debug("heartbeat failed", exc_info=True)

    # -- running

    def run_once(self) -> bool:
        """Claim and run one job. Returns False when there was nothing to do."""
        if time.monotonic() - self._last_recover > max(10.0, self.settings.job_stale_seconds / 4):
            self._last_recover = time.monotonic()
            try:
                n = Q.recover_stale(self.factory, self.settings)
                if n:
                    log.warning("Recovered %d job(s) from stopped workers", n)
            except Exception:  # noqa: BLE001
                log.exception("stale job recovery failed")
        job_id = Q.claim_next(self.factory, self.worker_id)
        if job_id is None:
            return False
        beat_stop = threading.Event()
        beat = threading.Thread(target=self._heartbeat_loop, args=(job_id, beat_stop), daemon=True)
        beat.start()
        self._state(job_id)
        try:
            outcome = process_job(self.ctx, job_id)
        finally:
            beat_stop.set()
        if outcome == "done":
            self.done += 1
        elif outcome == "failed":
            self.failed += 1
        self._state(None)
        log.info("job %s finished: %s", job_id, outcome)
        return True

    def run_forever(self, on_idle: Callable[[], None] | None = None) -> None:
        log.info("worker %s started", self.worker_id)
        self._state(None)
        last_beat = time.monotonic()
        try:
            while not self.stop.is_set():
                try:
                    worked = self.run_once()
                except Exception:  # noqa: BLE001 - one bad job must not stop the worker
                    log.exception("worker loop error")
                    worked = False
                    self.stop.wait(1.0)
                if worked:
                    last_beat = time.monotonic()
                else:
                    # an idle worker still reports that it is alive, otherwise the health check would call it dead
                    if time.monotonic() - last_beat >= self.settings.job_heartbeat_seconds:
                        self._state(None)
                        last_beat = time.monotonic()
                    if on_idle:
                        on_idle()
                    self.stop.wait(self.settings.job_poll_seconds)
        finally:
            self.pool.close(wait=False)
            self.forget()
            log.info("worker %s stopped", self.worker_id)

    def forget(self) -> None:
        """Remove this worker from the list of workers (a clean shutdown, or a one-off batch run that is finished)."""
        try:
            with session_scope(self.factory) as s:
                row = s.get(WorkerState, self.worker_id)
                if row is not None:
                    s.delete(row)
        except Exception:  # noqa: BLE001
            pass

    def drain(self, max_jobs: int = 100000) -> int:
        """Run jobs until the queue is empty. For tests and one-off batch runs."""
        n = 0
        while n < max_jobs and self.run_once():
            n += 1
        return n


def list_workers(factory: sessionmaker[Session], stale_after: float = 60.0) -> list[dict]:
    with session_scope(factory) as s:
        rows = s.scalars(select(WorkerState)).all()
        now = utcnow()
        for r in [r for r in rows if (now - r.heartbeat_at).total_seconds() > 86400]:  # left behind by a process that crashed a day ago
            s.delete(r)
        rows = [r for r in rows if (now - r.heartbeat_at).total_seconds() <= 86400]
        return [
            {"id": r.worker_id, "pid": r.pid, "current_job": r.current_job, "jobs_done": r.jobs_done, "jobs_failed": r.jobs_failed,
             "seconds_since_heartbeat": round((now - r.heartbeat_at).total_seconds(), 1), "alive": (now - r.heartbeat_at).total_seconds() < stale_after}
            for r in rows
        ]
