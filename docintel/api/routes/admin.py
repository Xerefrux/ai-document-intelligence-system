"""Health, settings, statistics, the job list, export, and cross verification of documents."""

from __future__ import annotations

import csv
import io
import json
import statistics
from datetime import timedelta
from typing import Any, Iterator, Literal

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select, text

from ...db.engine import session_scope
from ...db.models import Correction, Document, Job, Reconciliation, Record, ValidationIssue, utcnow
from ...jobs.queue import queue_stats
from ...jobs.worker import list_workers
from ...search.query import Filters
from ..deps import factory_of, filters_dep, get_record, settings_of, valid_id
from ..errors import ApiError
from ..recon_service import reconciliation_detail, run_reconciliation
from ..serialize import iso

public = APIRouter(tags=["system"])
router = APIRouter(tags=["system"])


# ------------------------------------------------------------------ health (no key needed)


@public.get("/health", summary="Is the service up?")
def health(request: Request):
    settings, factory = settings_of(request), factory_of(request)
    try:
        with session_scope(factory) as s:
            s.execute(text("SELECT 1"))
            q = queue_stats(s)
        workers = [w for w in list_workers(factory) if w["alive"]]
    except Exception:  # noqa: BLE001
        return JSONResponse({"status": "down", "database": "unreachable"}, status_code=503)
    embedded = getattr(request.app.state, "workers", None)
    stalled = q["queued"] > 0 and not workers and q["oldest_queued_seconds"] > 30
    return {
        "status": "degraded" if stalled else "ok",
        "version": settings.public_view()["version"],
        "database": "ok",
        "queue": {"queued": q["queued"], "running": q["running"], "oldest_queued_seconds": q["oldest_queued_seconds"]},
        "workers": {"alive": len(workers), "embedded_threads": embedded.alive if embedded else 0},
        "ocr": {"tesseract": settings.tesseract_available, "engine": settings.ocr_engine},
        "llm": settings.resolved_llm_provider,
        "auth_required": bool(settings.api_key),
        "message": "Jobs are waiting but no worker is running. Start one with `docintel worker` or set DOCINTEL_WORKERS=1." if stalled else None,
    }


@router.get("/config", summary="Settings that are safe to show (keys are never included)")
def config(request: Request):
    return settings_of(request).public_view()


# ------------------------------------------------------------------ statistics


def _percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    k = min(len(ordered) - 1, max(0, int(round(p * (len(ordered) - 1)))))
    return ordered[k]


@router.get("/stats", summary="Counts, success rate, processing time and queue state")
def stats(request: Request):
    factory = factory_of(request)
    with session_scope(factory) as s:
        by_status = {k: 0 for k in ("queued", "processing", "completed", "failed", "cancelled")}
        by_status.update({k: n for k, n in s.execute(select(Document.status, func.count()).group_by(Document.status)).all()})
        by_review = {k: n for k, n in s.execute(select(Document.review_status, func.count()).where(Document.status == "completed").group_by(Document.review_status)).all()}
        rec_review = {k: 0 for k in ("ok", "needs_review", "corrected", "approved")}
        rec_review.update({k: n for k, n in s.execute(select(Record.review_status, func.count()).group_by(Record.review_status)).all()})
        sev = {k: 0 for k in ("error", "warning", "info")}
        sev.update({k: n for k, n in s.execute(select(ValidationIssue.severity, func.count()).group_by(ValidationIssue.severity)).all()})
        top = s.execute(select(ValidationIssue.code, func.count().label("n")).group_by(ValidationIssue.code).order_by(func.count().desc()).limit(10)).all()
        times = [ms / 1000 for (ms,) in s.execute(select(Document.processing_ms).where(Document.status == "completed", Document.processing_ms.is_not(None)).order_by(Document.completed_at.desc()).limit(1000)).all()]
        hour_ago = utcnow() - timedelta(hours=1)
        last_hour_docs = s.scalar(select(func.count()).select_from(Document).where(Document.status == "completed", Document.completed_at >= hour_ago)) or 0
        last_hour_pages = s.scalar(select(func.coalesce(func.sum(Document.page_count), 0)).where(Document.status == "completed", Document.completed_at >= hour_ago)) or 0
        no_records = s.scalar(select(func.count()).select_from(Document).where(Document.status == "completed", Document.record_count == 0)) or 0
        finished = by_status["completed"] + by_status["failed"]
        out: dict[str, Any] = {
            "documents": {
                "total": sum(by_status.values()), "by_status": by_status, "by_review": by_review, "no_records": no_records,
                "success_rate": round(by_status["completed"] / finished, 4) if finished else None,
                "failure_rate": round(by_status["failed"] / finished, 4) if finished else None,
            },
            "records": {"total": sum(rec_review.values()), "by_review": rec_review},
            "issues": {"by_severity": sev, "top_codes": [{"code": c, "count": n} for c, n in top]},
            "corrections": s.scalar(select(func.count()).select_from(Correction)) or 0,
            "processing": {
                "samples": len(times), "median_seconds": round(statistics.median(times), 3) if times else None,
                "p95_seconds": round(_percentile(times, 0.95), 3) if times else None,
                "documents_last_hour": last_hour_docs, "pages_last_hour": int(last_hour_pages),
            },
            "queue": queue_stats(s),
        }
    out["workers"] = list_workers(factory)
    return out


@router.get("/jobs", summary="Background jobs")
def list_jobs(request: Request, status: Literal["queued", "running", "succeeded", "failed", "cancelled"] | None = None, limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)):
    with session_scope(factory_of(request)) as s:
        q = select(Job, Document.original_filename).join(Document, Document.id == Job.document_id)
        c = select(func.count()).select_from(Job)
        if status:
            q, c = q.where(Job.status == status), c.where(Job.status == status)
        rows = s.execute(q.order_by(Job.created_at.desc()).limit(limit).offset(offset)).all()
        total = s.scalar(c) or 0
        items = [
            {"id": j.id, "document_id": j.document_id, "filename": fn, "kind": j.kind, "status": j.status, "attempts": j.attempts, "max_attempts": j.max_attempts, "stage": j.stage,
             "progress_done": j.progress_done, "progress_total": j.progress_total, "error_code": j.error_code, "error_message": j.error_message, "worker": j.locked_by,
             "created_at": iso(j.created_at), "started_at": iso(j.started_at), "finished_at": iso(j.finished_at), "run_after": iso(j.run_after)}
            for j, fn in rows
        ]
    return {"items": items, "total": total, "limit": limit, "offset": offset}


# ------------------------------------------------------------------ export

_CSV_COLUMNS = ["record_id", "document_id", "filename", "page", "kind", "invoice_number", "invoice_date", "due_date", "supplier_name", "customer_name", "subtotal", "tax", "total", "currency",
                "review_status", "confidence", "issue_codes"]


def _csv_safe(v: Any) -> Any:
    """Spreadsheet programs run text that starts with = + - @ as a formula. Prefix those with a quote."""
    if isinstance(v, str) and v[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + v
    return v


@router.get("/export/records", summary="Download records as CSV or JSON")
def export_records(request: Request, format: Literal["csv", "json"] = "csv", flt: Filters = Depends(filters_dep), limit: int = Query(100000, ge=1, le=100000)):
    factory = factory_of(request)
    where, params = flt.sql()
    sql = text(f"""SELECT r.id, r.document_id, d.original_filename AS filename, r.page_start AS page, r.kind, r.invoice_number, r.invoice_date, r.due_date, r.supplier_name, r.customer_name,
                          r.subtotal, r.tax, r.total, r.currency, r.review_status, r.confidence,
                          (SELECT group_concat(DISTINCT v.code) FROM validation_issues v WHERE v.record_id = r.id AND v.severity IN ('error','warning')) AS issue_codes
                   FROM records r JOIN documents d ON d.id = r.document_id WHERE 1=1 {where} ORDER BY r.created_at, r.record_index LIMIT :lim""")

    def rows() -> Iterator[dict[str, Any]]:
        with session_scope(factory) as s:
            for r in s.execute(sql, {**params, "lim": limit}).mappings():
                d = dict(r)
                d["record_id"] = d.pop("id")
                for k in ("invoice_date", "due_date"):
                    d[k] = iso(d[k])
                yield d

    if format == "json":
        def gen_json() -> Iterator[str]:
            yield "["
            first = True
            for d in rows():
                d["issue_codes"] = d["issue_codes"].split(",") if d["issue_codes"] else []
                yield ("" if first else ",") + json.dumps(d, ensure_ascii=False)
                first = False
            yield "]"

        return StreamingResponse(gen_json(), media_type="application/json", headers={"Content-Disposition": 'attachment; filename="records.json"'})

    def gen_csv() -> Iterator[str]:
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(_CSV_COLUMNS)
        yield "﻿" + buf.getvalue()  # byte order mark so Excel opens it as UTF-8
        for d in rows():
            buf.seek(0), buf.truncate()
            w.writerow([_csv_safe(d.get(c)) for c in _CSV_COLUMNS])
            yield buf.getvalue()

    return StreamingResponse(gen_csv(), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": 'attachment; filename="records.csv"'})


# ------------------------------------------------------------------ reconciliation


class ReconcileIn(BaseModel):
    mode: Literal["auto", "manual"] = Field("auto", description="auto: every invoice record against every spreadsheet row already stored. manual: the two lists of documents below.")
    left_documents: list[str] = Field(default_factory=list, max_length=500)
    right_documents: list[str] = Field(default_factory=list, max_length=500)


@router.post("/reconciliations", status_code=201, summary="Compare invoices against a spreadsheet (or any two sets of documents)")
def create_reconciliation(request: Request, body: ReconcileIn):
    settings = settings_of(request)
    with session_scope(factory_of(request)) as s:
        if body.mode == "manual":
            if not body.left_documents or not body.right_documents:
                raise ApiError(422, "MISSING_SIDES", "Choose at least one document for each side.")
            wanted = set(body.left_documents) | set(body.right_documents)
            found = {i for (i,) in s.execute(select(Document.id).where(Document.id.in_(wanted))).all()}
            if wanted - found:
                raise ApiError(404, "NOT_FOUND", f"{len(wanted - found)} of the chosen documents do not exist.")
        rec = run_reconciliation(s, settings, left_docs=body.left_documents, right_docs=body.right_documents, mode=body.mode)
        return reconciliation_detail(s, rec)


@router.get("/reconciliations", summary="Earlier comparisons")
def list_reconciliations(request: Request, limit: int = Query(20, ge=1, le=100)):
    with session_scope(factory_of(request)) as s:
        rows = s.scalars(select(Reconciliation).order_by(Reconciliation.created_at.desc()).limit(limit)).all()
        return {"items": [{"id": r.id, "mode": r.mode, "created_at": iso(r.created_at), "summary": r.summary} for r in rows]}


@router.get("/reconciliations/{rec_id}", summary="One comparison with every pair")
def read_reconciliation(request: Request, rec_id: str):
    with session_scope(factory_of(request)) as s:
        rec = s.get(Reconciliation, valid_id(rec_id, "Reconciliation"))
        if rec is None:
            raise ApiError(404, "NOT_FOUND", "Reconciliation not found.")
        return reconciliation_detail(s, rec)
