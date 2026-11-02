"""Pulling the fields out of the text: layouts, missing values, receipts, multi-invoice files."""

from __future__ import annotations

import random

import pytest

from docintel.extraction.document import PageText, extract_document
from docintel.pipeline.process import process_file
from evaluation.extract_run import record_to_dict
from evaluation.metrics import HEADLINE, score_record
from evaluation.synth import faults as fl
from evaluation.synth.holdout import HOLDOUT_TEMPLATES
from evaluation.synth.templates import cur_printed, render_pdf
from evaluation.synth.data import truth_record

SIMPLE = """INVOICE
Bill No.: B-17
M/s   Nimbus Hardware LLC
Date: 2026-03-27

Description        Qty     Price     Amount
Bolts 10mm           2      5.00      10.00
Washers              4      2.50      10.00

Sub Total                            20.00
Total                                20.00
"""


@pytest.mark.parametrize("template", ["T1", "T2", "T3", "T4", "T5", "T6"])
def test_every_development_layout_is_read_correctly(make, settings, template):
    for seed in (21, 22, 23):
        pdf, truth = make.pdf(f"{template}-{seed}.pdf", seed=seed, template=template)
        res = process_file(pdf, settings)
        assert len(res.records) == 1
        sc = score_record(record_to_dict(res.records[0]), truth[0])
        bad = {f: o for f, o in sc["fields"].items() if f in HEADLINE and o not in ("right", "ok_null")}
        assert not bad, (template, seed, bad)
        assert sc["lines"][0] == sc["lines"][2], (template, seed, "line items", sc["lines"])


@pytest.mark.parametrize("template", sorted(HOLDOUT_TEMPLATES))
def test_other_fonts_and_wordings_still_read_the_headline_fields(make, settings, template):
    """The layouts written after the extractor was frozen. Receipts and boxed bills used to fail here."""
    rng = random.Random(40)
    inv = make.invoice(41, n_items=5)
    path = make.dir / f"{template}.pdf"
    infos = render_pdf(str(path), [inv], template, rng, HOLDOUT_TEMPLATES)
    truth = truth_record(inv, infos[0]["prints"], currency_printed=cur_printed(infos[0]["look"]))
    rec = process_file(path, settings).records[0]
    sc = score_record(record_to_dict(rec), truth)
    bad = {f: o for f, o in sc["fields"].items() if f in HEADLINE and o not in ("right", "ok_null")}
    assert not bad, bad
    assert sc["lines"][0] == sc["lines"][2] and sc["qu"][0] == sc["qu"][1]


def test_missing_invoice_number_stays_empty_and_is_reported(make, settings):
    inv = fl.faulty_invoice(random.Random(3), "missing_number", n_items=3)
    pdf, _ = make.pdf("nonum.pdf", invoices=[inv])
    res = process_file(pdf, settings)
    assert res.records[0].value("invoice_number") is None
    assert any(i.code == "MISSING_FIELD" and i.field == "invoice_number" for i in res.issues[0])


def test_a_missing_date_is_not_replaced_by_another_date(make, settings):
    inv = fl.faulty_invoice(random.Random(4), "missing_date", n_items=3)
    pdf, _ = make.pdf("nodate.pdf", invoices=[inv])
    rec = process_file(pdf, settings).records[0]
    assert rec.value("invoice_date") is None


def test_text_that_is_not_an_invoice_gives_no_record(settings):
    letter = "Dear Mr Rahman,\nThank you for your letter of 3 March. We will reply shortly.\nKind regards,\nAccounts"
    out = extract_document([PageText(1, letter)], None, settings)
    assert out.records == []


def test_m_slash_s_is_the_customer(settings):
    rec = extract_document([PageText(1, SIMPLE)], None, settings).records[0]
    assert rec.value("customer_name") == "Nimbus Hardware LLC"
    assert rec.value("invoice_number") == "B-17"
    assert rec.value("total") == 20.0
    assert [(li.quantity, li.unit_price, li.amount) for li in rec.line_items] == [(2.0, 5.0, 10.0), (4.0, 2.5, 10.0)]


def test_tax_is_not_invented_when_the_document_prints_none(settings):
    rec = extract_document([PageText(1, SIMPLE)], None, settings).records[0]
    assert rec.value("tax") is None
    assert rec.value("currency") is None


def test_receipt_with_quantity_on_the_second_line(settings):
    text = """RECEIPT
Receipt No: R-90
Date: 2026-02-14
Fresh vegetables assorted crate
   10 x 22.76                        227.60
Sugar 50kg
   4 x 35.55                         142.20
-------------------------
Sub-total                            369.80
TOTAL                                369.80
"""
    rec = extract_document([PageText(1, text)], None, settings).records[0]
    assert [(li.quantity, li.unit_price, li.amount) for li in rec.line_items] == [(10.0, 22.76, 227.6), (4.0, 35.55, 142.2)]


def test_table_numbers_are_read_in_order_when_headers_are_misaligned(settings):
    text = """BILL
Bill No. A-1     Bill Date 2026-03-01
S/N  Particulars                      Qty     Price     Total
1    Paint 20L bucket                      6     39,72    238,32
2    Cement bag                            1      5,46      5,46
                                   Sub Total              243,78
                                   Total                  243,78
"""
    rec = extract_document([PageText(1, text)], None, settings).records[0]
    assert [(li.quantity, li.unit_price, li.amount) for li in rec.line_items] == [(6.0, 39.72, 238.32), (1.0, 5.46, 5.46)]


def test_european_number_style_is_kept_per_document(settings):
    rec = extract_document([PageText(1, SIMPLE.replace("20.00", "1.020,50").replace("10.00", "510,25").replace("5.00", "255,12").replace("2.50", "127,56"))], None, settings).records[0]
    assert rec.value("total") == pytest.approx(1020.5)


def test_ambiguous_numeric_date_lowers_confidence(settings):
    text = SIMPLE.replace("2026-03-27", "03/04/2026")
    rec = extract_document([PageText(1, text)], None, settings).records[0]
    assert rec.conf("invoice_date") < 0.9


@pytest.mark.parametrize(
    "raw, expected",
    [("INV 1234", "INV 1234"), ("INV-2025-0042", "INV-2025-0042"), ("No 1234", "1234"), ("Date 25/07/2025", None), ("25/07/2025", None), ("1,234.50", None), ("Page 2", None), ("", None)],
)
def test_invoice_number_cleaning_never_keeps_a_label_word_or_a_date(raw, expected):
    from docintel.extraction.rules import clean_invoice_number

    assert clean_invoice_number(raw) == expected
