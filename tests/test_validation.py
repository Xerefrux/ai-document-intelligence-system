"""The checks that flag a record for a person: missing fields, wrong arithmetic, duplicates, cross-checks."""

from __future__ import annotations

import random

import pytest

from docintel.pipeline.process import process_file
from docintel.validation.reconcile import Side, reconcile, summarize
from docintel.validation.rules import review_needed
from evaluation.synth import faults as fl


def codes(res, i=0):
    return {x.code for x in res.issues[i]}


def test_a_clean_invoice_passes_every_check(make, settings):
    pdf, _ = make.pdf("clean.pdf", seed=9, template="T1", tax_rate=10.0)
    res = process_file(pdf, settings)
    assert not review_needed(res.issues[0]), [(i.code, i.message) for i in res.issues[0]]


@pytest.mark.parametrize("fault", [f for f in fl.FAULTS])
def test_each_injected_fault_is_flagged(make, settings, fault):
    hits = 0
    for seed in range(5):
        inv = fl.faulty_invoice(random.Random(100 + seed), fault, n_items=4)
        pdf, _ = make.pdf(f"{fault}-{seed}.pdf", invoices=[inv], template="T1")
        res = process_file(pdf, settings)
        assert inv.expect_codes <= codes(res), (fault, seed, inv.expect_codes, codes(res))
        assert review_needed(res.issues[0])
        hits += 1
    assert hits == 5


def test_totals_that_do_not_add_up_name_the_numbers(make, settings):
    inv = fl.faulty_invoice(random.Random(5), "total_wrong", n_items=3)
    pdf, _ = make.pdf("tw.pdf", invoices=[inv])
    msg = next(i.message for i in process_file(pdf, settings).issues[0] if i.code == "TOTAL_MISMATCH")
    assert "total" in msg.lower() and any(ch.isdigit() for ch in msg)


def test_rounding_inside_the_tolerance_is_not_an_error(make, tmp_path):
    from tests.conftest import make_settings

    pdf, _ = make.pdf("r.pdf", seed=12, template="T1", tax_rate=7.5)
    loose = make_settings(tmp_path, amount_tolerance=0.05)
    assert "TOTAL_MISMATCH" not in codes(process_file(pdf, loose))


def _side(i, no, total, supplier="Shapla Traders Ltd", date="2026-03-01", subtotal=None, tax=None):
    return Side(id=i, invoice_number=no, supplier_name=supplier, invoice_date=date, total=total, subtotal=subtotal, tax=tax, currency="USD", label=f"f{i}")


def test_cross_check_finds_matches_differences_and_missing_rows():
    left = [_side("a", "INV-1", 100.0), _side("b", "INV-2", 200.0), _side("c", "INV-3", 300.0)]
    right = [_side("x", "inv 1", 100.0), _side("y", "INV-2", 250.0), _side("z", "INV-9", 10.0)]
    items = {it.key: it for it in reconcile(left, right, 0.05)}
    assert items["INV-1"].status == "MATCH"
    assert items["INV-2"].status == "MISMATCH" and items["INV-2"].diffs[0]["field"] == "total"
    assert items["INV-3"].status == "MISSING_RIGHT"
    assert items["INV-9"].status == "MISSING_LEFT"
    s = summarize(list(items.values()))
    assert s["MATCH"] == 1 and s["MISMATCH"] == 1


def test_cross_check_tolerates_small_rounding():
    items = reconcile([_side("a", "INV-1", 100.00)], [_side("b", "INV-1", 100.03)], 0.05)
    assert items[0].status == "MATCH"
