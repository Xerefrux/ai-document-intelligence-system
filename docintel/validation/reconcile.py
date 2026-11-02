"""Cross verification: compare two sets of records, for example invoice PDFs against an Excel ledger.

Records are paired by normalized invoice number (and by supplier when several share a number).
Each pair is MATCH or MISMATCH (with the differing fields). Anything without a partner is
MISSING_RIGHT (only on the left) or MISSING_LEFT (only on the right).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from rapidfuzz import fuzz

from ..extraction.normalize import normalize_invoice_number, normalize_name

COMPARED_MONEY = ("total", "subtotal", "tax")


@dataclass
class Side:
    """The few fields of a record that matter for comparison."""

    id: str
    invoice_number: str | None
    supplier_name: str | None = None
    invoice_date: str | None = None
    total: float | None = None
    subtotal: float | None = None
    tax: float | None = None
    currency: str | None = None
    label: str = ""


@dataclass
class Item:
    key: str | None
    status: str
    left: Side | None
    right: Side | None
    diffs: list[dict[str, Any]] = field(default_factory=list)


def _supplier_close(a: str | None, b: str | None) -> bool | None:
    na, nb = normalize_name(a), normalize_name(b)
    if not na or not nb:
        return None
    return na == nb or fuzz.token_set_ratio(na, nb) >= 88


def compare(left: Side, right: Side, tol: float) -> list[dict[str, Any]]:
    diffs: list[dict[str, Any]] = []
    for name in COMPARED_MONEY:
        a, b = getattr(left, name), getattr(right, name)
        if a is not None and b is not None and abs(a - b) > tol:
            diffs.append({"field": name, "left": a, "right": b, "difference": round(a - b, 4)})
    if left.invoice_date and right.invoice_date and left.invoice_date != right.invoice_date:
        diffs.append({"field": "invoice_date", "left": left.invoice_date, "right": right.invoice_date})
    if left.currency and right.currency and left.currency != right.currency:
        diffs.append({"field": "currency", "left": left.currency, "right": right.currency})
    if _supplier_close(left.supplier_name, right.supplier_name) is False:
        diffs.append({"field": "supplier_name", "left": left.supplier_name, "right": right.supplier_name})
    return diffs


def reconcile(left: list[Side], right: list[Side], tol: float = 0.05) -> list[Item]:
    by_key: dict[str, list[Side]] = {}
    for r in right:
        k = normalize_invoice_number(r.invoice_number)
        by_key.setdefault(k, []).append(r)
    used: set[str] = set()
    items: list[Item] = []
    for l in left:
        k = normalize_invoice_number(l.invoice_number)
        if not k:
            items.append(Item(None, "MISSING_RIGHT", l, None))
            continue
        pool = [c for c in by_key.get(k, []) if c.id not in used]
        pick = None
        for c in pool:  # prefer the candidate with the same supplier when numbers repeat
            if _supplier_close(l.supplier_name, c.supplier_name) is not False:
                pick = c
                break
        if pick is None and pool and len(pool) == 1 and len([x for x in left if normalize_invoice_number(x.invoice_number) == k]) == 1:
            pick = pool[0]
        if pick is None:
            items.append(Item(l.invoice_number, "MISSING_RIGHT", l, None))
            continue
        used.add(pick.id)
        diffs = compare(l, pick, tol)
        items.append(Item(l.invoice_number, "MISMATCH" if diffs else "MATCH", l, pick, diffs))
    for r in right:
        if r.id not in used:
            items.append(Item(r.invoice_number, "MISSING_LEFT", None, r))
    return items


def summarize(items: list[Item]) -> dict[str, int]:
    out = {"MATCH": 0, "MISMATCH": 0, "MISSING_RIGHT": 0, "MISSING_LEFT": 0}
    for it in items:
        out[it.status] = out.get(it.status, 0) + 1
    out["total"] = len(items)
    return out
