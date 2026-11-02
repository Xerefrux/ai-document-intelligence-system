"""Numbers, dates, currencies and names written in the many ways invoices print them."""

from __future__ import annotations

from datetime import date

import pytest

from docintel.extraction.normalize import (
    detect_currency,
    detect_decimal_style,
    find_dates,
    infer_date_order,
    normalize_invoice_number,
    normalize_name,
    parse_amount,
)


@pytest.mark.parametrize(
    "text, expected",
    [
        ("1,234.56", 1234.56),
        ("1234.56", 1234.56),
        ("1.234,56", 1234.56),
        ("1 234,56", 1234.56),
        ("1,23,456.78", 123456.78),  # lakh grouping used in Bangladesh and India
        ("(45.00)", -45.0),
        ("-45.00", -45.0),
        ("45.00-", -45.0),
        ("৳5,000/-", 5000.0),
        ("$1,234.50", 1234.5),
        ("EUR 99,90", 99.9),
        ("0.00", 0.0),
    ],
)
def test_amounts(text, expected):
    m = parse_amount(text)
    assert m is not None, text
    assert m.as_float() == pytest.approx(expected)


@pytest.mark.parametrize("text", ["", "abc", "12 apples", "INV-2025", "--", "1.2.3.4"])
def test_not_an_amount(text):
    assert parse_amount(text) is None


def test_currency_symbols_and_codes():
    assert parse_amount("৳5,000/-").currency == "BDT"
    assert parse_amount("€ 12.00").currency == "EUR"
    assert parse_amount("£7.50").currency == "GBP"
    assert parse_amount("EUR 99,90").currency == "EUR"


def test_percent_is_marked_so_it_is_not_taken_for_money():
    m = parse_amount("15%")
    assert m is None or m.percent


def test_decimal_style_follows_the_unambiguous_amounts():
    assert detect_decimal_style("Total 1,234.56 and 99.00") == "."
    assert detect_decimal_style("Total 1.234,56 und 99,00") == ","
    assert detect_decimal_style("no amounts here") is None


def test_dates_iso_and_words():
    assert find_dates("Invoice date: 2026-04-03")[0].value == date(2026, 4, 3)
    assert find_dates("Issued 3 April 2026")[0].value == date(2026, 4, 3)
    assert find_dates("April 3, 2026")[0].value == date(2026, 4, 3)
    assert find_dates("03-Apr-2026")[0].value == date(2026, 4, 3)


def test_numeric_date_is_ambiguous_until_the_order_is_known():
    d = find_dates("03/04/2026")[0]
    assert d.ambiguous and {d.value, d.alt} == {date(2026, 4, 3), date(2026, 3, 4)}
    assert find_dates("03/04/2026", "dmy")[0].value == date(2026, 4, 3)
    assert find_dates("03/04/2026", "mdy")[0].value == date(2026, 3, 4)
    assert not find_dates("03/04/2026", "dmy")[0].ambiguous


def test_a_day_above_twelve_settles_the_order():
    assert not find_dates("27/04/2026")[0].ambiguous
    assert find_dates("27/04/2026")[0].value == date(2026, 4, 27)
    assert infer_date_order("27/04/2026 and 03/04/2026") == "dmy"
    assert infer_date_order("04/27/2026") == "mdy"
    assert infer_date_order("03/04/2026") is None


def test_impossible_dates_are_not_returned():
    assert find_dates("31/02/2026", "dmy") == []
    assert find_dates("2026-13-45") == []


def test_currency_from_a_printed_code_beats_a_symbol():
    code, evidence, conf = detect_currency("Currency: BDT\nTotal 1,200.00")
    assert code == "BDT" and conf >= 0.9
    assert detect_currency("Amounts shown in EUR")[0] == "EUR"
    assert detect_currency("nothing about money here")[0] is None


def test_names_compare_without_legal_suffixes():
    assert normalize_name("Shapla Traders Ltd.") == normalize_name("SHAPLA TRADERS LIMITED")
    assert normalize_name("Karnaphuli Foods Pvt Ltd") != normalize_name("Karnaphuli Textiles Pvt Ltd")
    assert normalize_name(None) == ""


def test_invoice_numbers_compare_without_punctuation():
    assert normalize_invoice_number("inv 2026/00417") == normalize_invoice_number("INV-2026-00417")
    assert normalize_invoice_number(None) == ""
