"""Compare extraction output with the ground truth and turn it into numbers.

Definitions (the evaluation report repeats these):

  field accuracy      the share of fields whose value equals the truth after normalising. Only fields with a
                      printed truth value are counted here; invented values are counted separately.
  hallucination rate  the share of fields where the truth is null (nothing printed) but a value was returned.
  line item F1        a predicted line counts as correct when its description is close (token set ratio of 85 or
                      more) and its amount matches; precision and recall are over all lines, F1 is their harmonic mean.
  document strict     every core field correct, no invented value, and every line item right.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from rapidfuzz import fuzz

from docintel.extraction.normalize import normalize_invoice_number, normalize_name

CORE = ("invoice_number", "invoice_date", "due_date", "supplier_name", "customer_name", "subtotal", "tax", "total", "currency")
HEADLINE = ("invoice_number", "invoice_date", "supplier_name", "customer_name", "subtotal", "tax", "total", "currency")


def _norm_text(s: str | None) -> str:
    return " ".join((s or "").casefold().split())


def field_equal(name: str, pred: Any, truth: Any) -> bool:
    if truth is None or pred is None:
        return truth is None and pred is None
    if name in ("subtotal", "tax", "total", "discount", "shipping"):
        return abs(float(pred) - float(truth)) <= 0.011
    if name == "invoice_number":
        return normalize_invoice_number(str(pred)) == normalize_invoice_number(str(truth))
    if name in ("supplier_name", "customer_name"):
        a, b = normalize_name(str(pred)), normalize_name(str(truth))
        return a == b
    return _norm_text(str(pred)) == _norm_text(str(truth))


def field_close(name: str, pred: Any, truth: Any) -> bool:
    """Like field_equal but names may differ slightly (OCR slips)."""
    if field_equal(name, pred, truth):
        return True
    if name in ("supplier_name", "customer_name") and pred and truth:
        return fuzz.token_set_ratio(normalize_name(str(pred)), normalize_name(str(truth))) >= 90
    if name == "invoice_number" and pred and truth:
        return fuzz.ratio(normalize_invoice_number(str(pred)), normalize_invoice_number(str(truth))) >= 90
    return False


def match_records(preds: list[dict], truths: list[dict]) -> list[tuple[dict | None, dict | None]]:
    """Pair predicted records with true ones: by invoice number first, then by position."""
    pairs: list[tuple[dict | None, dict | None]] = []
    left_p = list(range(len(preds)))
    left_t = list(range(len(truths)))
    for ti in list(left_t):
        key = normalize_invoice_number(truths[ti].get("invoice_number"))
        if not key:
            continue
        for pi in left_p:
            pk = normalize_invoice_number(preds[pi].get("invoice_number"))
            if pk and pk == key:
                pairs.append((preds[pi], truths[ti]))
                left_p.remove(pi)
                left_t.remove(ti)
                break
    # remaining by order (only when counts allow a sensible guess)
    for ti in list(left_t):
        if left_p:
            pi = left_p.pop(0)
            left_t.remove(ti)
            pairs.append((preds[pi], truths[ti]))
    for ti in left_t:
        pairs.append((None, truths[ti]))
    for pi in left_p:
        pairs.append((preds[pi], None))
    return pairs


def line_item_counts(pred_items: list[dict], true_items: list[dict]) -> tuple[int, int, int]:
    """(true positives, predicted, truth). Greedy one-to-one matching on description and amount."""
    used: set[int] = set()
    tp = 0
    for p in pred_items:
        best = None
        for j, t in enumerate(true_items):
            if j in used:
                continue
            desc_ok = fuzz.token_set_ratio(_norm_text(p.get("description")), _norm_text(t.get("description"))) >= 85
            amt_ok = (p.get("amount") is not None and t.get("amount") is not None and abs(p["amount"] - t["amount"]) <= 0.011) or (p.get("amount") is None and t.get("amount") is None)
            if desc_ok and amt_ok:
                best = j
                break
        if best is not None:
            used.add(best)
            tp += 1
    return tp, len(pred_items), len(true_items)


def line_qty_unit(pred_items: list[dict], true_items: list[dict]) -> tuple[int, int]:
    """Among lines matched on description and amount: (quantity and unit price values that are right, values checked)."""
    used: set[int] = set()
    ok = checked = 0
    for p in pred_items:
        for j, t in enumerate(true_items):
            if j in used:
                continue
            desc_ok = fuzz.token_set_ratio(_norm_text(p.get("description")), _norm_text(t.get("description"))) >= 85
            amt_ok = (p.get("amount") is not None and t.get("amount") is not None and abs(p["amount"] - t["amount"]) <= 0.011) or (p.get("amount") is None and t.get("amount") is None)
            if desc_ok and amt_ok:
                used.add(j)
                for k in ("quantity", "unit_price"):
                    if t.get(k) is not None:
                        checked += 1
                        pv = p.get(k)
                        ok += pv is not None and abs(pv - t[k]) <= 0.011
                break
    return ok, checked


def score_record(pred: dict | None, truth: dict) -> dict:
    """Per field outcome for one true record: right | wrong | missed | (hallucinated is tracked when truth is null)."""
    out: dict[str, Any] = {"fields": {}, "lines": (0, 0, len(truth.get("line_items") or []))}
    if pred is None:
        for f in CORE:
            out["fields"][f] = "missing_record" if truth.get(f) is not None else "ok_null"
        out["strict"] = False
        return out
    strict = True
    for f in CORE:
        t, p = truth.get(f), pred.get(f)
        if t is None:
            out["fields"][f] = "hallucinated" if p is not None else "ok_null"
            if p is not None and f in HEADLINE:
                strict = False
        else:
            if field_equal(f, p, t):
                out["fields"][f] = "right"
            elif p is None:
                out["fields"][f] = "missed"
                if f in HEADLINE:
                    strict = False
            else:
                out["fields"][f] = "wrong"
                if f in HEADLINE:
                    strict = False
    tp, npred, ntrue = line_item_counts(pred.get("line_items") or [], truth.get("line_items") or [])
    out["lines"] = (tp, npred, ntrue)
    out["qu"] = line_qty_unit(pred.get("line_items") or [], truth.get("line_items") or [])
    if npred != ntrue or tp != ntrue or out["qu"][0] != out["qu"][1]:
        strict = False
    out["strict"] = strict
    return out


def aggregate(scored: list[dict]) -> dict:
    """Combine score_record outputs into field accuracy, hallucination rate, line item F1 and strict accuracy."""
    right: dict[str, int] = defaultdict(int)
    printed: dict[str, int] = defaultdict(int)
    halluc: dict[str, int] = defaultdict(int)
    unprinted: dict[str, int] = defaultdict(int)
    tp = npred = ntrue = 0
    qu_ok = qu_all = 0
    strict = 0
    for s in scored:
        for f, o in s["fields"].items():
            if o in ("right", "wrong", "missed", "missing_record"):
                printed[f] += 1
                right[f] += o == "right"
            else:
                unprinted[f] += 1
                halluc[f] += o == "hallucinated"
        a, b, c = s["lines"]
        tp, npred, ntrue = tp + a, npred + b, ntrue + c
        qu_ok, qu_all = qu_ok + s.get("qu", (0, 0))[0], qu_all + s.get("qu", (0, 0))[1]
        strict += bool(s["strict"])
    acc = {f: round(right[f] / printed[f], 4) for f in CORE if printed[f]}
    head = [f for f in HEADLINE if printed[f]]
    macro = round(sum(acc[f] for f in head) / len(head), 4) if head else None
    micro = round(sum(right[f] for f in HEADLINE) / max(1, sum(printed[f] for f in HEADLINE)), 4)
    h_total = sum(unprinted[f] for f in HEADLINE)
    h_bad = sum(halluc[f] for f in HEADLINE)
    prec = tp / npred if npred else 0.0
    rec = tp / ntrue if ntrue else 0.0
    f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
    no_lines = ntrue == 0 and npred == 0  # ledgers have no line items: report n/a instead of a misleading 0
    return {
        "records": len(scored),
        "field_accuracy": acc,
        "field_accuracy_micro": micro,
        "field_accuracy_macro": macro,
        "hallucination_rate": round(h_bad / h_total, 4) if h_total else None,
        "hallucinated_values": h_bad,
        "unprinted_fields_checked": h_total,
        "line_item_precision": None if no_lines else round(prec, 4),
        "line_item_recall": None if no_lines else round(rec, 4),
        "line_item_f1": None if no_lines else round(f1, 4),
        "line_qty_unit_accuracy": round(qu_ok / qu_all, 4) if qu_all else None,
        "document_strict_accuracy": round(strict / len(scored), 4) if scored else None,
    }
