"""Invoice templates drawn with reportlab. Each template prints the same data in a different layout,
with its own label vocabulary, number format, date format and currency display.

T1..T6 are the development templates (used while building the extractor).
H1..H4 live in holdout.py and were written only after the extractor was frozen.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from datetime import date

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from .data import CURRENCIES, Invoice, fmt_date, fmt_number

W, H = A4


@dataclass
class Look:
    num_style: str
    date_style: str
    cur_mode: str  # code | symbol | none | header


def pick_look(rng: random.Random, inv: Invoice, *, num_style: str | None = None, date_style: str | None = None, cur_modes: tuple[str, ...] = ("code", "symbol", "header", "none")) -> Look:
    sym, style = CURRENCIES[inv.currency]
    style = num_style or style
    modes = [m for m in cur_modes if not (m == "symbol" and ((sym == "$" and inv.currency != "USD") or sym == "Rs."))]
    mode = rng.choice(modes)
    ds = inv.date_style_override or date_style or rng.choice(["dmy_mon", "mon_dmy", "iso", "dmy_slash", "dmy_dot", "dmy_full"])
    return Look(style, ds, mode)


class Pen:
    def __init__(self, c: canvas.Canvas, w: float = W, h: float = H):
        self.c, self.W, self.H = c, w, h

    def t(self, x: float, y: float, s: str, size: float = 10, font: str = "Helvetica", align: str = "l", gray: float = 0.0) -> None:
        c = self.c
        c.setFont(font, size)
        c.setFillGray(gray)
        yy = self.H - y
        if align == "r":
            c.drawRightString(x, yy, s)
        elif align == "c":
            c.drawCentredString(x, yy, s)
        else:
            c.drawString(x, yy, s)

    def line(self, x0: float, y: float, x1: float, w: float = 0.6, gray: float = 0.0) -> None:
        self.c.setLineWidth(w)
        self.c.setStrokeGray(gray)
        self.c.line(x0, self.H - y, x1, self.H - y)

    def vline(self, x: float, y0: float, y1: float, w: float = 0.6) -> None:
        self.c.setLineWidth(w)
        self.c.setStrokeGray(0)
        self.c.line(x, self.H - y0, x, self.H - y1)

    def box(self, x: float, y: float, w: float, h: float, fill: float | None = None, stroke: bool = True) -> None:
        c = self.c
        if fill is not None:
            c.setFillGray(fill)
        c.setStrokeGray(0)
        c.setLineWidth(0.6)
        c.rect(x, self.H - y - h, w, h, stroke=1 if stroke else 0, fill=1 if fill is not None else 0)
        c.setFillGray(0)

    def page(self) -> None:
        self.c.showPage()


def money(inv: Invoice, v: float, look: Look, *, force_plain: bool = False) -> str:
    n = fmt_number(v, look.num_style)
    if force_plain or look.cur_mode in ("none", "header"):
        return n
    sym = CURRENCIES[inv.currency][0]
    if look.cur_mode == "symbol":
        if sym in ("$", "£", "€"):
            return f"{n} {sym}" if look.num_style == "eu" else f"{sym}{n}"
        return f"{sym} {n}"
    return f"{inv.currency} {n}"


def cur_printed(look: Look) -> bool:
    return look.cur_mode != "none"


def paginate(n: int, cap1: int, capn: int, reserve: int = 7) -> list[int]:
    """Rows per page, leaving room for the totals block on the last page."""
    if n <= cap1 - reserve:
        return [n]
    counts, left = [], n
    take = min(cap1, left)
    counts.append(take)
    left -= take
    while left > 0:
        if left <= capn - reserve:
            counts.append(left)
            break
        take = min(capn, left)
        counts.append(take)
        left -= take
    else:
        counts.append(0)
    return counts


def tax_label_text(inv: Invoice, name: str = "Tax") -> str:
    r = inv.tax_rate
    pct = f"{r:g}%"
    return f"{name} ({pct})"


def prints_tax(inv: Invoice, rng_flag: bool) -> bool:
    return inv.tax_rate > 0 or rng_flag


# ---------------------------------------------------------------------------------------------- T1


def render_t1(c: canvas.Canvas, inv: Invoice, rng: random.Random) -> dict:
    p = Pen(c)
    look = pick_look(rng, inv)
    show_zero_tax = rng.random() < 0.5
    pages = paginate(len(inv.items), 24, 36)
    idx = 0
    for pg, n_rows in enumerate(pages):
        if pg == 0:
            p.t(40, 62, inv.supplier.name, 18, "Helvetica-Bold")
            for k, ln in enumerate(inv.supplier.lines):
                p.t(40, 78 + 12 * k, ln, 9)
            p.t(40, 78 + 12 * len(inv.supplier.lines), inv.supplier.email, 9, gray=0.3)
            p.t(W - 40, 62, "INVOICE", 22, "Helvetica-Bold", "r")
            p.t(W - 40, 84, f"Invoice No: {inv.p_number or ''}" if inv.p_number else "Invoice No:", 10, "Helvetica", "r")
            if inv.p_date:
                p.t(W - 40, 98, f"Invoice Date: {fmt_date(inv.p_date, look.date_style)}", 10, "Helvetica", "r")
            yy = 112
            if inv.p_due:
                p.t(W - 40, yy, f"Due Date: {fmt_date(inv.p_due, look.date_style)}", 10, "Helvetica", "r")
                yy += 14
            if inv.po:
                p.t(W - 40, yy, f"PO Number: {inv.po}", 10, "Helvetica", "r")
            p.t(40, 150, "Bill To:", 10, "Helvetica-Bold")
            p.t(40, 164, inv.customer.name, 10)
            for k, ln in enumerate(inv.customer.lines):
                p.t(40, 177 + 12 * k, ln, 9)
            y = 235
        else:
            p.t(40, 50, inv.supplier.name, 12, "Helvetica-Bold")
            p.t(W - 40, 50, f"Invoice No: {inv.p_number or ''}  (page {pg + 1} of {len(pages)})", 10, "Helvetica", "r")
            y = 90
        for x, h, al in [(40, "Description", "l"), (350, "Qty", "r"), (440, "Unit Price", "r"), (W - 40, "Amount", "r")]:
            p.t(x, y, h, 10, "Helvetica-Bold", al)
        p.line(40, y + 4, W - 40)
        y += 20
        for it in inv.items[idx: idx + n_rows]:
            p.t(40, y, it.description, 10)
            p.t(350, y, f"{it.quantity:g}", 10, "Helvetica", "r")
            p.t(440, y, money(inv, it.unit_price, look), 10, "Helvetica", "r")
            p.t(W - 40, y, money(inv, inv.p_item_amounts[idx], look), 10, "Helvetica", "r")
            idx += 1
            y += 17
        if pg == len(pages) - 1:
            y += 8
            p.line(360, y - 10, W - 40)
            rows = [("Subtotal", inv.p_subtotal)]
            if inv.discount:
                rows.append(("Discount", -inv.discount))
            if inv.shipping:
                rows.append(("Shipping", inv.shipping))
            printed_tax = prints_tax(inv, show_zero_tax)
            if printed_tax:
                rows.append((tax_label_text(inv), inv.p_tax))
            rows.append(("Total", inv.p_total))
            for lbl, v in rows:
                bold = "Helvetica-Bold" if lbl == "Total" else "Helvetica"
                p.t(380, y, lbl, 10.5, bold)
                p.t(W - 40, y, money(inv, v, look), 10.5, bold, "r")
                y += 16
            if look.cur_mode == "header":
                p.t(40, y + 10, f"All amounts are in {inv.currency}.", 9, gray=0.3)
            p.t(40, H - 60, "Thank you for your business.", 9, gray=0.3)
        if len(pages) > 1:
            p.t(W / 2, H - 30, f"Page {pg + 1} of {len(pages)}", 8, align="c", gray=0.4)
        if pg < len(pages) - 1:
            p.page()
    return {"look": look, "pages": len(pages), "printed_tax": prints_tax(inv, show_zero_tax),
            "prints": {"number", "date", "customer", "subtotal", "items", "item_qty", "item_unit", "item_amount"} | ({"due"} if inv.p_due else set()) | ({"tax"} if prints_tax(inv, show_zero_tax) else set())}


# ---------------------------------------------------------------------------------------------- T2


def render_t2(c: canvas.Canvas, inv: Invoice, rng: random.Random) -> dict:
    p = Pen(c)
    look = pick_look(rng, inv, cur_modes=("code", "header", "none"))
    pages = paginate(len(inv.items), 22, 36)
    idx = 0
    show_due = bool(inv.p_due)
    show_tax = inv.tax_rate > 0 or rng.random() < 0.5
    for pg, n_rows in enumerate(pages):
        if pg == 0:
            p.t(40, 60, inv.supplier.name, 20, "Helvetica-Bold")
            p.t(40, 76, " | ".join(inv.supplier.lines[:2]), 9, gray=0.25)
            p.t(40, 88, f"{inv.supplier.email}   {inv.supplier.phone}", 9, gray=0.25)
            heads = [("Invoice #", 40), ("Date", 190), ("Due Date", 320), ("PO Number", 450)]
            p.box(40, 112, W - 80, 18, fill=0.88)
            for lbl, x in heads:
                p.t(x + 6, 124, lbl, 9.5, "Helvetica-Bold")
            p.box(40, 130, W - 80, 22)
            p.t(46, 145, inv.p_number or "", 10)
            p.t(196, 145, fmt_date(inv.p_date, look.date_style) if inv.p_date else "", 10)
            p.t(326, 145, fmt_date(inv.p_due, look.date_style) if show_due else "", 10)
            p.t(456, 145, inv.po or "", 10)
            p.t(40, 185, "Billed To", 10, "Helvetica-Bold")
            p.t(40, 199, inv.customer.name, 10)
            for k, ln in enumerate(inv.customer.lines):
                p.t(40, 212 + 12 * k, ln, 9)
            y = 265
        else:
            p.t(40, 50, inv.supplier.name, 12, "Helvetica-Bold")
            p.t(W - 40, 50, f"Invoice #: {inv.p_number or ''}   Page {pg + 1} of {len(pages)}", 10, align="r")
            y = 90
        amount_hdr = f"Line Total ({inv.currency})" if look.cur_mode == "header" else "Line Total"
        p.box(40, y - 14, W - 80, 20, fill=0.88)
        for x, h, al in [(46, "Item", "l"), (330, "Hours/Qty", "r"), (420, "Rate", "r"), (W - 46, amount_hdr, "r")]:
            p.t(x, y, h, 9.5, "Helvetica-Bold", al)
        y += 22
        for it in inv.items[idx: idx + n_rows]:
            p.t(46, y, it.description, 10)
            p.t(330, y, f"{it.quantity:g}", 10, align="r")
            p.t(420, y, money(inv, it.unit_price, look), 10, align="r")
            p.t(W - 46, y, money(inv, inv.p_item_amounts[idx], look), 10, align="r")
            p.line(40, y + 5, W - 40, 0.3, 0.7)
            idx += 1
            y += 18
        if pg == len(pages) - 1:
            y += 6
            rows = [("Sub-total", inv.p_subtotal)]
            if inv.discount:
                rows.append(("Discount", -inv.discount))
            if inv.shipping:
                rows.append(("Shipping", inv.shipping))
            if show_tax:
                rows.append((f"VAT ({inv.tax_rate:g}%)", inv.p_tax))
            rows.append(("Amount Due", inv.p_total))
            for lbl, v in rows:
                bold = "Helvetica-Bold" if lbl == "Amount Due" else "Helvetica"
                p.t(380, y, lbl, 10.5, bold)
                p.t(W - 46, y, money(inv, v, look), 10.5, bold, "r")
                y += 16
            p.t(40, H - 70, "Payment terms: bank transfer within the due date. Late payments may attract interest.", 8.5, gray=0.35)
        if len(pages) > 1:
            p.t(W / 2, H - 30, f"Page {pg + 1} of {len(pages)}", 8, align="c", gray=0.4)
        if pg < len(pages) - 1:
            p.page()
    return {"look": look, "pages": len(pages),
            "prints": {"number", "date", "customer", "subtotal", "items", "item_qty", "item_unit", "item_amount"} | ({"due"} if show_due else set()) | ({"tax"} if show_tax else set())}


# ---------------------------------------------------------------------------------------------- T3 (receipt)


def render_t3(c: canvas.Canvas, inv: Invoice, rng: random.Random) -> dict:
    w, h = 226.0, 70 + 14 * (len(inv.items) + 18)
    c.setPageSize((w, h))
    p = Pen(c, w, h)
    look = pick_look(rng, inv, num_style="us" if inv.currency not in ("EUR",) else "eu", date_style=rng.choice(["dmy_slash", "iso", "dmy_dot"]), cur_modes=("none", "code"))
    y = 28
    p.t(w / 2, y, inv.supplier.name, 11, "Courier-Bold", "c")
    y += 13
    for ln in inv.supplier.lines[:2]:
        p.t(w / 2, y, ln, 8, "Courier", "c")
        y += 10
    p.t(w / 2, y, f"Tel: {inv.supplier.phone}", 8, "Courier", "c")
    y += 12
    p.t(8, y, "-" * 36, 8, "Courier")
    y += 11
    p.t(8, y, f"Receipt No: {inv.p_number or ''}" if inv.p_number else "Receipt No:", 8.5, "Courier")
    y += 11
    if inv.p_date:
        p.t(8, y, f"Date: {fmt_date(inv.p_date, look.date_style)} 11:{rng.randint(10, 59)}", 8.5, "Courier")
        y += 11
    p.t(8, y, "-" * 36, 8, "Courier")
    y += 12
    for k, it in enumerate(inv.items):
        qty = f"{it.quantity:g}"
        desc = it.description[:22]
        it.printed_description = desc
        amt = fmt_number(inv.p_item_amounts[k], look.num_style)
        p.t(8, y, f"{qty} x {desc}", 8.5, "Courier")
        p.t(w - 8, y, amt, 8.5, "Courier", "r")
        y += 11
    p.t(8, y, "-" * 36, 8, "Courier")
    y += 12
    rows = [("SUBTOTAL", inv.p_subtotal)]
    if inv.discount:
        rows.append(("DISCOUNT", -inv.discount))
    if inv.shipping:
        rows.append(("SHIPPING", inv.shipping))
    show_tax = inv.tax_rate > 0
    if show_tax:
        rows.append((f"TAX {inv.tax_rate:g}%", inv.p_tax))
    rows.append(("TOTAL", inv.p_total))
    for lbl, v in rows:
        bold = "Courier-Bold" if lbl == "TOTAL" else "Courier"
        txt = fmt_number(v, look.num_style)
        if lbl == "TOTAL" and look.cur_mode == "code":
            txt = f"{inv.currency} {txt}"
        p.t(8, y, lbl, 9, bold)
        p.t(w - 8, y, txt, 9, bold, "r")
        y += 12
    p.t(w / 2, y + 10, "THANK YOU - VISIT AGAIN", 8, "Courier", "c")
    return {"look": look, "pages": 1, "prints": {"number", "date", "subtotal", "items", "item_qty", "item_amount"} | ({"tax"} if show_tax else set()), "no_customer": True}


# ---------------------------------------------------------------------------------------------- T4 (European)


def render_t4(c: canvas.Canvas, inv: Invoice, rng: random.Random) -> dict:
    p = Pen(c)
    look = Look("eu", rng.choice(["dmy_dot", "dmy_dot", "iso"]), rng.choice(["symbol", "code"]) if inv.currency == "EUR" else "code")
    pages = paginate(len(inv.items), 24, 36)
    idx = 0
    sender = f"{inv.supplier.name} · {inv.supplier.lines[0]} · {inv.supplier.lines[1]}"
    for pg, n_rows in enumerate(pages):
        if pg == 0:
            p.t(40, 52, sender, 7.5, gray=0.3)
            p.t(40, 78, "Customer:", 9, gray=0.3)
            p.t(40, 92, inv.customer.name, 11, "Helvetica-Bold")
            for k, ln in enumerate(inv.customer.lines):
                p.t(40, 106 + 12 * k, ln, 10)
            p.t(W - 40, 80, "Invoice", 22, "Helvetica-Bold", "r")
            p.t(W - 40, 102, f"Invoice no.: {inv.p_number or ''}" if inv.p_number else "Invoice no.:", 10, align="r")
            if inv.p_date:
                p.t(W - 40, 116, f"Invoice date: {fmt_date(inv.p_date, look.date_style)}", 10, align="r")
            if inv.p_due:
                p.t(W - 40, 130, f"Payment due: {fmt_date(inv.p_due, look.date_style)}", 10, align="r")
            y = 215
        else:
            p.t(40, 50, f"{inv.supplier.name}", 11, "Helvetica-Bold")
            p.t(W - 40, 50, f"Invoice no.: {inv.p_number or ''}  -  page {pg + 1} of {len(pages)}", 10, align="r")
            y = 90
        for x, h, al in [(40, "Pos.", "l"), (80, "Description", "l"), (360, "Qty", "r"), (440, "Unit price", "r"), (W - 40, "Net amount", "r")]:
            p.t(x, y, h, 9.5, "Helvetica-Bold", al)
        p.line(40, y + 4, W - 40)
        y += 19
        for it in inv.items[idx: idx + n_rows]:
            p.t(40, y, str(idx + 1), 10)
            p.t(80, y, it.description, 10)
            p.t(360, y, fmt_number(it.quantity, "eu").rstrip("0").rstrip(",") if not float(it.quantity).is_integer() else f"{int(it.quantity)}", 10, align="r")
            p.t(440, y, money(inv, it.unit_price, look), 10, align="r")
            p.t(W - 40, y, money(inv, inv.p_item_amounts[idx], look), 10, align="r")
            idx += 1
            y += 17
        if pg == len(pages) - 1:
            y += 10
            p.line(340, y - 12, W - 40)
            rows = [("Net total", inv.p_subtotal)]
            if inv.discount:
                rows.append(("Discount", -inv.discount))
            if inv.shipping:
                rows.append(("Shipping", inv.shipping))
            show_tax = inv.tax_rate > 0
            if show_tax:
                rows.append((f"VAT {inv.tax_rate:g}%", inv.p_tax))
            rows.append(("Gross total", inv.p_total))
            for lbl, v in rows:
                bold = "Helvetica-Bold" if lbl == "Gross total" else "Helvetica"
                p.t(350, y, lbl, 10.5, bold)
                p.t(W - 40, y, money(inv, v, look), 10.5, bold, "r")
                y += 16
            p.line(40, H - 90, W - 40, 0.4, 0.5)
            p.t(40, H - 78, f"{inv.supplier.name}  ·  {inv.supplier.email}  ·  {inv.supplier.phone}", 8, gray=0.35)
            p.t(40, H - 66, "Bank: IBAN DE89 3704 0044 0532 0130 00   BIC COBADEFFXXX", 8, gray=0.35)
        if len(pages) > 1:
            p.t(W / 2, H - 30, f"Page {pg + 1} of {len(pages)}", 8, align="c", gray=0.4)
        if pg < len(pages) - 1:
            p.page()
    return {"look": look, "pages": len(pages), "prints": {"number", "date", "customer", "subtotal", "items", "item_qty", "item_unit", "item_amount"} | ({"due"} if inv.p_due else set()) | ({"tax"} if inv.tax_rate > 0 else set())}


# ---------------------------------------------------------------------------------------------- T5 (modern)


def render_t5(c: canvas.Canvas, inv: Invoice, rng: random.Random) -> dict:
    p = Pen(c)
    look = pick_look(rng, inv, date_style=rng.choice(["mon_dmy", "dmy_full", "dmy_mon"]), cur_modes=("code", "symbol", "header"))
    pages = paginate(len(inv.items), 20, 36, reserve=9)
    idx = 0
    for pg, n_rows in enumerate(pages):
        if pg == 0:
            p.t(40, 66, "Tax Invoice", 26, "Helvetica-Bold")
            p.t(W - 40, 40, "Invoice number", 8.5, align="r", gray=0.45)
            p.t(W - 40, 54, inv.p_number or "-", 11, "Helvetica-Bold", "r")
            p.t(W - 40, 74, "Date of issue", 8.5, align="r", gray=0.45)
            p.t(W - 40, 88, fmt_date(inv.p_date, look.date_style) if inv.p_date else "-", 10, align="r")
            p.t(W - 40, 108, "Date due", 8.5, align="r", gray=0.45)
            p.t(W - 40, 122, fmt_date(inv.p_due, look.date_style) if inv.p_due else "-", 10, align="r")
            p.t(40, 128, "FROM", 8.5, gray=0.45)
            p.t(40, 142, inv.supplier.name, 11, "Helvetica-Bold")
            for k, ln in enumerate(inv.supplier.lines):
                p.t(40, 155 + 12 * k, ln, 9.5)
            p.t(300, 160, "BILL TO", 8.5, gray=0.45)
            p.t(300, 174, inv.customer.name, 11, "Helvetica-Bold")
            for k, ln in enumerate(inv.customer.lines):
                p.t(300, 187 + 12 * k, ln, 9.5)
            y = 260
        else:
            p.t(40, 50, f"Tax Invoice {inv.p_number or ''}", 12, "Helvetica-Bold")
            p.t(W - 40, 50, f"Page {pg + 1} of {len(pages)}", 10, align="r")
            y = 90
        for x, h, al in [(40, "Description", "l"), (360, "Quantity", "r"), (450, "Price", "r"), (W - 40, "Amount", "r")]:
            p.t(x, y, h, 9, "Helvetica-Bold", al, gray=0.25)
        p.line(40, y + 5, W - 40, 1.2)
        y += 22
        for it in inv.items[idx: idx + n_rows]:
            p.t(40, y, it.description, 10)
            p.t(360, y, f"{it.quantity:g}", 10, align="r")
            p.t(450, y, money(inv, it.unit_price, look), 10, align="r")
            p.t(W - 40, y, money(inv, inv.p_item_amounts[idx], look), 10, align="r")
            p.line(40, y + 6, W - 40, 0.3, 0.8)
            idx += 1
            y += 19
        if pg == len(pages) - 1:
            y += 8
            rows = [("Subtotal", inv.p_subtotal)]
            if inv.discount:
                rows.append(("Discount", -inv.discount))
            if inv.shipping:
                rows.append(("Shipping", inv.shipping))
            if inv.tax_rate > 0:
                rows.append((f"Tax ({inv.tax_rate:g}%)", inv.p_tax))
            rows.append(("Total", inv.p_total))
            for lbl, v in rows:
                p.t(380, y, lbl, 10.5)
                p.t(W - 40, y, money(inv, v, look), 10.5, align="r")
                y += 17
            p.line(380, y - 8, W - 40, 1.2)
            y += 6
            p.t(380, y, "Total due", 12, "Helvetica-Bold")
            p.t(W - 40, y, money(inv, inv.p_total, look), 12, "Helvetica-Bold", "r")
            if look.cur_mode == "header":
                p.t(40, y, f"Currency: {inv.currency}", 9, gray=0.35)
        if len(pages) > 1:
            p.t(W / 2, H - 30, f"Page {pg + 1} of {len(pages)}", 8, align="c", gray=0.4)
        if pg < len(pages) - 1:
            p.page()
    return {"look": look, "pages": len(pages), "prints": {"number", "date", "customer", "subtotal", "items", "item_qty", "item_unit", "item_amount"} | ({"due"} if inv.p_due else set()) | ({"tax"} if inv.tax_rate > 0 else set())}


# ---------------------------------------------------------------------------------------------- T6 (Bangladesh style)


def render_t6(c: canvas.Canvas, inv: Invoice, rng: random.Random) -> dict:
    p = Pen(c)
    look = Look("lakh" if inv.currency in ("BDT", "INR") else "us", rng.choice(["dmy_slash", "dmy_dash_mon", "dmy_slash"]), "header")
    pages = paginate(len(inv.items), 20, 34, reserve=9)
    idx = 0
    for pg, n_rows in enumerate(pages):
        if pg == 0:
            p.t(W / 2, 56, inv.supplier.name, 17, "Helvetica-Bold", "c")
            p.t(W / 2, 71, ", ".join(inv.supplier.lines), 9, align="c")
            p.t(W / 2, 83, f"Phone: {inv.supplier.phone}, Email: {inv.supplier.email}", 9, align="c")
            p.box(W / 2 - 50, 94, 100, 22)
            p.t(W / 2, 110, "INVOICE", 13, "Helvetica-Bold", "c")
            p.t(40, 144, f"Invoice No.: {inv.p_number or ''}" if inv.p_number else "Invoice No.:", 10)
            if inv.p_date:
                p.t(40, 158, f"Date: {fmt_date(inv.p_date, look.date_style)}", 10)
            p.t(300, 144, f"Customer Name: {inv.customer.name}", 10)
            p.t(300, 158, f"Address: {inv.customer.lines[0]}, {inv.customer.lines[1]}", 9)
            y = 195
        else:
            p.t(40, 50, f"{inv.supplier.name}  -  Invoice No.: {inv.p_number or ''}", 10, "Helvetica-Bold")
            p.t(W - 40, 50, f"Page {pg + 1} of {len(pages)}", 9, align="r")
            y = 90
        cols = [(40, 62, "Sl."), (62, 330, "Description"), (330, 385, "Qty"), (385, 465, "Rate"), (465, W - 40, f"Amount ({inv.currency})")]
        p.box(40, y - 14, W - 80, 20, fill=0.9)
        for x0, x1, h in cols:
            p.t(x0 + 4, y, h, 9.5, "Helvetica-Bold")
        for x0, _, _ in cols[1:]:
            p.vline(x0, y - 14, y + 6)
        y += 22
        for it in inv.items[idx: idx + n_rows]:
            p.box(40, y - 13, W - 80, 19, stroke=True)
            p.t(44, y, str(idx + 1), 10)
            p.t(66, y, it.description, 10)
            p.t(381, y, f"{it.quantity:g}", 10, align="r")
            p.t(461, y, fmt_number(it.unit_price, look.num_style), 10, align="r")
            p.t(W - 44, y, fmt_number(inv.p_item_amounts[idx], look.num_style), 10, align="r")
            for x0, _, _ in cols[1:]:
                p.vline(x0, y - 13, y + 6)
            idx += 1
            y += 19
        if pg == len(pages) - 1:
            y += 10
            rows = [("Sub Total", inv.p_subtotal)]
            if inv.discount:
                rows.append(("Discount", -inv.discount))
            if inv.shipping:
                rows.append(("Shipping", inv.shipping))
            show_tax = inv.tax_rate > 0
            if show_tax:
                rows.append((f"VAT ({inv.tax_rate:g}%)", inv.p_tax))
            rows.append(("Grand Total", inv.p_total))
            for lbl, v in rows:
                bold = "Helvetica-Bold" if lbl == "Grand Total" else "Helvetica"
                p.t(385, y, lbl, 10.5, bold)
                p.t(W - 44, y, fmt_number(v, look.num_style), 10.5, bold, "r")
                y += 16
            p.t(40, y + 14, f"In words: {inv.currency} only (amount in figures above).", 9, gray=0.3)
            p.t(40, H - 80, "Authorised Signature", 9, gray=0.3)
            p.line(40, H - 90, 160, 0.5)
        if len(pages) > 1:
            p.t(W / 2, H - 30, f"Page {pg + 1} of {len(pages)}", 8, align="c", gray=0.4)
        if pg < len(pages) - 1:
            p.page()
    return {"look": look, "pages": len(pages), "prints": {"number", "date", "customer", "subtotal", "items", "item_qty", "item_unit", "item_amount"} | ({"tax"} if inv.tax_rate > 0 else set())}


DEV_TEMPLATES = {"T1": render_t1, "T2": render_t2, "T3": render_t3, "T4": render_t4, "T5": render_t5, "T6": render_t6}


def render_pdf(path: str, invoices: list[Invoice], template: str, rng: random.Random, registry: dict | None = None) -> list[dict]:
    """Render one or more invoices (one after another) into a single PDF. Returns each invoice's render info."""
    fn = (registry or DEV_TEMPLATES)[template]
    c = canvas.Canvas(path, pagesize=A4)
    infos = []
    for k, inv in enumerate(invoices):
        c.setPageSize(A4)
        for it in inv.items:
            it.printed_description = None
        info = fn(c, inv, rng)
        infos.append(info)
        c.showPage()
    c.save()
    return infos
