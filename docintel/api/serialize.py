"""Turning database rows into the JSON the API returns."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db.models import Correction, Document, Job, LineItem, ProcessingEvent, Record, ValidationIssue
from ..extraction.schema import ALL_FIELDS, FieldValue


def iso(v: Any) -> str | None:
    """Dates stay plain (2025-03-25). Timestamps are stored as naive UTC, so they are sent with a Z (2025-03-25T09:30:00Z)."""
    if isinstance(v, datetime):
        return v.replace(microsecond=0).isoformat() + "Z"
    return v.isoformat() if hasattr(v, "isoformat") else (None if v is None else str(v))


def issue_counts(session: Session, doc_ids: list[str]) -> dict[str, dict[str, int]]:
    if not doc_ids:
        return {}
    rows = session.execute(
        select(ValidationIssue.document_id, ValidationIssue.severity, func.count()).where(ValidationIssue.document_id.in_(doc_ids)).group_by(ValidationIssue.document_id, ValidationIssue.severity)
    ).all()
    out: dict[str, dict[str, int]] = {d: {"error": 0, "warning": 0, "info": 0} for d in doc_ids}
    for d, sev, n in rows:
        out[d][sev] = n
    return out


def doc_summary(d: Document, counts: dict[str, int] | None = None, job: Job | None = None) -> dict[str, Any]:
    total = d.page_count or 0
    progress = None
    if d.status == "completed":
        progress = 1.0
    elif total:
        progress = round(min(1.0, (d.pages_done or 0) / total), 4)
    out = {
        "id": d.id, "filename": d.original_filename, "file_type": d.file_type, "size_bytes": d.size_bytes, "status": d.status, "stage": d.stage,
        "page_count": d.page_count, "pages_done": d.pages_done, "progress": progress, "record_count": d.record_count, "review_status": d.review_status,
        "source_kind": d.source_kind, "ocr_used": d.ocr_used, "ocr_engine": d.ocr_engine, "ocr_confidence": d.ocr_confidence,
        "error_code": d.error_code, "error_message": d.error_message, "duplicate_of": d.duplicate_of, "batch_id": d.batch_id,
        "processing_ms": d.processing_ms, "created_at": iso(d.created_at), "completed_at": iso(d.completed_at), "sha256": d.sha256,
        "issues": counts or {"error": 0, "warning": 0, "info": 0},
    }
    if job is not None:
        out["job"] = {"id": job.id, "status": job.status, "attempts": job.attempts, "max_attempts": job.max_attempts, "stage": job.stage, "run_after": iso(job.run_after), "error_code": job.error_code}
    return out


def issue_dict(i: ValidationIssue) -> dict[str, Any]:
    return {"id": i.id, "record_id": i.record_id, "document_id": i.document_id, "code": i.code, "severity": i.severity, "field": i.field, "message": i.message, "details": i.details or {}}


def record_summary(r: Record) -> dict[str, Any]:
    return {
        "id": r.id, "document_id": r.document_id, "record_index": r.record_index, "kind": r.kind, "invoice_number": r.invoice_number, "invoice_date": iso(r.invoice_date),
        "supplier_name": r.supplier_name, "customer_name": r.customer_name, "total": r.total, "currency": r.currency, "review_status": r.review_status, "confidence": r.confidence,
        "page_start": r.page_start, "page_end": r.page_end, "sheet_name": r.sheet_name, "row_ref": r.row_ref, "method": r.method,
    }


def record_detail(session: Session, r: Record) -> dict[str, Any]:
    stored = r.fields_json or {}
    fields = {name: FieldValue.from_json(stored.get(name)).to_json() for name in ALL_FIELDS}
    items = session.scalars(select(LineItem).where(LineItem.record_id == r.id).order_by(LineItem.position)).all()
    issues = session.scalars(select(ValidationIssue).where(ValidationIssue.record_id == r.id).order_by(ValidationIssue.id)).all()
    corrections = session.scalars(select(Correction).where(Correction.record_id == r.id).order_by(Correction.id.desc()).limit(50)).all()
    doc = session.get(Document, r.document_id)
    sev_order = {"error": 0, "warning": 1, "info": 2}
    out = record_summary(r)
    out.update({
        "fields": fields,
        "line_items": [{"id": i.id, "position": i.position, "description": i.description, "quantity": i.quantity, "unit_price": i.unit_price, "amount": i.amount, "confidence": i.confidence} for i in items],
        "issues": sorted((issue_dict(i) for i in issues), key=lambda x: (sev_order.get(x["severity"], 3), x["id"])),
        "corrections": [{"id": c.id, "field": c.field, "old_value": c.old_value, "new_value": c.new_value, "note": c.note, "created_at": iso(c.created_at)} for c in corrections],
        "document": {"id": doc.id, "filename": doc.original_filename, "file_type": doc.file_type, "page_count": doc.page_count, "source_kind": doc.source_kind} if doc else None,
        "version": r.version, "updated_at": iso(r.updated_at),
    })
    return out


def event_dict(e: ProcessingEvent) -> dict[str, Any]:
    return {"id": e.id, "stage": e.stage, "level": e.level, "message": e.message, "duration_ms": e.duration_ms, "data": e.data, "created_at": iso(e.created_at), "job_id": e.job_id}
