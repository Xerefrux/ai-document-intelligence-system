"""Small helpers shared by the route modules."""

from __future__ import annotations

import re
from datetime import date
from typing import Literal

from fastapi import Query, Request
from sqlalchemy.orm import Session, sessionmaker

from ..config import Settings
from ..db.models import Document, Record
from ..search.query import Filters
from .errors import ApiError
from .security import api_key_ok

ID_RE = re.compile(r"^[0-9a-f]{32}$")


def settings_of(request: Request) -> Settings:
    return request.app.state.settings


def factory_of(request: Request) -> sessionmaker[Session]:
    return request.app.state.factory


def require_auth(request: Request) -> None:
    if not api_key_ok(request.app.state.settings, request):
        raise ApiError(401, "UNAUTHORIZED", "A valid X-API-Key header is required.")


def valid_id(value: str, what: str = "Item") -> str:
    if not ID_RE.match(value or ""):
        raise ApiError(404, "NOT_FOUND", f"{what} not found.")
    return value


def get_document(s: Session, doc_id: str) -> Document:
    d = s.get(Document, valid_id(doc_id, "Document"))
    if d is None:
        raise ApiError(404, "NOT_FOUND", "Document not found.")
    return d


def get_record(s: Session, rec_id: str) -> Record:
    r = s.get(Record, valid_id(rec_id, "Record"))
    if r is None:
        raise ApiError(404, "NOT_FOUND", "Record not found.")
    return r


def filters_dep(
    supplier: str | None = Query(None, max_length=200, description="Supplier name contains"),
    customer: str | None = Query(None, max_length=200, description="Customer name contains"),
    invoice_number: str | None = Query(None, max_length=128, description="Invoice number contains"),
    date_from: date | None = Query(None, description="Invoice date on or after (YYYY-MM-DD)"),
    date_to: date | None = Query(None, description="Invoice date on or before (YYYY-MM-DD)"),
    min_total: float | None = Query(None, description="Total at least"),
    max_total: float | None = Query(None, description="Total at most"),
    currency: str | None = Query(None, max_length=8),
    review_status: Literal["ok", "needs_review", "corrected", "approved"] | None = Query(None),
    document_id: str | None = Query(None, max_length=32),
    has_issues: bool | None = Query(None, description="Only records with (true) or without (false) errors or warnings"),
    issue_code: str | None = Query(None, max_length=40, description="Only records with this validation code"),
    file_type: Literal["pdf", "image", "xlsx", "xls", "csv"] | None = Query(None),
) -> Filters:
    return Filters(
        supplier=supplier, customer=customer, invoice_number=invoice_number, date_from=date_from, date_to=date_to, min_total=min_total, max_total=max_total,
        currency=currency, review_status=review_status, document_id=document_id, has_issues=has_issues, issue_code=issue_code, file_type=file_type,
    )
