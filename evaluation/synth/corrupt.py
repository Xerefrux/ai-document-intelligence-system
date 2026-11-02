"""Broken, hostile and unusual files, each with the outcome the system should reach.

Outcome vocabulary:
  rejected:<CODE>    refused at upload, nothing queued
  failed:<CODE>      accepted (the header looks right), then processing fails cleanly with this code
  empty              accepted and processed, but no invoice is found (completed, flagged for review)
"""

from __future__ import annotations

import io
import os
import random
import struct
import zipfile
from pathlib import Path

from PIL import Image
from reportlab.lib.pagesizes import A4
from reportlab.lib import pdfencrypt
from reportlab.pdfgen import canvas

from .data import make_invoice
from .templates import render_pdf


def _write(path: Path, data: bytes) -> None:
    path.write_bytes(data)


def build_corrupt(out_dir: Path, rng: random.Random) -> list[dict]:
    out_dir.mkdir(parents=True, exist_ok=True)
    entries: list[dict] = []

    def add(name: str, expect: str, note: str) -> Path:
        entries.append({"id": f"corrupt-{Path(name).stem}-{Path(name).suffix.lstrip('.')}", "file": f"files/{name}", "group": "corrupt", "expect": expect, "note": note, "records": []})
        return out_dir / name

    # a valid PDF to damage
    good = out_dir / "_good.pdf"
    render_pdf(str(good), [make_invoice(rng, n_items=6)], "T1", rng)
    data = good.read_bytes()
    good.unlink()

    _write(add("truncated.pdf", "failed:CORRUPT_FILE", "PDF cut off at 25 percent, so objects and the cross reference table are gone"), data[: int(len(data) * 0.25)])
    mid = bytearray(data)
    for i in range(len(mid) // 3, len(mid) * 2 // 3):
        mid[i] = rng.randrange(256)
    _write(add("scrambled_body.pdf", "failed:CORRUPT_FILE", "PDF header and trailer intact, the middle overwritten with random bytes"), bytes(mid))
    _write(add("random_bytes.pdf", "rejected:UNSUPPORTED_TYPE", "random bytes with a .pdf name"), bytes(rng.randrange(256) for _ in range(4096)))
    _write(add("zero_bytes.pdf", "rejected:EMPTY_FILE", "empty file"), b"")
    _write(add("program_named_pdf.pdf", "rejected:UNSUPPORTED_TYPE", "a Windows executable header renamed to .pdf"), b"MZ\x90\x00\x03\x00\x00\x00" + bytes(rng.randrange(256) for _ in range(2048)))

    enc = pdfencrypt.StandardEncryption("open-sesame", ownerPassword="owner-secret", canPrint=1)
    c = canvas.Canvas(str(out_dir / "locked.pdf"), pagesize=A4, encrypt=enc)
    c.drawString(72, 700, "Invoice No: LOCK-0001   Total: 100.00")
    c.showPage()
    c.save()
    add("locked.pdf", "failed:PASSWORD_PROTECTED", "PDF that needs a password to open")

    # spreadsheets
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Invoice No", "Date", "Total"])
    for i in range(30):
        ws.append([f"X-{i}", "2026-03-14", 100 + i])
    buf = io.BytesIO()
    wb.save(buf)
    x = buf.getvalue()
    _write(add("truncated.xlsx", "failed:CORRUPT_FILE", "workbook zip cut at 50 percent"), x[: len(x) // 2])
    _write(add("garbage.csv", "rejected:UNSUPPORTED_TYPE", "binary data with a .csv name"), bytes(rng.randrange(256) for _ in range(3000)) + b"\x00\x00")
    _write(add("headers_only.csv", "empty", "a ledger header and no rows"), b"Invoice No,Date,Supplier,Total\r\n")

    # images
    im = Image.new("RGB", (900, 1200), "white")
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=70)
    _write(add("blank_page.jpg", "empty", "a white page, nothing to read"), buf.getvalue())
    pdf_data = data
    # a real scan, then damaged
    from .degrade import degrade, rasterize
    tmp = out_dir / "_s.pdf"
    tmp.write_bytes(pdf_data)
    page = rasterize(tmp, 150)[0]
    tmp.unlink()
    buf = io.BytesIO()
    page.save(buf, "JPEG", quality=80)
    jpg = buf.getvalue()
    _write(add("truncated.jpg", "failed:CORRUPT_FILE", "JPEG cut at 35 percent"), jpg[: int(len(jpg) * 0.35)])
    buf = io.BytesIO()
    page.save(buf, "PNG")
    png = bytearray(buf.getvalue())
    for i in range(len(png) // 2, len(png) // 2 + 400):
        png[i] = rng.randrange(256)
    _write(add("damaged.png", "failed:CORRUPT_FILE", "PNG with bytes overwritten in the image data"), bytes(png))

    # an enormous canvas that compresses to almost nothing (decompression bomb)
    big = Image.new("L", (30000, 30000), 255)
    buf = io.BytesIO()
    big.save(buf, "PNG", optimize=False, compress_level=9)
    _write(add("huge_canvas.png", "failed:IMAGE_TOO_LARGE", "30000 by 30000 pixel image: small on disk, 900 megapixels once decoded"), buf.getvalue())

    # a perfectly valid PDF that is not an invoice
    c = canvas.Canvas(str(out_dir / "letter.pdf"), pagesize=A4)
    y = 780
    for line in ["Dear Ms Rahman,", "", "Thank you for visiting our office last week. We enjoyed discussing the new", "warehouse layout and the delivery schedule for next quarter.", "", "Kind regards,", "Operations team"]:
        c.drawString(72, y, line)
        y -= 16
    c.showPage()
    c.save()
    add("letter.pdf", "empty", "a normal letter, no invoice in it")
    return entries
