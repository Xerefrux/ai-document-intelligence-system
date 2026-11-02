"""Extracted records: list, read, correct, approve, re-check, delete. Plus the review queue of validation issues."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select, text, update

from ...db.engine import session_scope
from ...db.models import Correction, Document, Record, ValidationIssue, utcnow
from ...jobs.queue import add_event
from ...jobs.records import refresh_document_review, set_record_status, sync_columns, validate_stored_record
from ...search.query import Filters, search
from ..deps import factory_of, filters_dep, get_document, get_record, settings_of
from ..edit import EditError, apply_edit
from ..errors import ApiError
from ..serialize import record_detail

router = APIRouter(tags=["records"])


class RecordPatch(BaseModel):
    fields: dict[str, Any] | None = Field(None, description="Field name to new value. Send null or an empty string to clear a field. Dates are YYYY-MM-DD.")
    line_items: list[dict[str, Any]] | None = Field(None, max_length=2000, description="The complete new list of line items (description, quantity, unit_price, amount).")
    note: str | None = Field(None, max_length=500, description="Why the change was made. Kept in the audit trail.")
    expected_version: int | None = Field(None, ge=1, description="The version you loaded. If the record has changed since, the request fails with 409 instead of overwriting.")


class ApproveIn(BaseModel):
    note: str | None = Field(None, max_length=500)


@router.get("/records", summary="List records, with the same filters as search")
def list_records(request: Request, flt: Filters = Depends(filters_dep), limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)):
    with session_scope(factory_of(request)) as s:
        out = search(s, settings_of(request), "", filters=flt, limit=limit, offset=offset, pages=False)
        ids = [r["id"] for r in out["records"]]
        why: dict[str, list[dict[str, str]]] = {i: [] for i in ids}
        if ids:
            rank = {"error": 0, "warning": 1}
            rows = s.execute(select(ValidationIssue.record_id, ValidationIssue.code, ValidationIssue.severity, ValidationIssue.message).where(ValidationIssue.record_id.in_(ids), ValidationIssue.severity.in_(("error", "warning"))).order_by(ValidationIssue.id)).all()
            for rid, code, sev, msg in sorted(rows, key=lambda x: rank.get(x[2], 2)):
                if len(why[rid]) < 3:
                    why[rid].append({"code": code, "severity": sev, "message": msg})
        for r in out["records"]:
            r["issues"] = why.get(r["id"], [])
    return {"items": out["records"], "total": out["total_records"], "limit": limit, "offset": offset}


@router.get("/records/{rec_id}", summary="One record with every field, its evidence, line items, issues and edit history")
def read_record(request: Request, rec_id: str):
    with session_scope(factory_of(request)) as s:
        return record_detail(s, get_record(s, rec_id))


@router.patch("/records/{rec_id}", summary="Correct values")
def patch_record(request: Request, rec_id: str, body: RecordPatch):
    settings = settings_of(request)
    if not body.fields and body.line_items is None:
        raise ApiError(422, "NOTHING_TO_UPDATE", "Send at least one field or the line item list.")
    with session_scope(factory_of(request)) as s:
        rec = get_record(s, rec_id)
        if body.expected_version is not None:
            # compare and swap: one statement that takes the write lock and checks the version together
            res = s.execute(update(Record).where(Record.id == rec.id, Record.version == body.expected_version).values(version=Record.version))
            if res.rowcount == 0:
                raise ApiError(409, "VERSION_CONFLICT", "This record was changed after you opened it. Reload it and apply your change again.", {"current_version": rec.version})
            s.refresh(rec)
        try:
            changed = apply_edit(s, rec, settings, fields=body.fields, line_items=body.line_items, note=body.note)
        except EditError as exc:
            raise ApiError(422, "INVALID_VALUE", exc.message, {"field": exc.field}) from exc
        return {"changed": changed, "record": record_detail(s, rec)}


@router.post("/records/{rec_id}/approve", summary="Mark a record as checked by a person")
def approve_record(request: Request, rec_id: str, body: ApproveIn | None = None):
    note = body.note if body else None
    with session_scope(factory_of(request)) as s:
        rec = get_record(s, rec_id)
        open_issues = [i for i in s.scalars(select(ValidationIssue).where(ValidationIssue.record_id == rec.id, ValidationIssue.severity.in_(("error", "warning")))).all()]
        previous = rec.review_status
        rec.review_status = "approved"
        rec.version += 1
        rec.updated_at = utcnow()
        extra = f"Approved with open issues: {', '.join(sorted({i.code for i in open_issues}))}." if open_issues else None
        s.add(Correction(record_id=rec.id, field="review_status", old_value=previous, new_value="approved", note=" ".join(x for x in (note, extra) if x) or None))
        add_event(s, rec.document_id, "review", f"Record {rec.invoice_number or rec.id[:8]} approved by a person.")
        doc = s.get(Document, rec.document_id)
        if doc is not None:
            refresh_document_review(s, doc)
        return record_detail(s, rec)


@router.post("/records/{rec_id}/revalidate", summary="Run the validation rules again")
def revalidate_record(request: Request, rec_id: str):
    settings = settings_of(request)
    with session_scope(factory_of(request)) as s:
        rec = get_record(s, rec_id)
        validate_stored_record(s, rec, settings)
        set_record_status(s, rec)
        doc = s.get(Document, rec.document_id)
        if doc is not None:
            refresh_document_review(s, doc)
        return record_detail(s, rec)


@router.delete("/records/{rec_id}", status_code=204, summary="Delete a record that should not exist (for example a false detection)")
def delete_record(request: Request, rec_id: str):
    with session_scope(factory_of(request)) as s:
        rec = get_record(s, rec_id)
        doc_id, label = rec.document_id, rec.invoice_number or rec.id[:8]
        s.execute(delete(Record).where(Record.id == rec.id))
        add_event(s, doc_id, "review", f"Record {label} deleted by a person.", level="warning")
        doc = s.get(Document, doc_id)
        if doc is not None:
            refresh_document_review(s, doc)
    return Response(status_code=204)


@router.post("/documents/{doc_id}/records", status_code=201, summary="Add an empty record by hand (for a document where nothing was detected)")
def add_manual_record(request: Request, doc_id: str):
    settings = settings_of(request)
    with session_scope(factory_of(request)) as s:
        doc = get_document(s, doc_id)
        if doc.status != "completed":
            raise ApiError(409, "NOT_COMPLETED", "Records can only be added once the document has finished processing.")
        nxt = (s.scalar(select(func.max(Record.record_index)).where(Record.document_id == doc.id)) or -1) + 1
        rec = Record(document_id=doc.id, record_index=nxt, kind="invoice", method="manual", confidence=0.0, review_status="needs_review", page_start=1, page_end=doc.page_count or 1)
        sync_columns(rec, {})
        s.add(rec)
        s.flush()
        s.execute(update(Record).where(Record.id == rec.id).values(search_text=""))
        validate_stored_record(s, rec, settings, check_duplicates=False)
        set_record_status(s, rec)
        add_event(s, doc.id, "review", "An empty record was added by a person.")
        # the "no invoice found" document level warning no longer applies
        s.execute(delete(ValidationIssue).where(ValidationIssue.document_id == doc.id, ValidationIssue.record_id.is_(None), ValidationIssue.code == "NO_INVOICE_DETECTED"))
        refresh_document_review(s, doc)
        return record_detail(s, rec)


@router.get("/issues", summary="Validation issues across all documents (the review queue)")
def list_issues(
    request: Request,
    severity: Literal["error", "warning", "info"] | None = None,
    code: str | None = Query(None, max_length=40),
    document_id: str | None = Query(None, max_length=32),
    record_id: str | None = Query(None, max_length=32),
    only_open: bool = Query(True, description="Leave out issues on records a person has already approved"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    base = ["1=1"]
    params: dict[str, Any] = {}
    if document_id:
        base.append("v.document_id = :doc")
        params["doc"] = document_id
    if record_id:
        base.append("v.record_id = :rec")
        params["rec"] = record_id
    if only_open:
        base.append("(r.id IS NULL OR r.review_status <> 'approved')")
    base_sql = " AND ".join(base)
    cond = [base_sql]
    qp = dict(params)
    if severity:
        cond.append("v.severity = :sev")
        qp["sev"] = severity
    if code:
        cond.append("v.code = :code")
        qp["code"] = code
    where = " AND ".join(cond)
    frm = "FROM validation_issues v JOIN documents d ON d.id = v.document_id LEFT JOIN records r ON r.id = v.record_id"
    with session_scope(factory_of(request)) as s:
        total = s.execute(text(f"SELECT count(*) {frm} WHERE {where}"), qp).scalar() or 0
        rows = s.execute(
            text(f"""SELECT v.id, v.code, v.severity, v.field, v.message, v.record_id, v.document_id, d.original_filename AS filename,
                            r.invoice_number, r.supplier_name, r.review_status
                     {frm} WHERE {where}
                     ORDER BY CASE v.severity WHEN 'error' THEN 0 WHEN 'warning' THEN 1 ELSE 2 END, v.id DESC LIMIT :lim OFFSET :off"""),
            {**qp, "lim": limit, "off": offset},
        ).mappings().all()
        by_sev = {k: 0 for k in ("error", "warning", "info")}
        by_sev.update({k: n for k, n in s.execute(text(f"SELECT v.severity, count(*) {frm} WHERE {base_sql} GROUP BY v.severity"), params).all()})
        # per code: how many records (or documents, for document level issues) have an error or warning with it, so the number matches the list a person would open
        by_code = {k: n for k, n in s.execute(text(f"SELECT v.code, count(DISTINCT COALESCE(v.record_id, v.document_id)) AS n {frm} WHERE {base_sql} AND v.severity IN ('error', 'warning') GROUP BY v.code ORDER BY n DESC"), params).all()}
    return {"items": [dict(r) for r in rows], "total": int(total), "limit": limit, "offset": offset, "by_severity": by_sev, "by_code": by_code}
