"""Inject known mistakes into an invoice before it is drawn, so the validator has something to find.

The printed document carries the mistake; the ground truth records what is printed, not what
the invoice "should" have said. A correct extractor reads the wrong number faithfully, and the
validation step is what has to notice it.
"""

from __future__ import annotations

import random
from datetime import timedelta

from .data import Invoice, make_invoice, r2

FAULTS = [
    "total_wrong", "line_amount_wrong", "subtotal_wrong", "tax_wrong",
    "missing_number", "missing_date", "due_before_date", "ambiguous_date",
]

# Codes the validator must raise for each fault. Extra codes are allowed (a wrong subtotal also
# unbalances the total), missing ones count against recall.
EXPECT = {
    "total_wrong": {"TOTAL_MISMATCH"},
    "line_amount_wrong": {"LINE_ITEM_MISMATCH", "SUBTOTAL_MISMATCH"},
    "subtotal_wrong": {"SUBTOTAL_MISMATCH", "TOTAL_MISMATCH"},
    "tax_wrong": {"TOTAL_MISMATCH"},
    "missing_number": {"MISSING_FIELD"},
    "missing_date": {"MISSING_FIELD"},
    "due_before_date": {"DUE_BEFORE_INVOICE_DATE"},
    "ambiguous_date": {"DATE_AMBIGUOUS"},
}

# Receipts (T3) print no subtotal block, so only the header faults are meaningful there.
HEADER_ONLY = {"missing_number", "missing_date"}


def faulty_invoice(rng: random.Random, fault: str, **kw) -> Invoice:
    """Build an invoice that already carries `fault`."""
    if fault == "ambiguous_date":
        while True:
            inv = make_invoice(rng, numeric_date_unambiguous=False, **kw)
            if inv.date.day != inv.date.month:
                break
        inv.due_date = None
        inv.p_due = None
        inv.date_style_override = "dmy_slash"
    elif fault == "tax_wrong":
        inv = make_invoice(rng, tax_rate=rng.choice([5.0, 10.0, 15.0, 18.0, 20.0]), **kw)
    else:
        inv = make_invoice(rng, **kw)
    if fault == "total_wrong":
        inv.p_total = r2(inv.total + max(5.0, inv.total * rng.uniform(0.03, 0.12)))
    elif fault == "line_amount_wrong":
        k = rng.randrange(len(inv.items))
        inv.p_item_amounts[k] = r2(inv.p_item_amounts[k] + max(3.0, inv.p_item_amounts[k] * rng.uniform(0.05, 0.3)))
    elif fault == "subtotal_wrong":
        inv.p_subtotal = r2(inv.subtotal + max(5.0, inv.subtotal * rng.uniform(0.03, 0.1)))
    elif fault == "tax_wrong":
        inv.p_tax = r2(inv.tax + max(2.0, inv.tax * rng.uniform(0.2, 0.6)))
    elif fault == "missing_number":
        inv.p_number = None
    elif fault == "missing_date":
        inv.p_date = None
    elif fault == "due_before_date":
        if inv.due_date is None:
            inv.due_date = inv.date + timedelta(days=30)
        inv.p_due = inv.date - timedelta(days=rng.randint(3, 40))
    inv.faults = [fault]
    inv.expect_codes = set(EXPECT[fault])
    return inv
