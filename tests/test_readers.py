"""Reading files: type detection, PDF text layers, OCR, spreadsheets, and every way a file can be broken."""

from __future__ import annotations

import random

import pytest
from reportlab.lib.pdfencrypt import StandardEncryption
from reportlab.pdfgen import canvas

from docintel.pipeline.detect import sniff
from docintel.pipeline.process import process_file
from docintel.pipeline.types import CorruptFileError, DocIntelError, LimitExceededError, PasswordProtectedError, UnsupportedFileError
from tests.conftest import make_settings, needs_tesseract
from tests.factory import LEDGER_HEADER


# ---------------------------------------------------------------------------- type detection


def test_content_decides_not_the_name(make):
    pdf, _ = make.pdf("a.pdf")
    renamed = make.raw("report.csv", pdf.read_bytes())
    s = sniff(renamed)
    assert s.file_type == "pdf"  # the file is a PDF whatever its name says
    assert s.warnings and "content is pdf" in s.warnings[0]


@pytest.mark.parametrize(
    "name, data, code",
    [
        ("virus.pdf", b"MZ\x90\x00" + b"\x00" * 200, "UNSUPPORTED_TYPE"),
        ("page.pdf", b"<!DOCTYPE html><html></html>", "UNSUPPORTED_TYPE"),
        ("empty.pdf", b"", "EMPTY_FILE"),
        ("notes.txt", b"hello", "UNSUPPORTED_TYPE"),
        ("noext", b"%PDF-1.4 hello", "UNSUPPORTED_TYPE"),
        ("random.pdf", bytes(random.Random(1).randrange(256) for _ in range(4000)), "UNSUPPORTED_TYPE"),
        ("archive.xlsx", b"PK\x03\x04" + b"\x00" * 50, None),  # a zip header with a damaged body: the reader reports it
    ],
)
def test_unsupported_content_is_rejected(make, name, data, code):
    s = sniff(make.raw(name, data))
    if code:
        assert s.file_type is None and s.code == code
    else:
        assert s.file_type == "xlsx"


# ---------------------------------------------------------------------------- PDF and images


def test_pdf_text_layer_is_read_without_ocr(make, settings):
    pdf, truth = make.pdf("t.pdf", seed=3, template="T1")
    res = process_file(pdf, settings)
    assert res.file_type == "pdf"
    assert all(p.source == "text" for p in res.pages)
    assert truth[0]["invoice_number"] in res.pages[0].text
    assert res.records[0].value("invoice_number") == truth[0]["invoice_number"]
    assert res.records[0].value("total") == pytest.approx(truth[0]["total"])


def test_multi_page_pdf_keeps_every_page_and_line_item(make, settings):
    inv = make.invoice(7, n_items=45)
    pdf, truth = make.pdf("long.pdf", invoices=[inv], template="T1")
    res = process_file(pdf, settings)
    assert len(res.pages) >= 2
    assert len(res.records) == 1
    assert len(res.records[0].line_items) == len(truth[0]["line_items"]) == 45


def test_several_invoices_in_one_pdf_become_several_records(make, settings):
    invs = [make.invoice(s) for s in (11, 12, 13)]
    pdf, truth = make.pdf("multi.pdf", invoices=invs, template="T2")
    res = process_file(pdf, settings)
    assert [r.value("invoice_number") for r in res.records] == [t["invoice_number"] for t in truth]


@needs_tesseract
def test_scanned_invoice_is_read_with_ocr(make, settings):
    img, truth = make.scan("scan.png", seed=5, template="T1", level="light")
    res = process_file(img, settings)
    assert res.pages[0].source == "ocr"
    assert res.pages[0].ocr_confidence and res.pages[0].ocr_confidence > 0.5
    assert res.records and res.records[0].value("invoice_number") == truth[0]["invoice_number"]
    assert res.records[0].value("total") == pytest.approx(truth[0]["total"], abs=0.01)


@needs_tesseract
def test_blank_and_unreadable_images_produce_no_invented_invoice(make, settings):
    res = process_file(make.blank_image(), settings)
    assert res.records == []


@needs_tesseract
def test_text_in_an_image_is_found(make, settings):
    res = process_file(make.text_image("Invoice No INV-2026-0042   Total 1,250.00"), settings)
    assert "INV-2026-0042" in res.pages[0].text.replace(" ", "") or "0042" in res.pages[0].text


def test_a_renamed_png_is_processed_as_an_image(make, settings):
    png = make.blank_image("x.png")
    renamed = make.raw("scan.pdf", png.read_bytes())
    res = process_file(renamed, settings) if sniff(renamed).file_type else None
    assert res is not None and res.file_type == "image"


# ---------------------------------------------------------------------------- spreadsheets


def _ledger_rows(n=5):
    rows = [LEDGER_HEADER]
    for i in range(n):
        rows.append([f"INV-{100 + i}", f"2026-03-{10 + i:02d}", f"Supplier {i}", "Customer", "USD", 100.0 + i, 10.0, 110.0 + i])
    rows.append(["Total", None, None, None, None, 510.0, 50.0, 560.0])
    return rows


def test_ledger_gives_one_record_per_row_and_skips_the_total_row(make, settings):
    res = process_file(make.xlsx("l.xlsx", {"Register": _ledger_rows(5)}), settings)
    assert [r.value("invoice_number") for r in res.records] == [f"INV-{100 + i}" for i in range(5)]
    assert all(r.kind == "ledger_row" for r in res.records)


def test_hidden_sheets_are_ignored_and_all_visible_sheets_are_read(make, settings):
    path = make.xlsx("m.xlsx", {"A": _ledger_rows(2), "B": _ledger_rows(3), "Secret": _ledger_rows(4)}, hidden=("Secret",))
    res = process_file(path, settings)
    assert len(res.records) == 5


@pytest.mark.parametrize("encoding, delimiter", [("utf-8", ","), ("utf-8", ";"), ("utf-16", ","), ("latin-1", ";")])
def test_csv_encodings_and_delimiters(make, settings, encoding, delimiter):
    rows = _ledger_rows(3)
    rows[1][2] = "Café Supplier"
    res = process_file(make.csv("l.csv", rows, delimiter=delimiter, encoding=encoding), settings)
    assert [r.value("invoice_number") for r in res.records] == ["INV-100", "INV-101", "INV-102"]
    assert res.records[0].value("supplier_name") == "Café Supplier"


def test_csv_with_only_headers_has_no_records(make, settings):
    res = process_file(make.csv("h.csv", [LEDGER_HEADER]), settings)
    assert res.records == []


def test_spreadsheet_text_dates_and_european_numbers(make, settings):
    rows = [LEDGER_HEADER, ["A-1", "27/03/2026", "Shapla", "Karim", "EUR", "1.000,50", "100,05", "1.100,55"]]
    res = process_file(make.csv("eu.csv", rows, delimiter=";"), settings)
    r = res.records[0]
    assert r.value("total") == pytest.approx(1100.55)
    assert r.value("invoice_date") == "2026-03-27"


# ---------------------------------------------------------------------------- broken files


def test_truncated_pdf_fails_with_a_clear_code(make, settings):
    pdf, _ = make.pdf("ok.pdf")
    with pytest.raises(CorruptFileError) as e:
        process_file(make.truncated(pdf, "cut.pdf", 0.4), settings)
    assert e.value.code == "CORRUPT_FILE" and not e.value.retryable


def test_password_protected_pdf(make, settings):
    path = make.dir / "locked.pdf"
    c = canvas.Canvas(str(path), encrypt=StandardEncryption("secret", canPrint=1))
    c.drawString(72, 700, "Invoice No 123")
    c.save()
    with pytest.raises(PasswordProtectedError) as e:
        process_file(path, settings)
    assert e.value.code == "PASSWORD_PROTECTED"


def test_truncated_workbook_and_image(make, settings):
    x = make.xlsx("w.xlsx", {"S": _ledger_rows(20)})
    with pytest.raises(DocIntelError) as e:
        process_file(make.truncated(x, "cut.xlsx", 0.5), settings)
    assert e.value.code in ("CORRUPT_FILE", "UNSUPPORTED_TYPE")
    img = make.blank_image("i.png")
    with pytest.raises(DocIntelError):
        process_file(make.truncated(img, "cut.png", 0.3), settings)


def test_zip_bomb_workbook_is_refused_before_it_is_unpacked(make, tmp_path):
    s = make_settings(tmp_path, max_zip_uncompressed_mb=10)
    with pytest.raises(LimitExceededError):
        process_file(make.zip_bomb_xlsx(megabytes=30), s)


def test_oversized_image_canvas_is_refused(make, tmp_path):
    s = make_settings(tmp_path, max_image_pixels=1_000_000)
    with pytest.raises(LimitExceededError):
        process_file(make.blank_image("big.png", size=(3000, 3000)), s)


def test_unsupported_content_raises_before_any_reading(make, settings):
    with pytest.raises(UnsupportedFileError):
        process_file(make.raw("x.pdf", b"MZ" + b"\x00" * 100), settings)


# ---------------------------------------------------------------------------- PDFium is not thread safe


def _multi_page_pdf(path, pages: int = 6) -> str:
    c = canvas.Canvas(str(path))
    for p in range(pages):
        for line in range(18):
            c.drawString(60, 780 - line * 14, f"Page {p + 1} line {line + 1}: Invoice No INV-{p:03d}-{line:03d} total 1,234.50 thank you for your business")
        c.showPage()
    c.save()
    return str(path)


def test_many_threads_can_read_and_render_pdfs_at_once(tmp_path):
    """PDFium crashes the whole process when two threads are inside it together, and the workers are threads.

    Before every PDFium call went through one lock, this test (or the three worker test in test_system.py) ended with
    "Fatal Python error: Segmentation fault" on some runs. A crash cannot be caught, so the test simply has to survive.
    """
    import threading

    from docintel.pipeline.ocr import render_pdf_page
    from docintel.pipeline.pdf import PdfReader

    s = make_settings(tmp_path)
    a = _multi_page_pdf(tmp_path / "a.pdf")
    b = _multi_page_pdf(tmp_path / "b.pdf", pages=5)
    errors: list[BaseException] = []

    def read(path: str) -> None:
        try:
            for _ in range(15):
                reader = PdfReader(path, s)
                try:
                    for i in range(reader.page_count):
                        assert reader.try_text_page(i) is not None
                finally:
                    reader.close()
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    def render(path: str, pages: int) -> None:
        try:
            for i in range(30):
                assert render_pdf_page(path, i % pages, 72, 600).shape[2] == 3
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=read, args=(p,)) for p in (a, b, a, b, a)]
    threads += [threading.Thread(target=render, args=(a, 6)), threading.Thread(target=render, args=(b, 5)), threading.Thread(target=render, args=(a, 6))]
    [t.start() for t in threads]
    [t.join(180) for t in threads]
    assert not any(t.is_alive() for t in threads)
    assert not errors, errors[:3]
