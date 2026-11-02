"""Process one document end to end and save the results, page by page.

Stages: reading (text layer, OCR or spreadsheet), extracting, validating, saving, indexing.
Each page is saved as soon as it is read, so a crash or restart resumes after the last stored
page instead of starting over. Results are replaced in one transaction, so a reader never sees
half of a document's records.
"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Callable

from sqlalchemy import delete, select
from sqlalchemy.orm import Session, sessionmaker

from ..config import Settings
from ..db.engine import session_scope
from ..db.models import Document, Job, LineItem, Page, Record, ValidationIssue, utcnow
from ..extraction.document import extract_document
from ..extraction.llm import LLMExtractor
from ..pipeline import open_reader
from ..pipeline.ocr import resolve_engine_name
from ..pipeline.process import Cancelled, attach_ocr_confidence, read_pages, record_data, source_kind, to_page_texts
from ..pipeline.types import DocIntelError, PageContent
from ..search.index import index_document
from ..validation.duplicates import find_duplicates
from ..validation.rules import Issue, validate_record
from . import queue as Q
from .records import create_record, refresh_document_review, replace_issues, set_record_status

log = logging.getLogger(__name__)


class JobContext:
    """What a running job needs: settings, a session factory, the OCR pool and the LLM client."""

    def __init__(self, settings: Settings, factory: sessionmaker[Session], pool, llm: LLMExtractor | None, stop: threading.Event | None = None):
        self.settings, self.factory, self.pool, self.llm = settings, factory, pool, llm
        self.stop = stop or threading.Event()


def _page_row(doc_id: str, pc: PageContent) -> Page:
    return Page(
        document_id=doc_id, page_no=pc.page_no, label=pc.label, source=pc.source, text=pc.text, char_count=len(pc.text), ocr_confidence=pc.ocr_confidence,
        width=pc.width, height=pc.height, warnings=pc.warnings or None,
    )


def _set_stage(ctx: JobContext, job_id: str, doc_id: str, stage: str, **doc_fields) -> None:
    with session_scope(ctx.factory) as s:
        job = s.get(Job, job_id)
        doc = s.get(Document, doc_id)
        if job:
            job.stage = stage
            job.heartbeat_at = utcnow()
        if doc:
            doc.stage = stage
            for k, v in doc_fields.items():
                setattr(doc, k, v)


def process_job(ctx: JobContext, job_id: str) -> str:
    """Run a claimed job. Returns 'done', 'failed', 'retry' or 'cancelled'. Never raises."""
    s = ctx.settings
    t_start = time.perf_counter()
    with session_scope(ctx.factory) as session:
        job = session.get(Job, job_id)
        doc = session.get(Document, job.document_id) if job else None
        if job is None or doc is None:
            return "failed"
        doc_id, resume, attempt = doc.id, job.resume, job.attempts
        stored = Path(s.uploads_dir) / doc.stored_name
        file_type = doc.file_type
        doc.status, doc.stage, doc.error_code, doc.error_message = "processing", "reading", None, None
        Q.add_event(session, doc_id, "start", f"Processing started (attempt {attempt}).", job_id=job_id)
        if not resume:
            session.execute(delete(Page).where(Page.document_id == doc_id))
            session.execute(delete(Record).where(Record.document_id == doc_id))
            session.execute(delete(ValidationIssue).where(ValidationIssue.document_id == doc_id))
            doc.pages_done = 0
        done_pages = {p for (p,) in session.execute(select(Page.page_no).where(Page.document_id == doc_id, Page.source != "failed")).all()} if resume else set()

    deadline = t_start + s.document_timeout_seconds
    last_check = [0.0, False]

    def should_stop() -> bool:
        if ctx.stop.is_set():
            return True
        now = time.monotonic()
        if time.perf_counter() > deadline:
            raise DocIntelError(f"Processing took longer than {s.document_timeout_seconds} seconds.", code="TIMEOUT")
        if now - last_check[0] > 2.0:
            last_check[0] = now
            with session_scope(ctx.factory) as ss:
                d = ss.get(Document, doc_id)
                last_check[1] = d is None or d.status == "cancelled"
        return bool(last_check[1])

    try:
        reader = open_reader(str(stored), file_type, s)
    except DocIntelError as exc:
        return _fail(ctx, job_id, doc_id, exc.code, str(exc), exc.retryable)
    except Exception as exc:  # noqa: BLE001
        log.exception("Opening %s failed", doc_id)
        return _fail(ctx, job_id, doc_id, "PROCESSING_ERROR", f"The file could not be opened: {exc}", True)

    try:
        total = reader.page_count
        _set_stage(ctx, job_id, doc_id, "reading", page_count=total)
        with session_scope(ctx.factory) as session:
            job = session.get(Job, job_id)
            job.progress_total, job.progress_done = total, len(done_pages)
            for w in getattr(reader, "warnings", []):
                Q.add_event(session, doc_id, "read", w, level="warning", job_id=job_id)

        buffer: list[PageContent] = []
        counter = [len(done_pages)]

        def flush() -> None:
            if not buffer:
                return
            batch = list(buffer)
            buffer.clear()
            with session_scope(ctx.factory) as ss:
                for pc in batch:
                    ss.execute(delete(Page).where(Page.document_id == doc_id, Page.page_no == pc.page_no))
                    ss.add(_page_row(doc_id, pc))
                counter[0] += len(batch)
                job_ = ss.get(Job, job_id)
                doc_ = ss.get(Document, doc_id)
                job_.progress_done, job_.heartbeat_at = counter[0], utcnow()
                doc_.pages_done = counter[0]
                doc_.updated_at = utcnow()

        def on_page(pc: PageContent) -> None:
            buffer.append(pc)
            if len(buffer) >= s.page_commit_batch:
                flush()

        t_read = time.perf_counter()
        try:
            read_pages(reader, str(stored), file_type, s, ctx.pool, skip=done_pages, on_page=on_page, should_stop=should_stop)
        finally:
            flush()
        read_s = time.perf_counter() - t_read

        if should_stop() and not ctx.stop.is_set():
            return _cancelled(ctx, job_id, doc_id)
        if ctx.stop.is_set():
            return _requeue_for_shutdown(ctx, job_id, doc_id)

        # everything read so far, including pages stored by an earlier attempt
        with session_scope(ctx.factory) as session:
            rows = session.scalars(select(Page).where(Page.document_id == doc_id).order_by(Page.page_no)).all()
            pages = [PageContent(r.page_no, r.source, r.text, r.label, r.width, r.height, r.ocr_confidence, list(r.warnings or [])) for r in rows]
        failed_pages = [p for p in pages if p.source == "failed"]
        if pages and len(failed_pages) == len(pages):
            msg = failed_pages[0].warnings[0] if failed_pages[0].warnings else "No page could be read."
            code = failed_pages[0].meta.get("error_code") or (msg.split(":", 1)[0] if ":" in msg else "PROCESSING_ERROR")
            return _fail(ctx, job_id, doc_id, code, msg, False)

        _set_stage(ctx, job_id, doc_id, "extracting")
        t_ext = time.perf_counter()
        sheets = reader.sheets if file_type in ("xlsx", "xls", "csv") else None
        outcome = extract_document(to_page_texts(pages), sheets, s, ctx.llm)
        attach_ocr_confidence(outcome.records, pages)
        ext_s = time.perf_counter() - t_ext

        _set_stage(ctx, job_id, doc_id, "validating")
        issue_lists = [validate_record(record_data(r), s) for r in outcome.records]

        _set_stage(ctx, job_id, doc_id, "saving")
        confs = [p.ocr_confidence for p in pages if p.ocr_confidence is not None]
        with session_scope(ctx.factory) as session:
            doc = session.get(Document, doc_id)
            job = session.get(Job, job_id)
            session.execute(delete(Record).where(Record.document_id == doc_id))
            session.execute(delete(ValidationIssue).where(ValidationIssue.document_id == doc_id))
            created: list[Record] = []
            for idx, (ext, issues) in enumerate(zip(outcome.records, issue_lists)):
                rec = create_record(session, doc, ext, idx)
                replace_issues(session, rec, issues)
                created.append(rec)
            if not created:
                session.add(ValidationIssue(document_id=doc_id, record_id=None, code="NO_INVOICE_DETECTED", severity="warning", field=None,
                                            message=outcome.notes[0] if outcome.notes else "No invoice or invoice table was found in this document.", details=None))
            doc.page_count = len(pages)
            doc.text_chars = sum(len(p.text) for p in pages)
            doc.source_kind = source_kind(file_type, pages)
            doc.ocr_used = any(p.source == "ocr" for p in pages)
            doc.ocr_engine = resolve_engine_name(s) if doc.ocr_used else None
            doc.ocr_confidence = round(sum(confs) / len(confs), 4) if confs else None
            doc.record_count = len(created)
            for w in outcome.notes:
                Q.add_event(session, doc_id, "extract", w, level="warning", job_id=job_id)
            if failed_pages:
                Q.add_event(session, doc_id, "read", f"{len(failed_pages)} of {len(pages)} pages could not be read: " + "; ".join(p.warnings[0] for p in failed_pages[:3] if p.warnings), level="warning", job_id=job_id)
            if outcome.llm_calls or outcome.llm_failures:
                Q.add_event(session, doc_id, "llm", f"AI extraction: {outcome.llm_calls} call(s), {outcome.llm_failures} failed.", level="warning" if outcome.llm_failures else "info", job_id=job_id)
            session.flush()
            # duplicates are checked after the records exist, against everything already stored
            for rec in created:
                dup = find_duplicates(session, rec, s)
                if dup:
                    for i in dup:
                        session.add(ValidationIssue(document_id=doc_id, record_id=rec.id, code=i.code, severity=i.severity, field=i.field, message=i.message, details=i.details))
                set_record_status(session, rec)
                if dup:
                    doc.duplicate_of = doc.duplicate_of or (dup[0].details or {}).get("duplicate_of_document")
            doc.status, doc.stage = "completed", "done"
            doc.completed_at = utcnow()
            doc.processing_ms = int((time.perf_counter() - t_start) * 1000)
            refresh_document_review(session, doc)
            Q.finish_ok(session, job)
            Q.add_event(session, doc_id, "done", f"Done: {len(created)} record(s) from {len(pages)} page(s) in {doc.processing_ms / 1000:.1f} s.", job_id=job_id,
                        duration_ms=doc.processing_ms, data={"read_s": round(read_s, 2), "extract_s": round(ext_s, 2), "records": len(created), "pages": len(pages)})
        # Vectors are built after the results are committed, so a slow embedding service never holds the database write lock.
        with session_scope(ctx.factory) as session:
            index_document(session, doc_id, s)
        return "done"
    except Cancelled:
        if ctx.stop.is_set():
            return _requeue_for_shutdown(ctx, job_id, doc_id)
        return _cancelled(ctx, job_id, doc_id)
    except DocIntelError as exc:
        return _fail(ctx, job_id, doc_id, exc.code, str(exc), exc.retryable)
    except Exception as exc:  # noqa: BLE001
        log.exception("Processing %s failed", doc_id)
        return _fail(ctx, job_id, doc_id, "PROCESSING_ERROR", f"Unexpected error: {type(exc).__name__}: {exc}", True)
    finally:
        reader.close()


def _fail(ctx: JobContext, job_id: str, doc_id: str, code: str, message: str, retryable: bool) -> str:
    with session_scope(ctx.factory) as session:
        job = session.get(Job, job_id)
        doc = session.get(Document, doc_id)
        outcome = Q.finish_failed(session, job, doc, ctx.settings, code=code, message=message[:2000], retryable=retryable)
        if doc is not None:
            refresh_document_review(session, doc)
    return outcome


def _cancelled(ctx: JobContext, job_id: str, doc_id: str) -> str:
    with session_scope(ctx.factory) as session:
        job = session.get(Job, job_id)
        doc = session.get(Document, doc_id)
        job.status, job.finished_at = "cancelled", utcnow()
        if doc is not None:
            doc.status, doc.stage = "cancelled", "cancelled"
            Q.add_event(session, doc_id, "cancelled", "Processing was cancelled.", level="warning", job_id=job_id)
    return "cancelled"


def _requeue_for_shutdown(ctx: JobContext, job_id: str, doc_id: str) -> str:
    """The worker is stopping. Put the job back (without using up an attempt) so another worker resumes it."""
    with session_scope(ctx.factory) as session:
        job = session.get(Job, job_id)
        doc = session.get(Document, doc_id)
        job.status, job.locked_by, job.attempts, job.run_after = "queued", None, max(0, job.attempts - 1), utcnow()
        if doc is not None:
            doc.status, doc.stage = "queued", "queued"
            Q.add_event(session, doc_id, "paused", "The worker stopped; processing will resume from the last stored page.", level="warning", job_id=job_id)
    return "retry"


def run_inline(ctx: JobContext, job_id: str) -> str:
    """Convenience for tests and the CLI."""
    return process_job(ctx, job_id)
