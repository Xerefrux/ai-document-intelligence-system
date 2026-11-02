"""Uploads, processing status, extracted text, page images, cancel, delete and reprocess."""

from __future__ import annotations

import logging
from typing import Literal

from fastapi import APIRouter, File, Path as PathParam, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select, update

from ...db.engine import session_scope
from ...db.models import Correction, Document, Job, Page, ProcessingEvent, Record, ValidationIssue, new_id, utcnow
from ...jobs.queue import add_event, cancel_document_jobs, enqueue
from ...jobs.records import refresh_document_review
from ...pipeline.types import DocIntelError
from ..deps import factory_of, get_document, settings_of
from ..errors import ApiError
from ..preview import preview_png
from ..serialize import doc_summary, event_dict, issue_counts, issue_dict, iso, record_summary
from ..uploads import ingest_stream, media_type

log = logging.getLogger(__name__)
router = APIRouter(prefix="/documents", tags=["documents"])

# Failures that reprocessing cannot fix: the file itself is the problem.
PERMANENT_ERRORS = {"CORRUPT_FILE", "PASSWORD_PROTECTED", "UNSUPPORTED_TYPE", "EMPTY_FILE"}


class ReprocessIn(BaseModel):
    full: bool = Field(False, description="Read every page again. By default pages that were read successfully are kept and only the failed ones, extraction and validation are redone.")
    confirm: bool = Field(False, description="Required when the document has manual corrections, because reprocessing replaces its records.")


class BulkReprocessIn(BaseModel):
    ids: list[str] | None = Field(None, max_length=1000, description="Limit to these documents. Default: every failed document.")
    include_permanent: bool = Field(False, description="Also retry documents that failed because the file itself is damaged, locked or unsupported.")


def _escape_like(v: str) -> str:
    return v.lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


@router.post("", status_code=202, summary="Upload one or more files")
def upload_documents(request: Request, files: list[UploadFile] = File(..., description="PDF, JPG, PNG, TIFF, XLSX, XLS or CSV"), force: bool = Query(False, description="Store the file even if an identical one is already stored")):
    settings, factory = settings_of(request), factory_of(request)
    if len(files) > settings.max_files_per_request:
        raise ApiError(413, "TOO_MANY_FILES", f"At most {settings.max_files_per_request} files per request. Send the rest in another request.")
    batch = new_id()
    items = [ingest_stream(settings, factory, f.file, f.filename, batch_id=batch, force=force) for f in files]
    counts = {k: sum(1 for i in items if i["status"] == k) for k in ("queued", "duplicate", "rejected")}
    body = {"batch_id": batch, **counts, "items": items}
    return JSONResponse(body, status_code=202 if counts["queued"] else 200)


@router.get("", summary="List documents")
def list_documents(
    request: Request,
    status: Literal["queued", "processing", "completed", "failed", "cancelled"] | None = None,
    review_status: Literal["pending", "ok", "needs_review"] | None = None,
    file_type: Literal["pdf", "image", "xlsx", "xls", "csv"] | None = None,
    q: str | None = Query(None, max_length=200, description="File name contains"),
    batch_id: str | None = Query(None, max_length=32),
    sort: Literal["newest", "oldest", "name", "size"] = "newest",
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    conds = []
    if status:
        conds.append(Document.status == status)
    if review_status:
        conds.append(Document.review_status == review_status)
    if file_type:
        conds.append(Document.file_type == file_type)
    if batch_id:
        conds.append(Document.batch_id == batch_id)
    if q:
        conds.append(func.lower(Document.original_filename).like(f"%{_escape_like(q)}%", escape="\\"))
    order = {"newest": (Document.created_at.desc(), Document.id), "oldest": (Document.created_at, Document.id), "name": (func.lower(Document.original_filename), Document.id), "size": (Document.size_bytes.desc(), Document.id)}[sort]
    with session_scope(factory_of(request)) as s:
        total = s.scalar(select(func.count()).select_from(Document).where(*conds)) or 0
        rows = s.scalars(select(Document).where(*conds).order_by(*order).limit(limit).offset(offset)).all()
        counts = issue_counts(s, [d.id for d in rows])
        by_status = {k: 0 for k in ("queued", "processing", "completed", "failed", "cancelled")}
        by_status.update({k: v for k, v in s.execute(select(Document.status, func.count()).group_by(Document.status)).all()})
        needs_review = s.scalar(select(func.count()).select_from(Document).where(Document.review_status == "needs_review")) or 0
        items = [doc_summary(d, counts.get(d.id)) for d in rows]
    return {"items": items, "total": total, "limit": limit, "offset": offset, "status_counts": by_status, "needs_review": needs_review}


@router.post("/reprocess-failed", summary="Queue every failed document again")
def reprocess_failed(request: Request, body: BulkReprocessIn | None = None):
    body = body or BulkReprocessIn()
    settings = settings_of(request)
    requeued: list[str] = []
    skipped: list[dict] = []
    with session_scope(factory_of(request)) as s:
        q = select(Document).where(Document.status == "failed")
        if body.ids is not None:
            q = q.where(Document.id.in_(body.ids))
        for d in s.scalars(q.order_by(Document.created_at)).all():
            if d.error_code in PERMANENT_ERRORS and not body.include_permanent:
                skipped.append({"id": d.id, "filename": d.original_filename, "code": d.error_code, "reason": "The file itself is damaged, locked or unsupported, so retrying would fail the same way."})
                continue
            if not (settings.uploads_dir / d.stored_name).exists():
                skipped.append({"id": d.id, "filename": d.original_filename, "code": "FILE_MISSING", "reason": "The stored file is gone."})
                continue
            enqueue(s, d.id, settings, kind="reprocess", resume=True)
            requeued.append(d.id)
    return {"requeued": len(requeued), "ids": requeued, "skipped": skipped}


@router.get("/{doc_id}", summary="One document with its records, issues and processing history")
def get_document_detail(request: Request, doc_id: str):
    with session_scope(factory_of(request)) as s:
        d = get_document(s, doc_id)
        job = s.scalars(select(Job).where(Job.document_id == d.id).order_by(Job.created_at.desc()).limit(1)).first()
        counts = issue_counts(s, [d.id]).get(d.id)
        recs = s.scalars(select(Record).where(Record.document_id == d.id).order_by(Record.record_index).limit(100)).all()
        records_total = s.scalar(select(func.count()).select_from(Record).where(Record.document_id == d.id)) or 0
        sev = {"error": 0, "warning": 1, "info": 2}
        issues = sorted(s.scalars(select(ValidationIssue).where(ValidationIssue.document_id == d.id).order_by(ValidationIssue.id).limit(500)).all(), key=lambda i: (sev.get(i.severity, 3), i.id))
        events = list(reversed(s.scalars(select(ProcessingEvent).where(ProcessingEvent.document_id == d.id).order_by(ProcessingEvent.id.desc()).limit(100)).all()))
        pages = s.execute(select(Page.page_no, Page.source, Page.char_count, Page.ocr_confidence, Page.label, Page.warnings).where(Page.document_id == d.id).order_by(Page.page_no).limit(300)).all()
        corrections = s.scalar(select(func.count()).select_from(Correction).join(Record, Record.id == Correction.record_id).where(Record.document_id == d.id)) or 0
        out = doc_summary(d, counts, job)
        out.update({
            "records": [record_summary(r) for r in recs], "records_total": records_total,
            "issues": [issue_dict(i) for i in issues], "events": [event_dict(e) for e in events],
            "pages": [{"page_no": p[0], "source": p[1], "char_count": p[2], "ocr_confidence": p[3], "label": p[4], "warnings": p[5] or []} for p in pages],
            "corrections": corrections,
        })
    return out


@router.get("/{doc_id}/text", summary="Extracted text, a window of pages at a time")
def get_document_text(request: Request, doc_id: str, start: int = Query(1, ge=1, alias="from"), to: int | None = Query(None, ge=1)):
    end = min(to if to is not None else start + 9, start + 49)
    with session_scope(factory_of(request)) as s:
        d = get_document(s, doc_id)
        rows = s.scalars(select(Page).where(Page.document_id == d.id, Page.page_no >= start, Page.page_no <= end).order_by(Page.page_no)).all()
        return {
            "document_id": d.id, "from": start, "to": end, "page_count": d.page_count,
            "pages": [{"page_no": p.page_no, "source": p.source, "label": p.label, "ocr_confidence": p.ocr_confidence, "warnings": p.warnings or [], "text": p.text} for p in rows],
        }


@router.get("/{doc_id}/file", summary="Download the original file")
def download_file(request: Request, doc_id: str, inline: bool = Query(False, description="Show PDFs and images in the browser instead of downloading")):
    settings = settings_of(request)
    with session_scope(factory_of(request)) as s:
        d = get_document(s, doc_id)
        name, stored, ftype = d.original_filename, d.stored_name, d.file_type
    path = settings.uploads_dir / stored
    if not path.exists():
        raise ApiError(410, "FILE_MISSING", "The stored file is no longer on the server.")
    show = inline and ftype in ("pdf", "image")
    return FileResponse(path, media_type=media_type(ftype, stored), filename=name, content_disposition_type="inline" if show else "attachment")


@router.get("/{doc_id}/pages/{page_no}/image", summary="PNG picture of one page")
def page_image(request: Request, doc_id: str, page_no: int = PathParam(..., ge=1, le=100000)):
    settings = settings_of(request)
    with session_scope(factory_of(request)) as s:
        d = get_document(s, doc_id)
        ftype, stored, pages = d.file_type, d.stored_name, d.page_count
    if ftype not in ("pdf", "image"):
        raise ApiError(404, "NO_PREVIEW", "Spreadsheets have no page image. Use the extracted text.")
    if pages and page_no > pages:
        raise ApiError(404, "NOT_FOUND", f"The document has {pages} page(s).")
    path = settings.uploads_dir / stored
    if not path.exists():
        raise ApiError(410, "FILE_MISSING", "The stored file is no longer on the server.")
    try:
        data = preview_png(settings, doc_id, path, ftype, page_no)
    except DocIntelError as exc:
        raise ApiError(422, exc.code, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        log.warning("preview of %s page %s failed: %s", doc_id, page_no, exc)
        raise ApiError(422, "PREVIEW_FAILED", "This page could not be drawn.") from exc
    return Response(data, media_type="image/png", headers={"Cache-Control": "private, max-age=3600"})


@router.post("/{doc_id}/reprocess", summary="Process a document again (failed jobs, or after a settings change)")
def reprocess(request: Request, doc_id: str, body: ReprocessIn | None = None):
    body = body or ReprocessIn()
    settings = settings_of(request)
    with session_scope(factory_of(request)) as s:
        d = get_document(s, doc_id)
        if d.status == "processing":
            raise ApiError(409, "ALREADY_PROCESSING", "This document is being processed right now. Cancel it first if you want to start over.")
        if not (settings.uploads_dir / d.stored_name).exists():
            raise ApiError(410, "FILE_MISSING", "The stored file is no longer on the server, so it cannot be processed again. Upload it again.")
        edited = s.scalar(select(func.count()).select_from(Correction).join(Record, Record.id == Correction.record_id).where(Record.document_id == d.id)) or 0
        if edited and not body.confirm:
            raise ApiError(409, "CORRECTIONS_WOULD_BE_LOST", f"This document has {edited} manual correction(s). Reprocessing replaces its records and discards them. Send confirm=true to go ahead.", {"corrections": edited})
        job = enqueue(s, d.id, settings, kind="reprocess", resume=not body.full)
        if edited:
            add_event(s, d.id, "reprocess", f"Reprocessing started by a person; {edited} manual correction(s) were discarded.", level="warning", job_id=job.id)
        out = doc_summary(d, None, job)
    return out


@router.post("/{doc_id}/cancel", summary="Stop a queued or running document")
def cancel(request: Request, doc_id: str):
    with session_scope(factory_of(request)) as s:
        d = get_document(s, doc_id)
        if d.status not in ("queued", "processing"):
            raise ApiError(409, "NOT_ACTIVE", f"This document is {d.status}; there is nothing to cancel.")
        cancel_document_jobs(s, d.id)
        d.status, d.stage = "cancelled", "cancelled"
        add_event(s, d.id, "cancelled", "Cancelled by a person.", level="warning")
        refresh_document_review(s, d)
        return doc_summary(d)


@router.delete("/{doc_id}", status_code=204, summary="Delete a document, its records and its stored file")
def delete_document(request: Request, doc_id: str):
    settings = settings_of(request)
    with session_scope(factory_of(request)) as s:
        d = get_document(s, doc_id)
        if d.status == "processing":
            raise ApiError(409, "ALREADY_PROCESSING", "This document is being processed. Cancel it first, then delete it.")
        stored = d.stored_name
        s.delete(d)
    (settings.uploads_dir / stored).unlink(missing_ok=True)
    for p in settings.previews_dir.glob(f"{doc_id}-*.png"):
        p.unlink(missing_ok=True)
    return Response(status_code=204)
