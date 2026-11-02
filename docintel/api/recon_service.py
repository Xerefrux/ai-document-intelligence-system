"""Running a cross verification and saving the result."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import Settings
from ..db.models import Document, Reconciliation, ReconciliationItem, Record, ValidationIssue
from ..jobs.records import refresh_document_review, set_record_status
from ..validation.reconcile import Side, reconcile, summarize
from .serialize import iso


def _side(rec: Record, filename: str) -> Side:
    return Side(id=rec.id, invoice_number=rec.invoice_number, supplier_name=rec.supplier_name, invoice_date=rec.invoice_date.isoformat() if rec.invoice_date else None,
                total=rec.total, subtotal=rec.subtotal, tax=rec.tax, currency=rec.currency, label=filename)


def _records(session: Session, doc_ids: list[str] | None, kind: str | None) -> list[tuple[Record, str]]:
    q = select(Record, Document.original_filename).join(Document, Document.id == Record.document_id).where(Document.status == "completed")
    if doc_ids:
        q = q.where(Record.document_id.in_(doc_ids))
    if kind:
        q = q.where(Record.kind == kind)
    return list(session.execute(q.order_by(Document.created_at, Record.record_index)).all())


def run_reconciliation(session: Session, settings: Settings, *, left_docs: list[str] | None, right_docs: list[str] | None, mode: str) -> Reconciliation:
    """auto: every invoice record against every spreadsheet ledger row. manual: the two document lists given."""
    if mode == "auto":
        left = _records(session, None, "invoice")
        right = _records(session, None, "ledger_row")
    else:
        left = _records(session, left_docs or [], None)
        right = _records(session, right_docs or [], None)
    items = reconcile([_side(r, f) for r, f in left], [_side(r, f) for r, f in right], settings.amount_tolerance)
    rec = Reconciliation(mode=mode, left={"documents": left_docs, "records": len(left)}, right={"documents": right_docs, "records": len(right)}, summary=summarize(items))
    session.add(rec)
    session.flush()
    touched: set[str] = set()
    for it in items:
        session.add(ReconciliationItem(reconciliation_id=rec.id, key=it.key, status=it.status, left_record_id=it.left.id if it.left else None,
                                       right_record_id=it.right.id if it.right else None, diffs=it.diffs or None))
        if it.status == "MISMATCH":
            for side in (it.left, it.right):
                if side is None:
                    continue
                touched.add(side.id)
                text = "; ".join(f"{d['field']}: {d.get('left')} vs {d.get('right')}" for d in it.diffs)
                other = it.right if side is it.left else it.left
                existing = session.scalar(select(ValidationIssue).where(ValidationIssue.record_id == side.id, ValidationIssue.code == "CROSS_DOC_MISMATCH"))
                if existing is None:
                    doc_id = session.get(Record, side.id).document_id
                    session.add(ValidationIssue(document_id=doc_id, record_id=side.id, code="CROSS_DOC_MISMATCH", severity="warning", field=it.diffs[0]["field"] if it.diffs else None,
                                                message=f"Differs from {other.label if other else 'the other document'}: {text}.", details={"reconciliation_id": rec.id, "diffs": it.diffs}))
    session.flush()
    for rid in touched:
        record = session.get(Record, rid)
        set_record_status(session, record)
        doc = session.get(Document, record.document_id)
        if doc:
            refresh_document_review(session, doc)
    return rec


def reconciliation_detail(session: Session, rec: Reconciliation) -> dict:
    rows = session.scalars(select(ReconciliationItem).where(ReconciliationItem.reconciliation_id == rec.id).order_by(ReconciliationItem.id)).all()
    ids = {r.left_record_id for r in rows} | {r.right_record_id for r in rows}
    ids.discard(None)
    info: dict[str, dict] = {}
    if ids:
        for r, fname in session.execute(select(Record, Document.original_filename).join(Document, Document.id == Record.document_id).where(Record.id.in_(ids))).all():
            info[r.id] = {"id": r.id, "document_id": r.document_id, "filename": fname, "invoice_number": r.invoice_number, "supplier_name": r.supplier_name,
                          "invoice_date": r.invoice_date.isoformat() if r.invoice_date else None, "total": r.total, "subtotal": r.subtotal, "tax": r.tax, "currency": r.currency}
    return {
        "id": rec.id, "mode": rec.mode, "created_at": iso(rec.created_at), "summary": rec.summary, "left": rec.left, "right": rec.right,
        "items": [{"key": r.key, "status": r.status, "left": info.get(r.left_record_id), "right": info.get(r.right_record_id), "diffs": r.diffs or []} for r in rows],
    }
