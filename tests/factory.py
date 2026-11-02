"""Builds invoice-like sample files for the tests, using the same generator as the evaluation set."""

from __future__ import annotations

import csv
import io
import random
import zipfile
from pathlib import Path

import openpyxl
from PIL import Image, ImageDraw, ImageFont

from evaluation.build import random_invoice
from evaluation.synth import degrade as dg
from evaluation.synth.data import Invoice, truth_record
from evaluation.synth.templates import DEV_TEMPLATES, cur_printed, render_pdf


class Factory:
    def __init__(self, folder: Path):
        self.dir = folder
        folder.mkdir(parents=True, exist_ok=True)

    def invoice(self, seed: int = 1, **kw) -> Invoice:
        kw.setdefault("n_items", 4)
        return random_invoice(random.Random(seed), **kw)

    def pdf(self, name: str = "invoice.pdf", *, seed: int = 1, template: str = "T1", invoices: list[Invoice] | None = None, **kw) -> tuple[Path, list[dict]]:
        """A digital invoice PDF and the ground truth for it."""
        invs = invoices or [self.invoice(seed, **kw)]
        path = self.dir / name
        infos = render_pdf(str(path), invs, template, random.Random(seed), DEV_TEMPLATES)
        truth = [truth_record(i, inf["prints"], currency_printed=cur_printed(inf["look"])) for i, inf in zip(invs, infos)]
        return path, truth

    def scan(self, name: str = "scan.png", *, seed: int = 1, template: str = "T1", level: str = "light", **kw) -> tuple[Path, list[dict]]:
        """A scanned invoice: the same PDF drawn, turned into pixels and degraded like a photocopy."""
        src, truth = self.pdf(f"_{name}.pdf", seed=seed, template=template, **kw)
        rng = random.Random(seed)
        pages = [dg.degrade(im, rng, level) for im in dg.rasterize(src, dg.scan_dpi(rng, level))]
        path = self.dir / name
        pages[0].save(path)
        src.unlink()
        return path, truth

    def blank_image(self, name: str = "blank.png", size=(600, 800)) -> Path:
        path = self.dir / name
        Image.new("RGB", size, "white").save(path)
        return path

    def text_image(self, text: str, name: str = "text.png") -> Path:
        im = Image.new("RGB", (1400, 400), "white")
        d = ImageDraw.Draw(im)
        try:
            font = ImageFont.truetype("DejaVuSans.ttf", 56)
        except OSError:
            font = ImageFont.load_default()
        d.text((40, 120), text, fill="black", font=font)
        path = self.dir / name
        im.save(path)
        return path

    def xlsx(self, name: str, sheets: dict[str, list[list]], hidden: tuple[str, ...] = ()) -> Path:
        wb = openpyxl.Workbook()
        wb.remove(wb.active)
        for title, rows in sheets.items():
            ws = wb.create_sheet(title)
            for r in rows:
                ws.append(r)
            if title in hidden:
                ws.sheet_state = "hidden"
        path = self.dir / name
        wb.save(path)
        return path

    def csv(self, name: str, rows: list[list], *, delimiter: str = ",", encoding: str = "utf-8") -> Path:
        buf = io.StringIO()
        csv.writer(buf, delimiter=delimiter).writerows(rows)
        path = self.dir / name
        path.write_bytes(buf.getvalue().encode(encoding))
        return path

    def raw(self, name: str, data: bytes) -> Path:
        path = self.dir / name
        path.write_bytes(data)
        return path

    def truncated(self, src: Path, name: str, fraction: float = 0.5) -> Path:
        data = src.read_bytes()
        return self.raw(name, data[: max(16, int(len(data) * fraction))])

    def zip_bomb_xlsx(self, name: str = "bomb.xlsx", megabytes: int = 40) -> Path:
        """A real xlsx whose inner file expands to far more than it takes on disk."""
        base = self.xlsx("_b.xlsx", {"S": [["a", 1]]})
        path = self.dir / name
        with zipfile.ZipFile(base) as zin, zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zout:
            for item in zin.infolist():
                zout.writestr(item, zin.read(item.filename))
            zout.writestr("xl/junk.bin", b"\0" * (megabytes * 1024 * 1024))
        return path


LEDGER_HEADER = ["Invoice No", "Invoice Date", "Supplier", "Customer", "Currency", "Subtotal", "Tax", "Total"]
