"""Combine the rules result and the LLM result for the same invoice, field by field.

Agreement raises confidence. A value found by only one side is kept with that side's confidence. When
the two disagree, the amounts decide for money fields (the version that makes subtotal + tax = total
wins); otherwise the more confident value is kept, its confidence is capped below the review threshold,
and the disagreement is recorded so a person sees both readings.
"""

from __future__ import annotations

from rapidfuzz import fuzz

from .normalize import normalize_name
from .rules import _balanced
from .schema import ALL_FIELDS, DATE_FIELDS, MONEY_FIELDS, ExtractedRecord, FieldValue, LineItemValue


def _same(name: str, a, b) -> bool:
    if a is None or b is None:
        return False
    if name in MONEY_FIELDS:
        return abs(float(a) - float(b)) < 0.005
    if name in DATE_FIELDS or name == "currency":
        return str(a) == str(b)
    if name == "invoice_number":
        from .normalize import normalize_invoice_number

        return normalize_invoice_number(str(a)) == normalize_invoice_number(str(b))
    na, nb = normalize_name(str(a)), normalize_name(str(b))
    return bool(na) and (na == nb or fuzz.ratio(na, nb) >= 92)


def _items_score(items: list[LineItemValue], subtotal: float | None, tol: float) -> float:
    if not items:
        return 0.0
    ok = sum(1 for li in items if li.quantity is not None and li.unit_price is not None and li.amount is not None and abs(li.quantity * li.unit_price - li.amount) <= max(tol, 0.011 * abs(li.amount)))
    score = ok + 0.1 * len(items)
    amounts = [li.amount for li in items if li.amount is not None]
    if subtotal is not None and amounts and abs(sum(amounts) - subtotal) <= max(tol, 0.005 * abs(subtotal)):
        score += 5
    return score


def merge_records(rules: ExtractedRecord, llm: ExtractedRecord, *, tolerance: float, review_conf: float) -> ExtractedRecord:
    out = ExtractedRecord(method="hybrid", kind=rules.kind, page_start=rules.page_start, page_end=rules.page_end, sheet_name=rules.sheet_name, row_ref=rules.row_ref)
    out.text, out.ocr_confidence, out.notes = rules.text, rules.ocr_confidence, list(rules.notes)
    out.dropped = list(llm.dropped)

    def money_with(name: str, val: float) -> float | None:
        vals = {m: (val if m == name else rules.value(m)) for m in ("subtotal", "tax", "discount", "shipping", "total")}
        return vals

    for name in ALL_FIELDS:
        r = rules.fields.get(name) or FieldValue()
        m = llm.fields.get(name) or FieldValue()
        if r.value is None and m.value is None:
            out.fields[name] = r if r.evidence or r.note else FieldValue()
            continue
        if r.value is None:
            out.fields[name] = m
            continue
        if m.value is None:
            out.fields[name] = r
            continue
        if _same(name, r.value, m.value):
            best = r if r.confidence >= m.confidence else m
            out.fields[name] = FieldValue(best.value, round(min(0.99, max(r.confidence, m.confidence) + 0.05), 3), best.evidence, best.page or r.page, "both", best.note)
            continue
        # Disagreement.
        pick, other = (r, m) if r.confidence >= m.confidence else (m, r)
        if name in MONEY_FIELDS:
            vals = {k: (rules.value(k) if k != name else None) for k in ("subtotal", "tax", "discount", "shipping", "total")}
            for cand in (r, m):
                trial = dict(vals)
                trial[name] = cand.value
                bal = _balanced(trial["subtotal"], trial["tax"], trial["discount"], trial["shipping"], trial["total"], tolerance)
                if bal:
                    pick = cand
                    other = m if cand is r else r
                    break
        conf = min(pick.confidence, review_conf - 0.01)
        out.fields[name] = FieldValue(pick.value, round(max(conf, 0.1), 3), pick.evidence, pick.page, pick.source, note=f"Rules read {r.value!r}, the LLM read {m.value!r}.")
        out.disagreements.append({"field": name, "rules": r.value, "llm": m.value, "kept": pick.value})

    sub = out.value("subtotal")
    rs, ls = _items_score(rules.line_items, sub, tolerance), _items_score(llm.line_items, sub, tolerance)
    if ls > rs:
        out.line_items = llm.line_items
    else:
        out.line_items = rules.line_items
    if rules.line_items and llm.line_items and abs(len(rules.line_items) - len(llm.line_items)) > 0 and abs(rs - ls) < 1e-9:
        out.notes.append(f"Rules found {len(rules.line_items)} line items and the LLM {len(llm.line_items)}; kept the rules result.")
    return out
