"""The job queue: a table in the same database.

A worker takes a job with one UPDATE ... RETURNING statement, so two workers can never get the same
job. SQLite runs it as written; PostgreSQL gets FOR UPDATE SKIP LOCKED (written for PostgreSQL, but
only SQLite is tested in this project). Failed attempts wait longer each time (exponential backoff),
and a job whose worker stopped sending heartbeats is put back in the queue.
"""

from __future__ import annotations

import logging
import os
import socket
from datetime import datetime, timedelta

from sqlalchemy import func, select, text, update
from sqlalchemy.orm import Session, sessionmaker

from ..config import Settings
from ..db.engine import session_scope
from ..db.models import Document, Job, ProcessingEvent, utcnow

log = logging.getLogger(__name__)


def add_event(session: Session, document_id: str, stage: str, message: str, *, level: str = "info", job_id: str | None = None, duration_ms: int | None = None, data: dict | None = None) -> None:
    session.add(ProcessingEvent(document_id=document_id, job_id=job_id, stage=stage, level=level, message=message, duration_ms=duration_ms, data=data))


def enqueue(session: Session, document_id: str, settings: Settings, *, kind: str = "process", resume: bool = True, priority: int = 0) -> Job:
    """Queue a document. Any job still waiting for the same document is cancelled first, so there is one live job per document."""
    session.execute(update(Job).where(Job.document_id == document_id, Job.status == "queued").values(status="cancelled", finished_at=utcnow()))
    job = Job(document_id=document_id, kind=kind, resume=resume, priority=priority, max_attempts=settings.job_max_attempts, status="queued", run_after=utcnow())
    session.add(job)
    doc = session.get(Document, document_id)
    if doc is not None:
        doc.status = "queued"
        doc.stage = "queued"
        doc.error_code = None
        doc.error_message = None
        doc.completed_at = None
    add_event(session, document_id, "queued", "Queued for processing." if kind == "process" else "Queued for reprocessing.", job_id=job.id)
    session.flush()
    return job


_CLAIM_SQLITE = """
UPDATE jobs SET status = 'running', locked_by = :worker, heartbeat_at = :now, started_at = COALESCE(started_at, :now),
       attempts = attempts + 1, stage = 'starting'
WHERE id = (SELECT id FROM jobs WHERE status = 'queued' AND run_after <= :now ORDER BY priority, created_at LIMIT 1)
  AND status = 'queued'
RETURNING id
"""
_CLAIM_POSTGRES = _CLAIM_SQLITE.replace("LIMIT 1)", "LIMIT 1 FOR UPDATE SKIP LOCKED)")


def claim_next(factory: sessionmaker[Session], worker_id: str) -> str | None:
    """Atomically take the oldest runnable job. Returns its id, or None when the queue is empty."""
    with session_scope(factory) as s:
        sql = _CLAIM_POSTGRES if s.get_bind().dialect.name == "postgresql" else _CLAIM_SQLITE
        row = s.execute(text(sql), {"worker": worker_id, "now": utcnow()}).first()
        return row[0] if row else None


def heartbeat(factory: sessionmaker[Session], job_id: str) -> None:
    with session_scope(factory) as s:
        s.execute(update(Job).where(Job.id == job_id, Job.status == "running").values(heartbeat_at=utcnow()))


def backoff_delay(settings: Settings, attempts: int) -> timedelta:
    return timedelta(seconds=settings.job_backoff_seconds * (2 ** max(0, attempts - 1)))


def finish_ok(session: Session, job: Job) -> None:
    job.status = "succeeded"
    job.finished_at = utcnow()
    job.stage = "done"
    job.error_code = None
    job.error_message = None


def finish_failed(session: Session, job: Job, doc: Document | None, settings: Settings, *, code: str, message: str, retryable: bool) -> str:
    """Retry later if the error can heal and attempts remain, otherwise fail for good. Returns 'retry' or 'failed'."""
    if retryable and job.attempts < job.max_attempts:
        delay = backoff_delay(settings, job.attempts)
        job.status = "queued"
        job.run_after = utcnow() + delay
        job.locked_by = None
        job.error_code, job.error_message = code, message
        if doc is not None:
            doc.status = "queued"
            doc.stage = "waiting to retry"
            add_event(session, doc.id, "retry", f"{message} Retrying in {int(delay.total_seconds())} s (attempt {job.attempts} of {job.max_attempts}).", level="warning", job_id=job.id, data={"code": code})
        return "retry"
    job.status = "failed"
    job.finished_at = utcnow()
    job.error_code, job.error_message = code, message
    if doc is not None:
        doc.status = "failed"
        doc.stage = "failed"
        doc.error_code, doc.error_message = code, message
        doc.completed_at = utcnow()
        add_event(session, doc.id, "failed", message, level="error", job_id=job.id, data={"code": code})
    return "failed"


def recover_stale(factory: sessionmaker[Session], settings: Settings) -> int:
    """Put jobs back in the queue when their worker has gone quiet (crash, kill, machine restart)."""
    cutoff = utcnow() - timedelta(seconds=settings.job_stale_seconds)
    n = 0
    with session_scope(factory) as s:
        stale = s.scalars(select(Job).where(Job.status == "running", Job.heartbeat_at < cutoff)).all()
        for job in stale:
            doc = s.get(Document, job.document_id)
            log.warning("Job %s lost its worker %s; recovering", job.id, job.locked_by)
            finish_failed(s, job, doc, settings, code="WORKER_LOST", message="The worker stopped while processing this document.", retryable=True)
            if job.status == "queued":
                job.run_after = utcnow()  # no point in waiting after a crash
            n += 1
    return n


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def recover_dead_local(factory: sessionmaker[Session], settings: Settings) -> int:
    """At startup: running jobs that belonged to a process on this machine that no longer exists go back in the queue at once.

    Worker names are `<host>-<pid>-<suffix>`. Call this before this process starts its own workers.
    Jobs of workers on other machines are left to the heartbeat check in recover_stale.
    """
    host = socket.gethostname()
    n = 0
    with session_scope(factory) as s:
        for job in s.scalars(select(Job).where(Job.status == "running")).all():
            parts = (job.locked_by or "").rsplit("-", 2)
            if len(parts) != 3 or parts[0] != host or not parts[1].isdigit():
                continue
            pid = int(parts[1])
            if pid != os.getpid() and _pid_alive(pid):
                continue
            doc = s.get(Document, job.document_id)
            log.warning("Job %s was running in process %s, which is gone; requeueing", job.id, pid)
            finish_failed(s, job, doc, settings, code="WORKER_LOST", message="The server stopped while processing this document.", retryable=True)
            if job.status == "queued":
                job.run_after = utcnow()
            n += 1
    return n


def cancel_document_jobs(session: Session, document_id: str) -> None:
    session.execute(update(Job).where(Job.document_id == document_id, Job.status.in_(("queued",))).values(status="cancelled", finished_at=utcnow()))


def queue_stats(session: Session) -> dict:
    rows = session.execute(select(Job.status, func.count()).group_by(Job.status)).all()
    out = {s: 0 for s in ("queued", "running", "succeeded", "failed", "cancelled")}
    out.update({k: v for k, v in rows})
    oldest = session.scalar(select(func.min(Job.created_at)).where(Job.status == "queued"))
    out["oldest_queued_seconds"] = int((utcnow() - oldest).total_seconds()) if oldest else 0
    return out
