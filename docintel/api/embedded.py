"""Background workers that live inside the API process (DOCINTEL_WORKERS=N).

They share one OCR process pool and one AI client, so N workers do not start N copies of either.
For more throughput, or to keep heavy work off the web process, run `docintel worker` separately
and set DOCINTEL_WORKERS=0 on the API.
"""

from __future__ import annotations

import logging
import os
import socket
import threading

from sqlalchemy.orm import Session, sessionmaker

from ..config import Settings
from ..extraction.llm import LLMExtractor
from ..jobs.queue import recover_dead_local
from ..jobs.worker import LazyOcrPool, Worker

log = logging.getLogger(__name__)


class _SharedPool(LazyOcrPool):
    """Workers call close() when they stop; the real pool is closed once, by the owner."""

    def close(self, wait: bool = True) -> None:  # noqa: ARG002
        return

    def close_for_real(self, wait: bool = True) -> None:
        super().close(wait=wait)


class EmbeddedWorkers:
    def __init__(self, settings: Settings, factory: sessionmaker[Session]):
        self.settings, self.factory = settings, factory
        self.workers: list[Worker] = []
        self.threads: list[threading.Thread] = []
        self.pool = _SharedPool(settings)
        self.llm = LLMExtractor(settings) if settings.llm_enabled else None

    def start(self) -> None:
        try:
            n = recover_dead_local(self.factory, self.settings)
            if n:
                log.warning("Put %d interrupted job(s) back in the queue", n)
        except Exception:  # noqa: BLE001
            log.exception("could not recover interrupted jobs")
        host, pid = socket.gethostname(), os.getpid()
        for i in range(self.settings.workers):
            w = Worker(self.settings, self.factory, name=f"{host}-{pid}-w{i + 1}", llm=self.llm, pool=self.pool)
            t = threading.Thread(target=w.run_forever, name=f"docintel-worker-{i + 1}", daemon=True)
            self.workers.append(w)
            self.threads.append(t)
            t.start()
        if self.workers:
            log.info("Started %d background worker(s)", len(self.workers))

    def stop(self, timeout: float = 25.0) -> None:
        for w in self.workers:
            w.stop.set()
        for t in self.threads:
            t.join(timeout)
        self.pool.close_for_real(wait=False)
        client = getattr(self.llm, "client", None)
        if client is not None:
            try:
                client.close()
            except Exception:  # noqa: BLE001
                pass
        self.workers, self.threads = [], []

    @property
    def alive(self) -> int:
        return sum(1 for t in self.threads if t.is_alive())
