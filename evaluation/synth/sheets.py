"""Spreadsheet and CSV versions of the invoices: printed-style invoice sheets, ledgers, grouped line items."""

from __future__ import annotations

import csv
import io
import random
import shutil
import subprocess
import tempfile
from pathlib import Path

import openpyxl
import xlwt
from openpyxl.styles import Font

from .data import Invoice, fmt_date, fmt_number


def _date_cell(d, rng: random.Random, as_text: str | None):
    return fmt_date(d, as_text) if as_text else d


# label wording for the printed-style invoice sheet. Set 0 is used by the development split, set 1 only by the holdout split.
SHEET_LABELS = [
    {"title": "INVOICE", "no": "Invoice No", "date": "Invoice Date", "due": "Due Date", "bill": "Bill To:", "sub": "Subtotal", "total": "Total"},
    {"title": "TAX INVOICE", "no": "Document No.", "date": "Issued", "due": "Payment due by", "bill": "Billed to", "sub": "Net total", "total": "Amount payable"},
]


def write_invoice_xlsx(inv: Invoice, path: str | Path, rng: random.Random, *, formulas: bool = False, legacy_xls: bool = False, label_set: int = 0) -> dict:
    """One invoice laid out like a printed one. Returns what is printed (for the ground truth)."""
    L = SHEET_LABELS[label_set]
    if formulas and (inv.discount or inv.shipping):
        # This sheet has no discount or shipping rows, so a recalculated total would not match the label. Write values instead.
        formulas = False
    formulas_used = formulas
    date_text = rng.choice([None, None, "dmy_mon", "iso"])
    show_cur = rng.choice(["header", "row", "none"])
    tax_label = rng.choice(["Tax", "VAT", "GST", "Sales Tax"])
    rows: list[list] = []
    rows.append([inv.supplier.name, None, None, L["title"]])
    rows.append([inv.supplier.lines[0] + ", " + inv.supplier.lines[1]])
    rows.append([])
    rows.append([None, None, None, L["no"], inv.p_number])
    rows.append([None, None, None, L["date"], _date_cell(inv.p_date, rng, date_text) if inv.p_date else None])
    if inv.p_due:
        rows.append([None, None, None, L["due"], _date_cell(inv.p_due, rng, date_text)])
    if show_cur == "row":
        rows.append([None, None, None, "Currency", inv.currency])
    rows.append([])
    rows.append([L["bill"]])
    rows.append([inv.customer.name])
    rows.append([inv.customer.lines[0]])
    rows.append([])
    hdr = ["Description", "Qty", "Unit Price", f"Amount ({inv.currency})" if show_cur == "header" else "Amount"]
    rows.append(hdr)
    first_item_row = len(rows) + 1
    for k, it in enumerate(inv.items):
        r = len(rows) + 1
        amt = f"=B{r}*C{r}" if formulas else inv.p_item_amounts[k]
        rows.append([it.description, it.quantity, it.unit_price, amt])
    last_item_row = len(rows)
    rows.append([])
    sub_r = len(rows) + 1
    rows.append([None, None, L["sub"], f"=SUM(D{first_item_row}:D{last_item_row})" if formulas else inv.p_subtotal])
    printed_tax = inv.tax_rate > 0
    if printed_tax:
        rows.append([None, None, f"{tax_label} ({inv.tax_rate:g}%)", f"=D{sub_r}*{inv.tax_rate / 100}" if formulas else inv.p_tax])
    tot_r = len(rows) + 1
    rows.append([None, None, L["total"], f"=D{sub_r}+D{tot_r - 1}" if (formulas and printed_tax) else (f"=D{sub_r}" if formulas else inv.p_total)])

    if legacy_xls:
        wb = xlwt.Workbook()
        ws = wb.add_sheet("Invoice")
        date_fmt = xlwt.easyxf(num_format_str="YYYY-MM-DD")
        for r, row in enumerate(rows):
            for c, v in enumerate(row):
                if v is None:
                    continue
                if hasattr(v, "year") and not isinstance(v, str):
                    ws.write(r, c, v, date_fmt)
                else:
                    ws.write(r, c, v)
        wb.save(str(path))
    else:
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Invoice"
        for row in rows:
            ws.append(row)
        ws["A1"].font = Font(bold=True, size=14)
        wb.save(str(path))
        if formulas:
            _recalculate(Path(path))
    prints = {"number", "date", "customer", "subtotal", "items", "item_qty", "item_unit", "item_amount"} | ({"due"} if inv.p_due else set()) | ({"tax"} if printed_tax else set())
    return {"prints": prints, "currency_printed": show_cur != "none", "formulas_used": formulas_used}


def _recalculate(path: Path) -> None:
    """Let LibreOffice open and save the file so the formula results are stored in it, like a file saved by Excel."""
    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if not soffice:
        return
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run([soffice, "--headless", "--convert-to", "xlsx", "--outdir", tmp, str(path)], capture_output=True, timeout=120)
        out = Path(tmp) / path.name
        if out.exists():
            shutil.copy(out, path)


LEDGER_HEADERS = [
    ["Invoice No", "Invoice Date", "Supplier", "Customer", "Currency", "Subtotal", "Tax", "Total"],
    ["Invoice Number", "Date", "Vendor", "Client", "Ccy", "Net Amount", "VAT", "Gross Amount"],
    ["Bill No.", "Bill Date", "Supplier Name", "Customer Name", "Currency", "Sub Total", "Tax Amount", "Grand Total"],
    # variants 3 to 5: holdout split only
    ["Doc No.", "Doc Date", "Seller", "Buyer", "Curr.", "Amount before tax", "Sales tax", "Amount due"],
    ["Reference", "Issued", "Supplier", "Bill to", "Currency code", "Net", "Tax", "Total payable"],
    ["Inv #", "Invoice dt", "Vendor Name", "Client Name", "Cur", "Taxable value", "GST", "Invoice value"],
]


def ledger_rows(invoices: list[Invoice], rng: random.Random, *, header_variant: int, date_style: str, include_currency: bool, as_text_numbers: bool = False, num_style: str = "plain") -> tuple[list[list], list[dict]]:
    hdr = LEDGER_HEADERS[header_variant % len(LEDGER_HEADERS)]
    cols = list(range(len(hdr))) if include_currency else [i for i in range(len(hdr)) if i != 4]
    rows: list[list] = [[hdr[i] for i in cols]]
    truths: list[dict] = []
    for inv in invoices:
        def num(v):
            return fmt_number(v, num_style) if as_text_numbers else float(v)
        full = [inv.p_number, fmt_date(inv.p_date, date_style) if date_style else inv.p_date, inv.supplier.name, inv.customer.name, inv.currency, num(inv.p_subtotal), num(inv.p_tax), num(inv.p_total)]
        rows.append([full[i] for i in cols])
        truths.append({
            "invoice_number": inv.p_number, "invoice_date": inv.p_date.isoformat() if inv.p_date else None, "due_date": None,
            "supplier_name": inv.supplier.name, "customer_name": inv.customer.name,
            "subtotal": round(inv.p_subtotal, 2), "tax": round(inv.p_tax, 2), "total": round(inv.p_total, 2),
            "currency": inv.currency if include_currency else None, "line_items": [],
        })
    return rows, truths


def write_ledger(invoices: list[Invoice], path: str | Path, rng: random.Random, fmt: str, **kw) -> list[dict]:
    """fmt: xlsx | xls | csv. Extra keyword arguments: csv_delim, csv_encoding, title_rows."""
    title_rows = kw.pop("title_rows", 0)
    csv_delim = kw.pop("csv_delim", ",")
    csv_encoding = kw.pop("csv_encoding", "utf-8")
    rows, truths = ledger_rows(invoices, rng, **kw)
    totals_row = ["Total"] + [None] * (len(rows[0]) - 1)
    pre = [["Purchase register"], ["Prepared by accounts team"], []][:title_rows]
    body = pre + rows
    if fmt == "xlsx":
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Register"
        for r in body:
            ws.append(r)
        ws.append(totals_row)
        wb.save(str(path))
    elif fmt == "xls":
        wb = xlwt.Workbook()
        ws = wb.add_sheet("Register")
        dfmt = xlwt.easyxf(num_format_str="YYYY-MM-DD")
        for ri, r in enumerate(body):
            for ci, v in enumerate(r):
                if v is None:
                    continue
                ws.write(ri, ci, v, dfmt) if hasattr(v, "year") and not isinstance(v, str) else ws.write(ri, ci, v)
        wb.save(str(path))
    else:
        buf = io.StringIO()
        w = csv.writer(buf, delimiter=csv_delim, lineterminator="\r\n")
        for r in body:
            w.writerow(["" if v is None else (v.isoformat() if hasattr(v, "isoformat") else v) for v in r])
        data = buf.getvalue()
        if csv_encoding == "utf-16":
            Path(path).write_bytes(data.encode("utf-16"))
        else:
            Path(path).write_bytes(data.encode(csv_encoding, errors="replace"))
    return truths


def write_grouped(invoices: list[Invoice], path: str | Path, rng: random.Random, *, with_invoice_total: bool) -> list[dict]:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Lines"
    head = ["Invoice No", "Date", "Supplier", "Customer", "Description", "Qty", "Unit Price", "Line Total"] + (["Invoice Total"] if with_invoice_total else [])
    ws.append(head)
    truths = []
    for inv in invoices:
        for it, amt in zip(inv.items, inv.p_item_amounts):
            row = [inv.p_number, inv.p_date, inv.supplier.name, inv.customer.name, it.description, it.quantity, it.unit_price, amt]
            if with_invoice_total:
                row.append(inv.p_total)
            ws.append(row)
        truths.append({
            "invoice_number": inv.p_number, "invoice_date": inv.p_date.isoformat(), "due_date": None, "supplier_name": inv.supplier.name,
            "customer_name": inv.customer.name, "subtotal": None, "tax": None, "total": round(inv.p_total, 2) if with_invoice_total else None,
            "currency": None,
            "line_items": [{"description": it.description, "quantity": it.quantity, "unit_price": it.unit_price, "amount": round(a, 2)} for it, a in zip(inv.items, inv.p_item_amounts)],
        })
    wb.save(str(path))
    return truths


def write_multisheet(inv_a: Invoice, ledger_invs: list[Invoice], path: str | Path, rng: random.Random) -> tuple[list[dict], list[dict]]:
    """A workbook with a ledger, a notes sheet, an invoice sheet and a hidden sheet. Returns (ledger truths, invoice truth)."""
    tmp = Path(str(path) + ".tmp1.xlsx")
    sheet_info = write_invoice_xlsx(inv_a, tmp, rng)
    inv_wb = openpyxl.load_workbook(tmp)
    inv_ws = inv_wb["Invoice"]
    rows, ledger_truth = ledger_rows(ledger_invs, rng, header_variant=0, date_style="dmy_mon", include_currency=True)
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Summary"
    for r in rows:
        ws.append(r)
    notes = wb.create_sheet("Notes")
    notes.append(["Remember to send the signed copies to accounts."])
    notes.append(["Contact: finance@example.com"])
    inv_copy = wb.create_sheet("Invoice")
    for row in inv_ws.iter_rows(values_only=True):
        inv_copy.append(list(row))
    hidden = wb.create_sheet("Old data")
    hidden.append(["Invoice No", "Total"])
    hidden.append(["OLD-1", 1.0])
    hidden.sheet_state = "hidden"
    wb.save(str(path))
    tmp.unlink(missing_ok=True)
    inv_truth = {"prints": sheet_info["prints"], "currency_printed": sheet_info["currency_printed"]}
    return ledger_truth, [inv_truth]
