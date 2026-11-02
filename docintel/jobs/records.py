"""Saving extraction results as database rows, and turning rows back into something the validator can read."""

from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from ..config import Settings
from ..db.models import Document, LineItem, Record, ValidationIssue, utcnow
from ..extraction.schema import ALL_FIELDS, DATE_FIELDS, MONEY_FIELDS, ExtractedRecord, FieldValue, LineItemValue
from ..search.index import record_search_text
from ..validation.duplicates import find_duplicates, keys_for
from ..validation.rules import Issue, RecordData, review_needed, validate_record


def _as_date(v: Any) -> date | None:
    if isinstance(v, date):
        return v
    if isinstance(v, str):
        try:
            return date.fromisoformat(v[:10])
        except ValueError:
            return None
    return None


def _as_float(v: Any) -> float | None:
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def sync_columns(rec: Record, fields: dict[str, FieldValue]) -> None:
    """Copy the field values into the typed columns the API filters and sorts on."""
    g = lambda n: fields[n].value if n in fields else None  # noqa: E731
    rec.invoice_number = (str(g("invoice_number"))[:128] if g("invoice_number") not in (None, "") else None)
    rec.invoice_date = _as_date(g("invoice_date"))
    rec.due_date = _as_date(g("due_date"))
    rec.supplier_name = (str(g("supplier_name"))[:300] if g("supplier_name") else None)
    rec.customer_name = (str(g("customer_name"))[:300] if g("customer_name") else None)
    rec.subtotal = _as_float(g("subtotal"))
    rec.tax = _as_float(g("tax"))
    rec.total = _as_float(g("total"))
    rec.currency = (str(g("currency"))[:8] if g("currency") else None)
    rec.inv_key, rec.dedupe_key = keys_for(rec.invoice_number, rec.supplier_name)
    rec.fields_json = {k: fv.to_json() for k, fv in fields.items()}


def refresh_search_text(rec: Record, items: list[LineItem | LineItemValue]) -> None:
    vals = {
        "invoice_number": rec.invoice_number, "supplier_name": rec.supplier_name, "customer_name": rec.customer_name,
        "invoice_date": rec.invoice_date.isoformat() if rec.invoice_date else None, "due_date": rec.due_date.isoformat() if rec.due_date else None,
        "currency": rec.currency, "total": rec.total,
    }
    rec.search_text = record_search_text(vals, [{"description": i.description} for i in items])


def create_record(session: Session, doc: Document, ext: ExtractedRecord, index: int) -> Record:
    rec = Record(
        document_id=doc.id, record_index=index, kind=ext.kind, page_start=ext.page_start, page_end=ext.page_end, sheet_name=ext.sheet_name, row_ref=ext.row_ref,
        method=ext.method, confidence=ext.overall_confidence(), review_status="needs_review",
    )
    sync_columns(rec, ext.fields)
    session.add(rec)
    session.flush()
    for pos, li in enumerate(ext.line_items, 1):
        session.add(LineItem(record_id=rec.id, position=pos, description=li.description, quantity=li.quantity, unit_price=li.unit_price, amount=li.amount, confidence=li.confidence))
    refresh_search_text(rec, ext.line_items)
    return rec


def record_to_data(rec: Record, items: list[LineItem], ocr_conf: float | None = None, extra: dict | None = None) -> RecordData:
    """Rebuild what the validator needs from stored rows (used after a manual correction)."""
    stored = rec.fields_json or {}
    fields: dict[str, FieldValue] = {}
    for name in ALL_FIELDS:
        fv = FieldValue.from_json(stored.get(name))
        col = getattr(rec, name, None) if hasattr(rec, name) else None
        if name in DATE_FIELDS and isinstance(col, date):
            col = col.isoformat()
        if fv.value is None and col is not None:
            fv.value = col
        fields[name] = fv
    li = [LineItemValue(i.description, i.quantity, i.unit_price, i.amount, i.confidence or 0.0) for i in items]
    extra = extra or {}
    if any(fv.source == "manual" for fv in fields.values()):
        ocr_conf = None  # a person has looked at this record, a poor scan no longer needs flagging
    return RecordData(kind=rec.kind, fields=fields, line_items=li, method=rec.method, ocr_confidence=ocr_conf, dropped=extra.get("dropped", []), disagreements=extra.get("disagreements", []))


def replace_issues(session: Session, rec: Record, issues: list[Issue], keep_codes: set[str] | None = None) -> None:
    """Replace a record's issues. Issues whose code is in keep_codes (cross document results) survive."""
    q = delete(ValidationIssue).where(ValidationIssue.record_id == rec.id)
    if keep_codes:
        q = q.where(ValidationIssue.code.notin_(keep_codes))
    session.execute(q)
    for i in issues:
        session.add(ValidationIssue(document_id=rec.document_id, record_id=rec.id, code=i.code, severity=i.severity, field=i.field, message=i.message, details=i.details))


def record_issues(session: Session, rec: Record) -> list[Issue]:
    session.flush()  # sessions here do not autoflush; make sure issues added a moment ago are visible
    rows = session.scalars(select(ValidationIssue).where(ValidationIssue.record_id == rec.id)).all()
    return [Issue(r.code, r.severity, r.field, r.message, r.details or {}) for r in rows]


def set_record_status(session: Session, rec: Record, *, human_touched: bool = False) -> None:
    issues = record_issues(session, rec)
    if rec.review_status == "approved":
        return
    if review_needed(issues):
        rec.review_status = "needs_review"
    else:
        rec.review_status = "corrected" if human_touched else "ok"


def refresh_document_review(session: Session, doc: Document) -> None:
    """Document level review state, derived from its records and its own issues."""
    session.flush()
    statuses = [r for (r,) in session.execute(select(Record.review_status).where(Record.document_id == doc.id)).all()]
    doc_issue = session.scalar(select(func.count()).select_from(ValidationIssue).where(ValidationIssue.document_id == doc.id, ValidationIssue.record_id.is_(None), ValidationIssue.severity.in_(("error", "warning"))))
    doc.record_count = len(statuses)
    if doc.status in ("queued", "processing"):
        doc.review_status = "pending"
    elif doc.status != "completed":
        doc.review_status = "pending"
    elif not statuses or doc_issue or any(s == "needs_review" for s in statuses):
        doc.review_status = "needs_review"
    else:
        doc.review_status = "ok"


def validate_stored_record(session: Session, rec: Record, settings: Settings, *, check_duplicates: bool = True) -> list[Issue]:
    items = session.scalars(select(LineItem).where(LineItem.record_id == rec.id).order_by(LineItem.position)).all()
    old = {i.code: i for i in session.scalars(select(ValidationIssue).where(ValidationIssue.record_id == rec.id))}
    extra = {}
    for code in ("UNGROUNDED_VALUE", "EXTRACTOR_DISAGREEMENT"):
        if code in old and old[code].details:
            extra["dropped" if code == "UNGROUNDED_VALUE" else "disagreements"] = old[code].details.get("dropped" if code == "UNGROUNDED_VALUE" else "disagreements", [])
    from ..db.models import Document as D

    doc = session.get(D, rec.document_id)
    ocr_conf = doc.ocr_confidence if doc is not None else None
    issues = validate_record(record_to_data(rec, list(items), ocr_conf, extra), settings)
    if check_duplicates:
        issues += find_duplicates(session, rec, settings)
    replace_issues(session, rec, issues, keep_codes={"CROSS_DOC_MISMATCH"})
    rec.updated_at = utcnow()
    return issues
