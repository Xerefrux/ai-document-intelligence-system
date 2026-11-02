"""Extraction from spreadsheets.

Three layouts are recognised:
  ledger        one row per invoice (invoice number and total columns); each row is a "ledger_row" record
  grouped       one row per line item with an invoice number column; rows are grouped into invoices
  invoice sheet a single invoice laid out like a printed one (labels and values in cells, a table in the middle)

The first two read columns by their header names. The last one is turned into layout lines and read by
the same rules as a PDF, so label detection, party detection and the line item table logic are shared.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from ..pipeline.spreadsheet import cell_text
from ..pipeline.types import SheetContent
from .lines import Cell, DocLine, clip
from .normalize import detect_decimal_style, find_dates, infer_date_order, parse_amount, parse_quantity
from .rules import Ctx, extract_invoice
from .schema import ExtractedRecord, FieldValue, LineItemValue

_COLS: list[tuple[str, re.Pattern[str]]] = [
    ("invoice_number", re.compile(r"^(?:tax\s+)?(?:invoice|inv\.?|bill|document|doc|receipt|voucher)\s*(?:no\.?|number|num|nr|#|id|ref)?\.?$|^(?:invoice|inv)\s*(?:no|number|#)$|^ref(?:erence)?(?:\s*no\.?)?$", re.I)),
    ("invoice_date", re.compile(r"^(?:(?:invoice|inv\.?|issue|bill|billing|document|doc\.?)\s*)?(?:date|dt)$|^issued(?:\s*(?:on|date))?$|^date\s+of\s+(?:issue|invoice)$", re.I)),
    ("due_date", re.compile(r"^(?:payment\s+)?due(?:\s*date)?$|^pay(?:ment)?\s+by$", re.I)),
    ("supplier_name", re.compile(r"^(?:supplier|vendor|seller|payee|biller|issued\s+by|company|from)(?:\s*name)?$", re.I)),
    ("customer_name", re.compile(r"^(?:customer|client|buyer|purchaser|bill(?:ed)?\s*to|sold\s*to)(?:\s*name)?$", re.I)),
    ("subtotal", re.compile(r"^(?:sub\s*-?\s*total|net(?:\s*(?:amount|total|value))?|taxable(?:\s*(?:amount|value))?|amount\s+before\s+tax|total\s+(?:excl\w*|ex)\.?\s*(?:tax|vat|gst))$", re.I)),
    ("tax", re.compile(r"^(?:total\s+)?(?:tax(?:\s*amount)?|vat(?:\s*amount)?|gst(?:\s*amount)?|sales\s*tax|iva)$", re.I)),
    ("discount", re.compile(r"^(?:total\s+)?discount(?:\s*amount)?$", re.I)),
    ("shipping", re.compile(r"^(?:shipping|freight|delivery|postage|handling)(?:\s*(?:charges?|cost))?$", re.I)),
    ("total", re.compile(r"^(?:grand\s+total|total(?:\s*(?:amount|due|incl\w*|payable|invoice|value))?|gross(?:\s*(?:amount|total))?|invoice\s*(?:total|amount|value)|amount(?:\s*(?:due|payable))?|balance(?:\s+due)?|payable)$", re.I)),
    ("currency", re.compile(r"^(?:currency(?:\s*code)?|ccy|cur(?:r)?\.?)$", re.I)),
    ("description", re.compile(r"^(?:descriptions?|item(?:\s*description)?s?|product(?:\s*(?:name|description))?|services?|particulars?|details?|goods|article|work\s+description)$", re.I)),
    ("quantity", re.compile(r"^(?:qty\.?|quantity|units?|hours?|hrs\.?|pcs\.?|count)$", re.I)),
    ("unit_price", re.compile(r"^(?:unit\s*(?:price|cost|rate)|price|rate|cost|price\s+each)$", re.I)),
    ("line_amount", re.compile(r"^(?:line\s*(?:total|amount)|ext\.?(?:ended)?(?:\s*(?:price|amount))?|total\s+price|amt\.?)$", re.I)),
]

_TOTAL_ROW = re.compile(r"^(?:grand\s+)?(?:sub\s*-?\s*)?totals?\b|^sum\b|^total\s", re.I)
# a cell that is only a summary label. Checked on the invoice number column itself, where a loose prefix match could eat a real number such as "SUM-2025-01".
_TOTAL_LABEL = re.compile(r"^(?:(?:grand|sub|overall|page|column)\s*-?\s*)*totals?\s*:?$|^sum\s*:?$", re.I)


def map_header(row: list[Any]) -> dict[int, str]:
    out: dict[int, str] = {}
    used: set[str] = set()
    for ci, v in enumerate(row):
        if not isinstance(v, str):
            continue
        t = re.sub(r"\s+", " ", v.strip().strip(":")).strip()
        if not t or len(t) > 40:
            continue
        for name, pat in _COLS:
            if pat.match(t) and name not in used:
                out[ci] = name
                used.add(name)
                break
    return out


def find_header(rows: list[list[Any]]) -> tuple[int, dict[int, str]] | None:
    best: tuple[int, int, dict[int, str]] | None = None
    for r, row in enumerate(rows[:60]):
        m = map_header(row)
        score = len(m)
        if score >= 3 and (best is None or score > best[1]):
            best = (r, score, m)
    return (best[0], best[2]) if best else None


def _to_number(v: Any, hint: str | None) -> tuple[float | None, str | None]:
    """Returns (number, unreadable_text). Empty cells give (None, None)."""
    if v is None or (isinstance(v, str) and not v.strip()):
        return None, None
    if isinstance(v, bool):
        return None, str(v)
    if isinstance(v, (int, float)):
        return (float(v), None) if v == v and abs(v) != float("inf") else (None, str(v))
    if isinstance(v, Decimal):
        return float(v), None
    m = parse_amount(str(v), hint)
    if m is None or m.percent:
        return None, str(v)
    return m.as_float(), None


def _to_date(v: Any, order: str | None) -> tuple[date | None, str | None, bool]:
    if v is None or (isinstance(v, str) and not v.strip()):
        return None, None, False
    if isinstance(v, datetime):
        return v.date(), None, False
    if isinstance(v, date):
        return v, None, False
    found = find_dates(str(v), order)
    if found:
        return found[0].value, None, found[0].ambiguous
    return None, str(v), False


def _fv_text(v: Any, ev: str, conf: float = 0.95) -> FieldValue:
    if v is None or (isinstance(v, str) and not v.strip()):
        return FieldValue()
    return FieldValue(cell_text(v).strip(), conf, ev, None)


def _fv_num(v: Any, ev: str, hint: str | None) -> FieldValue:
    num, bad = _to_number(v, hint)
    if num is not None:
        return FieldValue(round(num, 4), 0.95, ev, None)
    if bad is not None:
        return FieldValue(None, 0.0, bad, None, note="unreadable")
    return FieldValue()


def _fv_date(v: Any, ev: str, order: str | None) -> FieldValue:
    d, bad, amb = _to_date(v, order)
    if d is not None:
        note = None
        conf = 0.95
        if amb:
            conf, note = 0.7, "Ambiguous day/month order; read as day first."
        return FieldValue(d.isoformat(), conf, ev, None, note=note)
    if bad is not None:
        return FieldValue(None, 0.0, bad, None, note="unreadable")
    return FieldValue()


def _row_ev(sheet: str, r: int, row: list[Any]) -> str:
    return clip(f"{sheet}!row {r}: " + " | ".join(cell_text(c) for c in row if c is not None and cell_text(c)))


def _cur_from(v: Any) -> FieldValue:
    t = cell_text(v).strip().upper() if v is not None else ""
    if re.fullmatch(r"[A-Z]{3}", t):
        return FieldValue(t, 0.95, t, None)
    from .normalize import symbol_to_code

    code = symbol_to_code(cell_text(v).strip()) if v is not None else None
    return FieldValue(code, 0.85, cell_text(v).strip(), None) if code else FieldValue()


def _sheet_hint(rows: list[list[Any]]) -> tuple[str | None, str | None]:
    text = "\n".join(str(c) for r in rows[:400] for c in r if isinstance(c, str))
    return detect_decimal_style(text), infer_date_order(text)


def _lines_from_rows(rows: list[list[Any]], page: int = 1, col_width: int = 28) -> list[DocLine]:
    lines: list[DocLine] = []
    for row in rows:
        cells: list[Cell] = []
        parts: list[str] = []
        pos = 0
        for ci, v in enumerate(row):
            t = cell_text(v).strip()
            if not t:
                continue
            col = ci * col_width
            cells.append(Cell(t, col, col + len(t)))
        raw = ""
        for c in cells:
            raw = raw.ljust(c.col) + c.text
        lines.append(DocLine(page, raw, cells, None, "sheet"))
    return lines


# --------------------------------------------------------------------------------- layouts


def _ledger(sheet: SheetContent, h: int, cmap: dict[int, str], rows: list[list[Any]], hint, order) -> list[ExtractedRecord]:
    inv = next(ci for ci, n in cmap.items() if n == "invoice_number")
    totals_col = next((ci for ci, n in cmap.items() if n == "total"), None)
    out: list[ExtractedRecord] = []
    for r in range(h + 1, len(rows)):
        row = rows[r]
        if not any(v is not None and cell_text(v) for v in row):
            continue
        first = next((cell_text(v) for v in row if v is not None and cell_text(v)), "")
        get = lambda name: next((row[ci] for ci, n in cmap.items() if n == name and ci < len(row)), None)  # noqa: E731
        inv_cell = cell_text(row[inv]).strip() if inv < len(row) and row[inv] is not None else ""
        if _TOTAL_LABEL.match(inv_cell) or (_TOTAL_ROW.match(first) and not inv_cell):
            continue
        if (inv >= len(row) or row[inv] in (None, "")) and (totals_col is None or totals_col >= len(row) or row[totals_col] in (None, "")):
            continue
        ev = _row_ev(sheet.name, r + 1, row)
        rec = ExtractedRecord(kind="ledger_row", method="rules", sheet_name=sheet.name, row_ref=f"{sheet.name}!{r + 1}", page_start=None, page_end=None)
        rec.text = ev
        rec.fields["invoice_number"] = _fv_text(get("invoice_number"), ev)
        rec.fields["invoice_date"] = _fv_date(get("invoice_date"), ev, order)
        rec.fields["due_date"] = _fv_date(get("due_date"), ev, order)
        rec.fields["supplier_name"] = _fv_text(get("supplier_name"), ev)
        rec.fields["customer_name"] = _fv_text(get("customer_name"), ev)
        for name in ("subtotal", "tax", "discount", "shipping", "total"):
            rec.fields[name] = _fv_num(get(name), ev, hint)
        rec.fields["currency"] = _cur_from(get("currency"))
        out.append(rec)
    return out


def _grouped(sheet: SheetContent, h: int, cmap: dict[int, str], rows: list[list[Any]], hint, order) -> list[ExtractedRecord]:
    inv = next(ci for ci, n in cmap.items() if n == "invoice_number")
    groups: dict[str, list[tuple[int, list[Any]]]] = {}
    for r in range(h + 1, len(rows)):
        row = rows[r]
        if not any(v is not None and cell_text(v) for v in row):
            continue
        key = cell_text(row[inv]).strip() if inv < len(row) and row[inv] is not None else ""
        if not key or _TOTAL_LABEL.match(key):
            continue
        groups.setdefault(key, []).append((r + 1, row))
    out: list[ExtractedRecord] = []
    for key, grp in groups.items():
        first_r, first = grp[0]
        get = lambda name, rw=first: next((rw[ci] for ci, n in cmap.items() if n == name and ci < len(rw)), None)  # noqa: E731
        ev = _row_ev(sheet.name, first_r, first)
        rec = ExtractedRecord(kind="invoice", method="rules", sheet_name=sheet.name, row_ref=f"{sheet.name}!{grp[0][0]}-{grp[-1][0]}")
        rec.text = "\n".join(_row_ev(sheet.name, r, row) for r, row in grp)
        rec.fields["invoice_number"] = _fv_text(key, ev)
        rec.fields["invoice_date"] = _fv_date(get("invoice_date"), ev, order)
        rec.fields["due_date"] = _fv_date(get("due_date"), ev, order)
        rec.fields["supplier_name"] = _fv_text(get("supplier_name"), ev)
        rec.fields["customer_name"] = _fv_text(get("customer_name"), ev)
        last = grp[-1][1]
        for name in ("subtotal", "tax", "discount", "shipping", "total"):
            rec.fields[name] = _fv_num(get(name, last), _row_ev(sheet.name, grp[-1][0], last), hint)
        rec.fields["currency"] = _cur_from(get("currency"))
        for r, row in grp:
            g = lambda name, rw=row: next((rw[ci] for ci, n in cmap.items() if n == name and ci < len(rw)), None)  # noqa: E731
            desc = g("description")
            qty, _ = _to_number(g("quantity"), hint)
            unit, _ = _to_number(g("unit_price"), hint)
            amt, _ = _to_number(g("line_amount"), hint)
            if desc is None and qty is None and unit is None and amt is None:
                continue
            conf = 0.95 if (qty is not None and unit is not None and amt is not None and abs(qty * unit - amt) <= 0.05 + 0.011 * abs(amt)) else 0.8
            rec.line_items.append(LineItemValue(cell_text(desc).strip() if desc is not None else None, qty, unit, amt, conf, None, _row_ev(sheet.name, r, row)))
        out.append(rec)
    return out


def _invoice_sheet(sheet: SheetContent, rows: list[list[Any]], ctx: Ctx) -> ExtractedRecord | None:
    lines = _lines_from_rows(rows)
    if not any(not ln.blank for ln in lines):
        return None
    rec = extract_invoice(lines, ctx)
    rec.sheet_name = sheet.name
    rec.page_start = rec.page_end = None
    rec.method = "rules"
    useful = rec.fields["invoice_number"].value or rec.fields["total"].value or rec.line_items
    if not useful:
        return None
    for fv in rec.fields.values():
        if fv.evidence and not fv.evidence.startswith(sheet.name):
            fv.evidence = clip(f"{sheet.name}: {fv.evidence}")
    return rec


def extract_from_sheet(sheet: SheetContent, date_order_setting: str, tolerance: float) -> list[ExtractedRecord]:
    rows = sheet.rows
    if not rows:
        return []
    hint, inferred = _sheet_hint(rows)
    order = date_order_setting if date_order_setting in ("dmy", "mdy") else inferred
    ctx = Ctx(order=order, decimal_hint=hint, tolerance=tolerance)

    found = find_header(rows)
    if found:
        h, cmap = found
        names = set(cmap.values())
        if "invoice_number" in names:
            inv_ci = next(ci for ci, n in cmap.items() if n == "invoice_number")
            keys = [cell_text(r[inv_ci]).strip() for r in rows[h + 1:] if inv_ci < len(r) and r[inv_ci] not in (None, "") and cell_text(r[inv_ci]).strip()]
            repeated = len(set(keys)) < len(keys)
            lineish = "description" in names and (bool(names & {"quantity", "unit_price", "line_amount"}) or repeated)
            recs: list[ExtractedRecord] = []
            if lineish:
                if "line_amount" not in names and "total" in names:
                    # In a table of line items a column called Total or Amount is the amount of that row.
                    for ci, n in list(cmap.items()):
                        if n == "total":
                            cmap[ci] = "line_amount"
                recs = _grouped(sheet, h, cmap, rows, hint, order)
            elif "total" in names:
                recs = _ledger(sheet, h, cmap, rows, hint, order)
            if recs:
                return recs
    rec = _invoice_sheet(sheet, rows, ctx)
    return [rec] if rec else []
