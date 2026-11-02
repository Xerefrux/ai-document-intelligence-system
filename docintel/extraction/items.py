"""Line item extraction: find the table header, then read each row under it."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from . import labels as L
from .lines import Cell, DocLine, clip
from .normalize import Money, parse_amount, parse_quantity
from .schema import LineItemValue

if TYPE_CHECKING:  # pragma: no cover
    from .rules import Ctx

_HEADER = {
    "desc": re.compile(r"^(?:descriptions?|item(?:\s+description)?s?|products?(?:\s+(?:name|description))?|services?|particulars?|details?|goods|article|name|work\s+description|narration|description\s+of\s+(?:goods|services))$", re.I),
    "qty": re.compile(r"^(?:qty\.?|quantity|units?|hours?|hrs\.?|count|pcs\.?|no\.?\s+of\s+\w+|number)$", re.I),
    "unit": re.compile(r"^(?:unit\s+(?:price|cost|rate)|price|rate|unit|cost|price\s+each|each|price\s*/\s*unit|rate\s*/\s*unit|unit\s+amt)$", re.I),
    "amount": re.compile(r"^(?:amount|line\s+total|total|ext\.?(?:ended)?(?:\s+(?:price|amount))?|net\s+amount|value|sub\s*-?\s*total|total\s+price|line\s+amount|net|amt\.?)$", re.I),
    "tax": re.compile(r"^(?:tax(?:\s+(?:%|rate|amount))?|vat(?:\s*%)?|gst(?:\s*%)?)$", re.I),
    "disc": re.compile(r"^(?:disc(?:ount)?(?:\s*%)?)$", re.I),
    "skip": re.compile(r"^(?:#|no\.?|sl\.?|sr\.?(?:\s*no\.?)?|s/n|sku|item\s+(?:code|no\.?|#)|code|ref|part\s+(?:no|number)|hsn(?:/sac)?|uom|unit\s+of\s+measure)$", re.I),
}
_STOP_HEAD = re.compile(r"^(?:notes?|terms?|payment(?:\s+(?:terms|details|info\w*|instructions?))?|bank(?:\s+details)?|remarks?|thank\s+you|please|make\s+(?:cheques?|checks?)|authori[sz]ed|signature|declaration|amount\s+in\s+words|in\s+words|page\s+\d+)\b", re.I)


_CUR_TAIL = re.compile(r"\s*\(\s*(?:[A-Za-z]{3}|[$€£¥₹৳]|tk\.?|rs\.?)\s*\)\s*$", re.I)


def _header_role(raw: str) -> str:
    """Role of one header cell. '(USD)' after the name is ignored, and 'Hours/Qty' is read part by part."""
    text = _CUR_TAIL.sub("", L.repair_label(raw).strip(" :")).strip(" :")
    candidates = [text]
    if "/" in text:
        candidates += [p.strip() for p in text.split("/") if p.strip()]
    for cand in candidates:
        for role, pat in _HEADER.items():
            if pat.match(cand):
                return role
    return "other"


def header_roles(line: DocLine) -> list[tuple[str, Cell]] | None:
    roles: list[tuple[str, Cell]] = []
    for c in line.cells:
        roles.append((_header_role(c.text), c))
    kinds = {r for r, _ in roles}
    if "desc" in kinds and kinds & {"qty", "unit", "amount"}:
        return roles
    known = [r for r, _ in roles if r in ("qty", "unit", "amount")]
    if len(set(known)) >= 2 and len(roles) <= 8 and sum(1 for r, _ in roles if r != "other") >= 3:
        return roles
    return None


def _cell_money(c: Cell, hint: str | None) -> Money | None:
    m = parse_amount(c.text, hint)
    return m if m is not None and not m.percent else None


def _is_totals_line(line: DocLine) -> bool:
    if not line.cells:
        return False
    for c in line.cells[:2]:
        cls = L.classify_amount_label(c.text)
        if cls:
            return True
    return False


def _nearest(cols: list[tuple[str, Cell]], cell: Cell) -> str:
    best, best_d = "other", 1e9
    for role, h in cols:
        d = min(abs(cell.center - h.center), abs(cell.end - h.end), abs(cell.col - h.col))
        if d < best_d:
            best, best_d = role, d
    return best


def _items_from_header(lines: list[DocLine], ctx: "Ctx") -> list[LineItemValue]:
    items: list[LineItemValue] = []
    cols: list[tuple[str, Cell]] | None = None
    desc_col: Cell | None = None
    in_table = False
    last_row_idx = -10
    last_item: LineItemValue | None = None
    for i, ln in enumerate(lines):
        if ln.blank:
            continue
        roles = header_roles(ln)
        if roles:
            cols = [(r, c) for r, c in roles if r != "other"]
            desc = [c for r, c in roles if r == "desc"]
            desc_col = desc[0] if desc else None
            in_table = True
            last_item = None
            continue
        if not in_table or cols is None:
            continue
        if _is_totals_line(ln) and any(_cell_money(c, ctx.decimal_hint) for c in ln.cells[1:]):
            in_table = False
            last_item = None
            continue
        if _STOP_HEAD.match(ln.cells[0].text):
            in_table = False
            last_item = None
            continue

        numeric_roles = [(r, c) for r, c in cols if r in ("qty", "unit", "amount", "tax", "disc", "skip")]
        text_cells: list[Cell] = []
        values: dict[str, Money] = {}
        qty_text: str | None = None
        # First sort the cells of the row: words, a row number left of the description, or numbers.
        pre: list[tuple[Cell, Money | None, object, str]] = []
        seen_text = False
        for c in ln.cells:
            m = _cell_money(c, ctx.decimal_hint)
            q = parse_quantity(c.text)
            if m is None and q is None:
                pre.append((c, None, None, "text"))
                seen_text = True
            elif desc_col is not None and c.end < desc_col.col + 1 and not seen_text:
                pre.append((c, m, q, "rownum"))  # a row number to the left of the description
            else:
                pre.append((c, m, q, "num"))
        # Headers are often left aligned over right aligned numbers, so the nearest header can be the wrong one.
        # When the row has exactly as many numbers as the table has number columns, read them in order instead.
        order_roles = [r for r, h in sorted(numeric_roles, key=lambda rc: rc[1].col) if r != "skip"]
        n_num = sum(1 for p in pre if p[3] == "num")
        use_order = n_num == len(order_roles) and n_num >= 2
        k_num = 0
        for c, m, q, kind in pre:
            if kind == "text":
                text_cells.append(c)
                continue
            if kind == "rownum":
                continue
            if use_order:
                role = order_roles[k_num]
            else:
                role = _nearest(numeric_roles, c) if numeric_roles else "other"
            k_num += 1
            if role == "skip":
                continue
            if role == "qty":
                qty_text = c.text
                values.setdefault("qty", m if m is not None else Money(__import__("decimal").Decimal(str(q))))
            elif role in ("unit", "amount", "tax", "disc"):
                if m is None:
                    text_cells.append(c)
                    continue
                if role in values and role == "amount":
                    values[role] = m  # the right most value wins
                else:
                    values.setdefault(role, m)
            else:
                text_cells.append(c)

        has_money = any(k in values for k in ("unit", "amount"))
        desc = " ".join(c.text for c in text_cells).strip(" -|")
        if has_money and re.search(r"[A-Za-z]{2}", desc):
            qty = float(values["qty"].value) if "qty" in values else None
            unit = values["unit"].as_float() if "unit" in values else None
            amt = values["amount"].as_float() if "amount" in values else None
            conf = 0.8
            if qty is not None and unit is not None and amt is not None:
                conf = 0.92 if abs(qty * unit - amt) <= max(ctx.tolerance, 0.011 * abs(amt)) else 0.65
            elif amt is not None:
                conf = 0.78
            else:
                conf = 0.6
            item = LineItemValue(desc, qty, unit, amt, round(conf * (0.75 + 0.25 * (1 if ln.source != "ocr" else 0.9)), 3), ln.page, clip(ln.plain))
            items.append(item)
            last_item = item
            last_row_idx = i
        elif not values and last_item is not None and i - last_row_idx == 1 and desc_col is not None:
            first = ln.cells[0]
            if abs(first.col - desc_col.col) <= 3 and len(ln.plain) < 80 and not ln.plain.rstrip().endswith(":") and not L.NON_NAME_LABEL.match(ln.plain):
                last_item.description = (last_item.description or "") + " " + ln.plain
                last_item.evidence = clip((last_item.evidence or "") + " | " + ln.plain)
                last_row_idx = i
    return items


_INT_LIKE = re.compile(r"^\d{1,4}$")
_QTY_PREFIX = re.compile(r"^(\d{1,5}(?:[.,]\d{1,3})?)\s*[x×*]\s+(?=\S)", re.I)


def _take_qty_prefix(desc: str, qty: float | None) -> tuple[str, float | None]:
    m = _QTY_PREFIX.match(desc)
    if not m or qty is not None:
        return desc, qty
    try:
        return desc[m.end():].strip(), float(m.group(1).replace(",", "."))
    except ValueError:
        return desc, qty


def _assign_numbers(nums: list[Money]) -> tuple[float | None, float | None, float | None, float]:
    """Guess (quantity, unit_price, amount, confidence) from the numbers at the end of a row.

    With three or more numbers the last three are checked against quantity x price = amount. With fewer,
    only what can be told apart is filled in, and the rest stays empty.
    """
    vals = [n.as_float() for n in nums]
    if len(vals) >= 3:
        q, u, a = vals[-3:]
        if abs(q * u - a) <= max(0.05, 0.011 * abs(a)):
            return q, u, a, 0.7
        return None, None, a, 0.45
    if len(vals) == 2:
        n1, n2 = vals
        if n1 == n2:
            return 1.0, n1, n2, 0.6
        if float(n1).is_integer() and 0 < n1 <= 1000 and n2 >= n1:
            return n1, None, n2, 0.55
        return None, n1, n2, 0.5
    return None, None, vals[0], 0.55


def _items_from_shape(lines: list[DocLine], ctx: "Ctx") -> list[LineItemValue]:
    """No usable table header (poor scan, receipt). Read rows that look like a description followed by numbers."""
    stop = len(lines)
    for i, ln in enumerate(lines):
        if _is_totals_line(ln) and any(_cell_money(c, ctx.decimal_hint) for c in ln.cells[1:]):
            stop = i
            break
    rows: list[LineItemValue] = []
    for ln in lines[:stop]:
        if ln.blank or len(ln.cells) < 2 or _STOP_HEAD.match(ln.cells[0].text):
            continue
        cells = list(ln.cells)
        if len(cells) >= 3 and _INT_LIKE.match(cells[0].text) and not parse_quantity(cells[1].text):
            cells = cells[1:]  # row number
        k = len(cells)
        while k > 0 and _cell_money(cells[k - 1], ctx.decimal_hint) is not None:
            k -= 1
        numeric = cells[k:]
        text_cells = cells[:k]
        if not (1 <= len(numeric) <= 5) or not text_cells:
            continue
        desc = " ".join(c.text for c in text_cells).strip(" -|")
        if len(re.findall(r"[A-Za-z]", desc)) < 3 or L.NON_NAME_LABEL.match(desc) and ":" in desc:
            continue
        if L.classify_amount_label(desc):
            continue
        nums = [m for m in (_cell_money(c, ctx.decimal_hint) for c in numeric) if m is not None]
        if not nums:
            continue
        # A date or phone number is not a price.
        if any(len(re.sub(r"\D", "", c.text)) >= 9 for c in numeric):
            continue
        qty, unit, amt, conf = _assign_numbers(nums)
        desc, qty = _take_qty_prefix(desc, qty)
        rows.append(LineItemValue(desc, qty, unit, amt, round(conf * (0.75 + 0.25 * (1 if ln.source != "ocr" else 0.9)), 3), ln.page, clip(ln.plain)))
    # One stray match is more likely a header block than a table.
    return rows if len(rows) >= 2 else []


_QTY_TIMES = re.compile(r"^(?P<q>\d+(?:[.,]\d+)?)\s*(?:x|X|\u00d7|@)\s*(?P<u>.+)$")


def _items_from_two_lines(lines: list[DocLine], ctx: "Ctx") -> list[LineItemValue]:
    """Till receipts: the item name on one line, then `2 x 12.50` and the amount on the line below it."""
    stop = len(lines)
    for i, ln in enumerate(lines):
        if _is_totals_line(ln) and any(_cell_money(c, ctx.decimal_hint) for c in ln.cells[1:]):
            stop = i
            break
    rows: list[LineItemValue] = []
    prev: DocLine | None = None
    for ln in lines[:stop]:
        if ln.blank:
            continue
        cells = list(ln.cells)
        if len(cells) >= 2:
            m = _QTY_TIMES.match(cells[0].text)
            if m and prev is not None and len(prev.cells) == 1 and len(re.findall(r"[A-Za-z]", prev.cells[0].text)) >= 3:
                unit = parse_amount(m.group("u"), ctx.decimal_hint)
                amt = _cell_money(cells[-1], ctx.decimal_hint)
                qty = parse_quantity(m.group("q"))
                if unit is not None and amt is not None and qty:
                    rows.append(LineItemValue(prev.cells[0].text.strip(" -|"), float(qty), unit.as_float(), amt.as_float(), round(0.8 * (1 if ln.source != "ocr" else 0.9), 3), ln.page, clip(prev.plain + " / " + ln.plain)))
                    prev = None
                    continue
        prev = ln
    return rows if len(rows) >= 1 else []


def extract_line_items(lines: list[DocLine], ctx: "Ctx") -> list[LineItemValue]:
    items = _items_from_header(lines, ctx)
    if items:
        return items
    two = _items_from_two_lines(lines, ctx)
    if two:
        return two
    return _items_from_shape(lines, ctx)
