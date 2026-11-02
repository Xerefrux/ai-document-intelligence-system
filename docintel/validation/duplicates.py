"""Duplicate invoice detection against records already in the database.

Two levels:
  DUPLICATE_INVOICE   same supplier (allowing small spelling differences) and the same invoice number
  POSSIBLE_DUPLICATE  same supplier, date and total, but the invoice number differs (a typo, an OCR slip,
                      or the same bill sent again with a new number)

The same invoice number from two different suppliers is normal (many suppliers number from 1) and is not flagged.
Only the later record is flagged; the earlier one is the original.
"""

from __future__ import annotations

from rapidfuzz import fuzz
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from ..config import Settings
from ..db.models import Document, Record
from ..extraction.normalize import normalize_invoice_number, normalize_name
from .rules import Issue

SUPPLIER_SAME = 88  # token_set_ratio at or above this counts as the same supplier


def keys_for(invoice_number: str | None, supplier_name: str | None) -> tuple[str | None, str | None]:
    inv_key = normalize_invoice_number(invoice_number) or None
    sup = normalize_name(supplier_name)
    dedupe = f"{sup}|{inv_key}" if inv_key and sup else None
    return inv_key, dedupe


def _same_supplier(a: str | None, b: str | None) -> bool | None:
    """True or False, or None when either name is missing."""
    na, nb = normalize_name(a), normalize_name(b)
    if not na or not nb:
        return None
    return na == nb or fuzz.token_set_ratio(na, nb) >= SUPPLIER_SAME


def _earlier(c: Record, rec: Record) -> bool:
    return (c.created_at, c.id) < (rec.created_at, rec.id)


def find_duplicates(session: Session, rec: Record, settings: Settings) -> list[Issue]:
    tol = max(settings.amount_tolerance, 0.005)
    conds = []
    if rec.inv_key:
        conds.append(Record.inv_key == rec.inv_key)
    if rec.invoice_date is not None and rec.total is not None:
        conds.append(and_(Record.invoice_date == rec.invoice_date, Record.total.between(rec.total - tol, rec.total + tol)))
    if not conds:
        return []
    q = (
        select(Record, Document.original_filename)
        .join(Document, Document.id == Record.document_id)
        .where(Record.kind == rec.kind, Record.id != rec.id, or_(*conds))
        .order_by(Record.created_at)
        .limit(200)
    )
    issues: list[Issue] = []
    seen: set[str] = set()
    for cand, filename in session.execute(q).all():
        if not _earlier(cand, rec):
            continue
        same_sup = _same_supplier(rec.supplier_name, cand.supplier_name)
        same_number = bool(rec.inv_key) and cand.inv_key == rec.inv_key
        same_total = rec.total is not None and cand.total is not None and abs(cand.total - rec.total) <= tol
        same_date = rec.invoice_date is not None and cand.invoice_date == rec.invoice_date
        info = {"duplicate_of_record": cand.id, "duplicate_of_document": cand.document_id, "filename": filename, "invoice_number": cand.invoice_number}
        if same_number and same_sup is True:
            if "DUPLICATE_INVOICE" not in seen:
                seen.add("DUPLICATE_INVOICE")
                issues.append(Issue("DUPLICATE_INVOICE", "error", "invoice_number",
                                    f"Invoice {rec.invoice_number} from {cand.supplier_name or 'the same supplier'} already exists (file {filename}).", info))
        elif same_number and same_sup is None and same_total:
            # supplier unknown on one side: same number and same total is still strong evidence
            if "DUPLICATE_INVOICE" not in seen:
                seen.add("DUPLICATE_INVOICE")
                issues.append(Issue("DUPLICATE_INVOICE", "error", "invoice_number",
                                    f"Invoice {rec.invoice_number} with the same total already exists (file {filename}); a supplier name is missing on one of them.", info))
        elif not same_number and same_sup is True and same_total and same_date:
            if "POSSIBLE_DUPLICATE" not in seen and "DUPLICATE_INVOICE" not in seen:
                seen.add("POSSIBLE_DUPLICATE")
                issues.append(Issue("POSSIBLE_DUPLICATE", "warning", "invoice_number",
                                    f"Same supplier, date and total as invoice {cand.invoice_number or '(no number)'} in {filename}, but a different invoice number.", info))
    return issues
