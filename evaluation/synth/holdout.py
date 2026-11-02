"""Holdout invoice layouts H1 to H4.

These were written AFTER the extractor was frozen and nothing in the extractor was changed to fit them
before the first holdout run was recorded. They use other fonts, other label wording and other ways of
laying out the same facts than the development layouts T1 to T6 (see evaluation/EVALUATION.md).

  H1  serif "tax invoice": Invoice #, Issued, Payment due by, Billed to, Units / Rate / Line total, Net total, Amount payable
  H2  narrow till receipt in Courier: quantity and price on a second line under each item name, no customer, no due date
  H3  "Bill" with a boxed grid: Bill No., Bill Date, M/s, Particulars, Grand Total
  H4  modern layout with the label printed above its value: INVOICE NUMBER, ISSUE DATE, DUE DATE, BILLED TO, TOTAL DUE

All of it is synthetic. The author is the same person (and tool) that wrote the extractor, which is a limit of this test.
"""

from __future__ import annotations

import random

from reportlab.pdfgen import canvas

from .data import Invoice, fmt_date, fmt_number
from .templates import Look, Pen, H, W, money, paginate, pick_look


def _base_prints(inv: Invoice, *, customer: bool = True, due: bool = True) -> set[str]:
    s = {"number", "date", "subtotal", "items", "item_qty", "item_unit", "item_amount"}
    if customer:
        s.add("customer")
    if due and inv.p_due:
        s.add("due")
    if inv.tax_rate > 0:
        s.add("tax")
    return s


def _totals(inv: Invoice, names: dict[str, str]) -> list[tuple[str, float]]:
    rows: list[tuple[str, float]] = [(names["sub"], inv.p_subtotal)]
    if inv.discount:
        rows.append((names["disc"], -inv.discount))
    if inv.shipping:
        rows.append((names["ship"], inv.shipping))
    if inv.tax_rate > 0:
        rows.append((names["tax"].format(r=f"{inv.tax_rate:g}"), inv.p_tax))
    rows.append((names["total"], inv.p_total))
    return rows


# ---------------------------------------------------------------------------------------------- H1


def render_h1(c: canvas.Canvas, inv: Invoice, rng: random.Random) -> dict:
    p = Pen(c)
    look = pick_look(rng, inv)
    F, FB = "Times-Roman", "Times-Bold"
    pages = paginate(len(inv.items), 20, 32, reserve=9)
    idx = 0
    names = {"sub": "Net total", "disc": "Less discount", "ship": "Delivery charge", "tax": "VAT ({r}%)", "total": "Amount payable"}
    for pg, n_rows in enumerate(pages):
        if pg == 0:
            p.t(40, 62, inv.supplier.name, 21, FB)
            for k, ln in enumerate(inv.supplier.lines):
                p.t(40, 80 + 12 * k, ln, 10, F)
            p.t(40, 80 + 12 * len(inv.supplier.lines), inv.supplier.phone, 10, F, gray=0.3)
            p.t(W - 40, 62, "Tax Invoice", 19, FB, "r")
            p.t(W - 40, 86, f"Invoice # {inv.p_number}" if inv.p_number else "Invoice #", 11, F, "r")
            if inv.p_date:
                p.t(W - 40, 101, f"Issued {fmt_date(inv.p_date, look.date_style)}", 11, F, "r")
            y = 116
            if inv.p_due:
                p.t(W - 40, y, f"Payment due by {fmt_date(inv.p_due, look.date_style)}", 11, F, "r")
                y += 15
            if inv.po:
                p.t(W - 40, y, f"Your ref. {inv.po}", 11, F, "r")
            p.line(40, 175, W - 40, 1.0)
            p.t(40, 195, "Billed to", 11, FB)
            p.t(40, 210, inv.customer.name, 12, F)
            for k, ln in enumerate(inv.customer.lines):
                p.t(40, 224 + 12 * k, ln, 10, F)
            p.t(340, 195, "Payment terms", 11, FB)
            p.t(340, 210, "Bank transfer, please quote the invoice number", 10, F)
            y = 275
        else:
            p.t(40, 50, inv.supplier.name, 13, FB)
            p.t(W - 40, 50, f"Invoice # {inv.p_number or ''}   page {pg + 1} of {len(pages)}", 10, F, "r")
            y = 90
        p.box(40, y - 14, W - 80, 20, fill=0.9)
        for x, h, al in [(46, "Item", "l"), (350, "Units", "r"), (440, "Rate", "r"), (W - 46, "Line total", "r")]:
            p.t(x, y, h, 10.5, FB, al)
        y += 24
        for it in inv.items[idx: idx + n_rows]:
            p.t(46, y, it.description, 10.5, F)
            p.t(350, y, f"{it.quantity:g}", 10.5, F, "r")
            p.t(440, y, money(inv, it.unit_price, look), 10.5, F, "r")
            p.t(W - 46, y, money(inv, inv.p_item_amounts[idx], look), 10.5, F, "r")
            idx += 1
            y += 18
        if pg == len(pages) - 1:
            y += 6
            p.line(330, y - 12, W - 40, 0.8)
            for lbl, v in _totals(inv, names):
                bold = FB if lbl == names["total"] else F
                if lbl == names["total"]:
                    p.line(330, y - 12, W - 40, 1.4)
                    y += 4
                p.t(340, y, lbl, 11, bold)
                p.t(W - 46, y, money(inv, v, look), 11, bold, "r")
                y += 17
            if look.cur_mode == "header":
                p.t(40, y + 6, f"Amounts shown in {inv.currency}.", 9.5, F, gray=0.3)
            p.t(40, H - 70, "Bank: First Commerce Bank   Account 00-4417-2209   Sort 20-45-17", 9, F, gray=0.35)
        if len(pages) > 1:
            p.t(W / 2, H - 30, f"{pg + 1} / {len(pages)}", 8, align="c", gray=0.4)
        if pg < len(pages) - 1:
            p.page()
    return {"look": look, "pages": len(pages), "prints": _base_prints(inv)}


# ---------------------------------------------------------------------------------------------- H2 (till receipt)


def render_h2(c: canvas.Canvas, inv: Invoice, rng: random.Random) -> dict:
    p = Pen(c)
    look = pick_look(rng, inv, cur_modes=("code", "symbol", "header", "none"))
    F, FB = "Courier", "Courier-Bold"
    x0, x1 = 150, 445
    has_cust = rng.random() < 0.5
    width_chars = 40
    for it in inv.items:
        d = it.description
        it.printed_description = d if len(d) <= width_chars - 2 else d[: width_chars - 2].rstrip()
    caps = [16, 30]
    pages = paginate(len(inv.items), caps[0], caps[1], reserve=7)
    idx = 0
    names = {"sub": "Sub-total", "disc": "Discount", "ship": "Shipping", "tax": "Tax {r}%", "total": "TOTAL"}
    for pg, n_rows in enumerate(pages):
        if pg == 0:
            p.t(W / 2, 62, inv.supplier.name, 13, FB, "c")
            for k, ln in enumerate(inv.supplier.lines):
                p.t(W / 2, 77 + 11 * k, ln, 8.5, F, "c")
            p.t(W / 2, 77 + 11 * len(inv.supplier.lines), inv.supplier.phone, 8.5, F, "c")
            y = 77 + 11 * (len(inv.supplier.lines) + 1) + 18
            p.t(W / 2, y, "* * * RECEIPT * * *", 11, FB, "c")
            y += 20
            p.t(x0, y, f"Receipt No: {inv.p_number}" if inv.p_number else "Receipt No:", 9.5, F)
            y += 13
            if inv.p_date:
                p.t(x0, y, f"Date: {fmt_date(inv.p_date, look.date_style)}", 9.5, F)
                y += 13
            if has_cust:
                p.t(x0, y, f"Sold to: {inv.customer.name}", 9.5, F)
                y += 13
            p.t(x0, y, "-" * width_chars, 9.5, F)
            y += 15
        else:
            p.t(x0, 50, f"Receipt No: {inv.p_number or ''}  (page {pg + 1})", 9.5, F)
            y = 80
        for it in inv.items[idx: idx + n_rows]:
            p.t(x0, y, it.printed_description or it.description, 9.5, F)
            y += 12
            p.t(x0 + 14, y, f"{it.quantity:g} x {money(inv, it.unit_price, look)}", 9.5, F)
            p.t(x1, y, money(inv, inv.p_item_amounts[idx], look), 9.5, F, "r")
            idx += 1
            y += 15
        if pg == len(pages) - 1:
            p.t(x0, y, "-" * width_chars, 9.5, F)
            y += 15
            for lbl, v in _totals(inv, names):
                bold = FB if lbl == names["total"] else F
                p.t(x0, y, lbl, 10 if lbl != names["total"] else 11.5, bold)
                p.t(x1, y, money(inv, v, look), 10 if lbl != names["total"] else 11.5, bold, "r")
                y += 15
            if look.cur_mode == "header":
                p.t(x0, y + 4, f"Currency: {inv.currency}", 9, F)
                y += 14
            p.t(W / 2, y + 18, "THANK YOU, PLEASE COME AGAIN", 9, F, "c")
        if pg < len(pages) - 1:
            p.page()
    return {"look": look, "pages": len(pages), "prints": _base_prints(inv, customer=has_cust, due=False)}


# ---------------------------------------------------------------------------------------------- H3 (boxed "bill")


def render_h3(c: canvas.Canvas, inv: Invoice, rng: random.Random) -> dict:
    p = Pen(c)
    look = Look("lakh" if inv.currency in ("BDT", "INR") else pick_look(rng, inv).num_style, rng.choice(["dmy_slash", "dmy_mon", "dmy_dot", "dmy_dash_mon"]), "header")
    pages = paginate(len(inv.items), 20, 32, reserve=9)
    idx = 0
    names = {"sub": "Sub Total", "disc": "Discount", "ship": "Delivery", "tax": "VAT {r}%", "total": "Grand Total"}
    for pg, n_rows in enumerate(pages):
        if pg == 0:
            p.t(40, 60, inv.supplier.name, 18, "Helvetica-Bold")
            for k, ln in enumerate(inv.supplier.lines):
                p.t(40, 76 + 12 * k, ln, 9)
            p.t(W - 40, 60, "BILL", 20, "Helvetica-Bold", "r")
            gy = 140
            p.box(40, gy, W - 80, 66)
            p.vline(W / 2, gy, gy + 66)
            p.line(40, gy + 33, W - 40)
            p.t(46, gy + 14, "Bill No.", 9, "Helvetica-Bold")
            p.t(110, gy + 14, inv.p_number or "", 10.5)
            p.t(W / 2 + 6, gy + 14, "Bill Date", 9, "Helvetica-Bold")
            p.t(W / 2 + 70, gy + 14, fmt_date(inv.p_date, look.date_style) if inv.p_date else "", 10.5)
            p.t(46, gy + 28 + 19, "M/s", 9, "Helvetica-Bold")
            p.t(110, gy + 28 + 19, inv.customer.name, 10.5)
            p.t(W / 2 + 6, gy + 28 + 19, "Payment Due", 9, "Helvetica-Bold")
            p.t(W / 2 + 70, gy + 28 + 19, fmt_date(inv.p_due, look.date_style) if inv.p_due else "", 10.5)
            y = 245
        else:
            p.t(40, 50, f"{inv.supplier.name}   Bill No. {inv.p_number or ''}", 10, "Helvetica-Bold")
            p.t(W - 40, 50, f"Page {pg + 1} of {len(pages)}", 9, align="r")
            y = 90
        cols = [(40, 66, "S/N"), (66, 340, "Particulars"), (340, 395, "Qty"), (395, 470, "Price"), (470, W - 40, "Total")]
        p.box(40, y - 14, W - 80, 20, fill=0.88)
        for x0, _, h in cols:
            p.t(x0 + 4, y, h, 9.5, "Helvetica-Bold")
        for x0, _, _ in cols[1:]:
            p.vline(x0, y - 14, y + 6)
        y += 22
        for it in inv.items[idx: idx + n_rows]:
            p.box(40, y - 13, W - 80, 19, stroke=True)
            p.t(44, y, str(idx + 1), 10)
            p.t(70, y, it.description, 10)
            p.t(391, y, f"{it.quantity:g}", 10, align="r")
            p.t(466, y, fmt_number(it.unit_price, look.num_style), 10, align="r")
            p.t(W - 44, y, fmt_number(inv.p_item_amounts[idx], look.num_style), 10, align="r")
            for x0, _, _ in cols[1:]:
                p.vline(x0, y - 13, y + 6)
            idx += 1
            y += 19
        if pg == len(pages) - 1:
            y += 12
            for lbl, v in _totals(inv, names):
                bold = "Helvetica-Bold" if lbl == names["total"] else "Helvetica"
                p.t(395, y, lbl, 10.5, bold)
                p.t(W - 44, y, fmt_number(v, look.num_style), 10.5, bold, "r")
                y += 16
            p.t(40, y + 14, f"All amounts in {inv.currency}. E. & O.E.", 9, gray=0.3)
            p.t(W - 40, H - 80, "Received with thanks", 9, align="r", gray=0.3)
        if len(pages) > 1:
            p.t(W / 2, H - 30, f"Page {pg + 1} of {len(pages)}", 8, align="c", gray=0.4)
        if pg < len(pages) - 1:
            p.page()
    return {"look": look, "pages": len(pages), "prints": _base_prints(inv)}


# ---------------------------------------------------------------------------------------------- H4 (label above value)


def render_h4(c: canvas.Canvas, inv: Invoice, rng: random.Random) -> dict:
    p = Pen(c)
    look = pick_look(rng, inv)
    pages = paginate(len(inv.items), 21, 33, reserve=9)
    idx = 0
    names = {"sub": "Subtotal", "disc": "Discount", "ship": "Shipping", "tax": "Sales tax ({r}%)", "total": "Total"}
    lab = lambda x, y, s, al="l": p.t(x, y, s, 7.5, "Helvetica-Bold", al, gray=0.45)  # noqa: E731
    for pg, n_rows in enumerate(pages):
        if pg == 0:
            p.t(40, 62, inv.supplier.name, 17, "Helvetica-Bold")
            p.t(W - 40, 62, "Invoice", 24, "Helvetica", "r", gray=0.35)
            p.box(40, 100, W - 80, 48, fill=0.95)
            lab(52, 114, "INVOICE NUMBER")
            p.t(52, 134, inv.p_number or "", 11)
            lab(200, 114, "ISSUE DATE")
            p.t(200, 134, fmt_date(inv.p_date, look.date_style) if inv.p_date else "", 11)
            lab(330, 114, "DUE DATE")
            p.t(330, 134, fmt_date(inv.p_due, look.date_style) if inv.p_due else "", 11)
            lab(W - 52, 114, "TOTAL DUE", "r")
            p.t(W - 52, 134, money(inv, inv.p_total, look), 13, "Helvetica-Bold", "r")
            lab(40, 185, "BILLED TO")
            p.t(40, 201, inv.customer.name, 11.5, "Helvetica-Bold")
            for k, ln in enumerate(inv.customer.lines):
                p.t(40, 215 + 12 * k, ln, 9.5)
            lab(330, 185, "FROM")
            p.t(330, 201, inv.supplier.name, 11)
            for k, ln in enumerate(inv.supplier.lines):
                p.t(330, 215 + 12 * k, ln, 9.5)
            if inv.po:
                lab(330, 262, "PURCHASE ORDER")
                p.t(330, 276, inv.po, 10)
            y = 312
        else:
            p.t(40, 50, inv.supplier.name, 12, "Helvetica-Bold")
            p.t(W - 40, 50, f"{inv.p_number or ''}  (page {pg + 1} of {len(pages)})", 10, align="r")
            y = 90
        for x, h, al in [(40, "DESCRIPTION", "l"), (350, "QTY", "r"), (445, "UNIT COST", "r"), (W - 40, "AMOUNT", "r")]:
            lab(x, y, h, al)
        p.line(40, y + 6, W - 40, 0.8)
        y += 24
        for it in inv.items[idx: idx + n_rows]:
            p.t(40, y, it.description, 10)
            p.t(350, y, f"{it.quantity:g}", 10, align="r")
            p.t(445, y, money(inv, it.unit_price, look), 10, align="r")
            p.t(W - 40, y, money(inv, inv.p_item_amounts[idx], look), 10, align="r")
            idx += 1
            y += 17
        if pg == len(pages) - 1:
            y += 6
            p.line(340, y - 11, W - 40, 0.8)
            for lbl, v in _totals(inv, names):
                bold = "Helvetica-Bold" if lbl == names["total"] else "Helvetica"
                p.t(350, y, lbl, 10.5, bold)
                p.t(W - 40, y, money(inv, v, look), 10.5, bold, "r")
                y += 16
            if look.cur_mode == "header":
                p.t(40, y + 8, f"Currency: {inv.currency}", 9, gray=0.35)
        if len(pages) > 1:
            p.t(W / 2, H - 30, f"Page {pg + 1} of {len(pages)}", 8, align="c", gray=0.4)
        if pg < len(pages) - 1:
            p.page()
    return {"look": look, "pages": len(pages), "prints": _base_prints(inv)}


HOLDOUT_TEMPLATES = {"H1": render_h1, "H2": render_h2, "H3": render_h3, "H4": render_h4}
