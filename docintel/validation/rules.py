"""Validation rules for one extracted record.

Pure functions, no database. The same rules run after extraction, after a manual correction and
when a record is revalidated. Severity decides what happens next:

  error    something required is missing or the numbers do not add up; the record goes to review
  warning  worth a human look (low confidence, ambiguous date, possible duplicate); also goes to review
  info     good to know (no customer printed, no tax line); never forces a review
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from ..config import Settings
from ..extraction.schema import DATE_FIELDS, MONEY_FIELDS, REQUIRED_FIELDS, FieldValue, LineItemValue

# code -> (default severity, what it means). Shown by GET /api/config and in the docs.
CODES: dict[str, tuple[str, str]] = {
    "MISSING_FIELD": ("error", "A required field was not found on the document."),
    "INVALID_NUMBER": ("error", "A number could not be read, or is zero or negative where that is unusual."),
    "INVALID_DATE": ("error", "A date could not be read, or is far outside a believable range."),
    "DATE_AMBIGUOUS": ("warning", "The date can be read as day/month or month/day and nothing else on the page settles it."),
    "DUE_BEFORE_INVOICE_DATE": ("warning", "The due date is earlier than the invoice date."),
    "LINE_ITEM_MISMATCH": ("error", "Quantity times unit price does not equal the line amount."),
    "SUBTOTAL_MISMATCH": ("error", "The line amounts do not add up to the subtotal."),
    "TOTAL_MISMATCH": ("error", "Subtotal, tax, discount and shipping do not add up to the total."),
    "DUPLICATE_INVOICE": ("error", "Another record has the same supplier and invoice number."),
    "POSSIBLE_DUPLICATE": ("warning", "Another record has the same supplier, date and total under a different number."),
    "LOW_CONFIDENCE": ("warning", "A required value was read with low confidence."),
    "UNGROUNDED_VALUE": ("warning", "The AI returned a value that is not in the document text; it was discarded."),
    "OCR_LOW_QUALITY": ("warning", "The scan was hard to read; check the values against the image."),
    "CROSS_DOC_MISMATCH": ("warning", "The same invoice differs between two documents (for example a PDF and a spreadsheet)."),
    "EXTRACTOR_DISAGREEMENT": ("warning", "The rules and the AI read a field differently."),
    "NO_INVOICE_DETECTED": ("warning", "No invoice or invoice table was found in this document."),
}

INFO_IF_MISSING = ("customer_name", "currency", "subtotal", "tax")


@dataclass
class Issue:
    code: str
    severity: str
    field: str | None
    message: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class RecordData:
    """What the rules need to know about a record. Built from an extraction result or from the database."""

    kind: str = "invoice"
    fields: dict[str, FieldValue] = field(default_factory=dict)
    line_items: list[LineItemValue] = field(default_factory=list)
    method: str = "rules"
    ocr_confidence: float | None = None
    dropped: list[dict] = field(default_factory=list)
    disagreements: list[dict] = field(default_factory=list)

    def value(self, name: str) -> Any:
        fv = self.fields.get(name)
        return fv.value if fv else None

    def fv(self, name: str) -> FieldValue | None:
        return self.fields.get(name)


def _d(x: Any) -> Decimal:
    return Decimal(str(round(float(x), 4)))


def _fmt(x: Any) -> str:
    return f"{float(x):,.2f}"


def _parse_date(v: Any) -> date | None:
    if isinstance(v, date):
        return v
    if isinstance(v, str):
        try:
            return date.fromisoformat(v[:10])
        except ValueError:
            return None
    return None


def review_needed(issues: list[Issue]) -> bool:
    return any(i.severity in ("error", "warning") for i in issues)


def validate_record(rec: RecordData, settings: Settings) -> list[Issue]:
    out: list[Issue] = []
    tol = _d(settings.amount_tolerance)
    ledger = rec.kind == "ledger_row"

    # ---- missing or unreadable fields
    required = ("invoice_number", "total") if ledger else REQUIRED_FIELDS
    for name in (*REQUIRED_FIELDS, *INFO_IF_MISSING):
        fv = rec.fv(name)
        if fv is not None and fv.value is not None:
            continue
        label = name.replace("_", " ")
        if fv is not None and fv.note == "unreadable" and fv.evidence:
            if name in DATE_FIELDS:
                out.append(Issue("INVALID_DATE", "error", name, f"The {label} could not be read: '{fv.evidence}'.", {"text": fv.evidence}))
            else:
                out.append(Issue("INVALID_NUMBER", "error", name, f"The {label} could not be read as a number: '{fv.evidence}'.", {"text": fv.evidence}))
            continue
        if name in required:
            out.append(Issue("MISSING_FIELD", "error", name, f"The {label} was not found."))
        elif name in REQUIRED_FIELDS:
            out.append(Issue("MISSING_FIELD", "warning" if name == "invoice_date" else "info", name, f"The {label} was not found."))
        else:
            out.append(Issue("MISSING_FIELD", "info", name, f"No {label} on the document."))
    for name in ("due_date", "discount", "shipping"):
        fv = rec.fv(name)
        if fv is not None and fv.value is None and fv.note == "unreadable" and fv.evidence:
            code = "INVALID_DATE" if name in DATE_FIELDS else "INVALID_NUMBER"
            out.append(Issue(code, "warning", name, f"The {name.replace('_', ' ')} could not be read: '{fv.evidence}'.", {"text": fv.evidence}))

    # ---- number sanity
    for name in MONEY_FIELDS:
        v = rec.value(name)
        if v is None:
            continue
        if name == "total" and float(v) <= 0:
            out.append(Issue("INVALID_NUMBER", "warning", name, f"The total is {_fmt(v)}; zero or negative totals are unusual (credit note?).", {"value": v}))
        elif name in ("subtotal", "tax", "discount", "shipping") and float(v) < 0 and name != "discount":
            out.append(Issue("INVALID_NUMBER", "warning", name, f"The {name} is negative ({_fmt(v)}).", {"value": v}))
        if abs(float(v)) > 1e10:
            out.append(Issue("INVALID_NUMBER", "warning", name, f"The {name} is implausibly large ({v}).", {"value": v}))

    # ---- dates
    inv_date = _parse_date(rec.value("invoice_date"))
    due_date = _parse_date(rec.value("due_date"))
    today = date.today()
    for name, d in (("invoice_date", inv_date), ("due_date", due_date)):
        if d is None:
            continue
        if d.year < 1990 or d > today + timedelta(days=366):
            out.append(Issue("INVALID_DATE", "warning", name, f"The {name.replace('_', ' ')} ({d.isoformat()}) is outside a believable range.", {"value": d.isoformat()}))
        fv = rec.fv(name)
        if fv is not None and fv.note and fv.note.lower().startswith("ambiguous"):
            out.append(Issue("DATE_AMBIGUOUS", "warning", name, f"The {name.replace('_', ' ')} '{fv.evidence}' could be day/month or month/day; it was read as {d.isoformat()}.", {"read_as": d.isoformat(), "evidence": fv.evidence}))
    if inv_date and due_date and due_date < inv_date:
        out.append(Issue("DUE_BEFORE_INVOICE_DATE", "warning", "due_date", f"The due date {due_date.isoformat()} is before the invoice date {inv_date.isoformat()}.", {"invoice_date": inv_date.isoformat(), "due_date": due_date.isoformat()}))

    # ---- arithmetic
    items = [li for li in rec.line_items if any(v is not None for v in (li.quantity, li.unit_price, li.amount))]
    bad_lines = 0
    for pos, li in enumerate(items, 1):
        if li.quantity is None or li.unit_price is None or li.amount is None:
            continue
        expected = _d(li.quantity) * _d(li.unit_price)
        allowed = max(tol, abs(_d(li.amount)) * Decimal("0.0005"))
        if abs(expected - _d(li.amount)) > allowed:
            bad_lines += 1
            if bad_lines <= 10:
                out.append(Issue("LINE_ITEM_MISMATCH", "error", f"line_items[{pos}]", f"Line {pos}: {li.quantity:g} x {_fmt(li.unit_price)} = {_fmt(expected)}, but the amount is {_fmt(li.amount)}.", {"line": pos, "expected": float(expected), "found": li.amount, "description": li.description}))
    if bad_lines > 10:
        out.append(Issue("LINE_ITEM_MISMATCH", "error", "line_items", f"{bad_lines - 10} more line items do not multiply out.", {"more": bad_lines - 10}))

    amounts = [li.amount for li in items]
    line_sum = sum((_d(a) for a in amounts), Decimal(0)) if amounts and all(a is not None for a in amounts) else None
    subtotal = rec.value("subtotal")
    total = rec.value("total")
    if line_sum is not None and subtotal is not None and abs(line_sum - _d(subtotal)) > tol:
        out.append(Issue("SUBTOTAL_MISMATCH", "error", "subtotal", f"The line amounts add up to {_fmt(line_sum)}, but the subtotal is {_fmt(subtotal)}.", {"expected": float(line_sum), "found": subtotal}))
    base = _d(subtotal) if subtotal is not None else line_sum
    if total is not None and base is not None:
        tax = rec.value("tax")
        discount = rec.value("discount")
        shipping = rec.value("shipping")
        expected = base + (_d(tax) if tax is not None else 0) - abs(_d(discount) if discount is not None else 0) + (_d(shipping) if shipping is not None else 0)
        if abs(expected - _d(total)) > tol:
            parts = f"subtotal {_fmt(base)}" + (f" + tax {_fmt(tax)}" if tax is not None else " (no tax found)")
            if discount:
                parts += f" - discount {_fmt(abs(discount))}"
            if shipping:
                parts += f" + shipping {_fmt(shipping)}"
            out.append(Issue("TOTAL_MISMATCH", "error", "total", f"{parts} = {_fmt(expected)}, but the total is {_fmt(total)}.", {"expected": float(expected), "found": total, "difference": float(_d(total) - expected)}))

    # ---- how sure are we
    for name in REQUIRED_FIELDS:
        fv = rec.fv(name)
        if fv is not None and fv.value is not None and fv.source != "manual" and fv.confidence < settings.review_confidence:
            out.append(Issue("LOW_CONFIDENCE", "warning", name, f"The {name.replace('_', ' ')} was read with low confidence ({fv.confidence:.2f}).", {"confidence": fv.confidence, "evidence": fv.evidence}))
    if rec.ocr_confidence is not None and rec.ocr_confidence < settings.ocr_low_quality_below:
        out.append(Issue("OCR_LOW_QUALITY", "warning", None, f"The scan was read with low OCR confidence ({rec.ocr_confidence:.2f}). Check the values against the image.", {"ocr_confidence": rec.ocr_confidence}))
    if rec.dropped:
        names = ", ".join(sorted({d.get("field", "?") for d in rec.dropped}))
        out.append(Issue("UNGROUNDED_VALUE", "warning", None, f"The AI returned values that are not in the document text and were discarded: {names}.", {"dropped": rec.dropped[:10]}))
    if rec.disagreements:
        names = ", ".join(sorted({d.get("field", "?") for d in rec.disagreements}))
        out.append(Issue("EXTRACTOR_DISAGREEMENT", "warning", None, f"The rules and the AI read these fields differently: {names}.", {"disagreements": rec.disagreements[:10]}))
    return out
