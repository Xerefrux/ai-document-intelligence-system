"""Rules based extraction of one invoice from its lines.

The extractor looks for labels ("Invoice No", "Bill To", "Subtotal"...) and reads the value beside
or below them. Every value comes with the line it was read from (evidence) and a confidence. When
nothing convincing is found the field stays empty: the extractor never fills a gap with a guess.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal

from . import labels as L
from .items import extract_line_items, header_roles
from .lines import Cell, DocLine, clip
from .normalize import (
    Money,
    ParsedDate,
    detect_currency,
    detect_decimal_style,
    find_dates,
    find_money,
    infer_date_order,
    parse_amount,
)
from .schema import ExtractedRecord, FieldValue


@dataclass
class Ctx:
    order: str | None = None  # 'dmy' | 'mdy' | None (unknown)
    decimal_hint: str | None = None
    tolerance: float = 0.05
    force_order: bool = False


def make_ctx(lines: list[DocLine], date_order_setting: str, tolerance: float) -> Ctx:
    text = "\n".join(ln.plain for ln in lines)
    if date_order_setting in ("dmy", "mdy"):
        order = date_order_setting
    else:
        order = infer_date_order(text)
    return Ctx(order=order, decimal_hint=detect_decimal_style(text), tolerance=tolerance)


# ---------------------------------------------------------------------------------------- helpers


def ocr_factor(ln: DocLine) -> float:
    """Scale confidence by how well the OCR read this page. Text from a digital file scores 1."""
    if ln.source != "ocr" or ln.ocr_conf is None:
        return 1.0
    c = ln.ocr_conf
    if c >= 0.90:
        return 1.0
    if c <= 0.60:
        return 0.75
    return 0.75 + 0.25 * (c - 0.60) / 0.30


def _aligned_below(lines: list[DocLine], i: int, cell: Cell) -> tuple[Cell, int] | None:
    for j in range(i + 1, min(i + 4, len(lines))):
        ln = lines[j]
        if ln.blank:
            continue
        best: tuple[float, Cell] | None = None
        for c in ln.cells:
            overlap = min(c.end, cell.end) - max(c.col, cell.col)
            d = min(abs(c.col - cell.col), abs(c.center - cell.center), abs(c.end - cell.end))
            if overlap > 0 or d <= 6:
                if best is None or d < best[0]:
                    best = (d, c)
        return (best[1], j) if best else None
    return None


def _aligned_above(lines: list[DocLine], i: int, cell: Cell) -> Cell | None:
    for j in range(i - 1, max(i - 3, -1), -1):
        ln = lines[j]
        if ln.blank:
            continue
        best: tuple[float, Cell] | None = None
        for c in ln.cells:
            overlap = min(c.end, cell.end) - max(c.col, cell.col)
            d = min(abs(c.col - cell.col), abs(c.center - cell.center), abs(c.end - cell.end))
            if overlap > 0 or d <= 6:
                if best is None or d < best[0]:
                    best = (d, c)
        return best[1] if best else None
    return None


@dataclass
class Hit:
    text: str
    weight: float
    line_idx: int
    page: int
    evidence: str
    via: str  # same_cell | next_cell | below
    factor: float = 1.0


def labeled_hits(lines: list[DocLine], patterns, *, near: int | None = None) -> list[Hit]:
    hits: list[Hit] = []
    for i, ln in enumerate(lines):
        if ln.blank:
            continue
        if near is not None and i > near:
            break
        for ci, cell in enumerate(ln.cells):
            txt = L.repair_label(cell.text)
            for pat, weight in patterns:
                m = pat.match(txt)
                if not m:
                    continue
                v = (m.groupdict().get("v") or "").strip(" :-\t")
                ev = clip(ln.plain)
                f = ocr_factor(ln)
                if v:
                    hits.append(Hit(v, weight, i, ln.page, ev, "same_cell", f))
                else:
                    if ci + 1 < len(ln.cells):
                        hits.append(Hit(ln.cells[ci + 1].text, weight, i, ln.page, ev, "next_cell", f))
                    below = _aligned_below(lines, i, cell)
                    if below:
                        bcell, bj = below
                        hits.append(Hit(bcell.text, weight - 0.3, i, ln.page, clip(ln.plain + " | " + lines[bj].plain), "below", f))
                break
    return hits


# ---------------------------------------------------------------------------------- invoice number

_STOP = {"date", "no", "number", "page", "of", "total", "due", "to", "from", "ref", "amount", "invoice", "bill"}
_INVNO_FALLBACK = re.compile(r"\b((?:INV|BILL|RCPT|REC|IN|SI|TI|CI|PI)[-/ ]?\d[\w\-/]{2,25})\b", re.I)


def clean_invoice_number(v: str) -> str | None:
    v = v.strip().strip(":#-.,;|\"'()[]").strip()
    if not v:
        return None
    parts = v.split()
    tok = parts[0].strip(":#-.,;|")
    if re.fullmatch(r"[A-Za-z]{1,5}", tok) and len(parts) > 1 and re.match(r"\d", parts[1]):
        nxt = parts[1].strip(":#,.;|")
        # "INV 1234" is one number, but "Date 25/07/2025" or "No 1234" start with a label word that is not part of it.
        tok = nxt if tok.lower() in _STOP else tok + " " + nxt
    tok = tok.strip(":#-.,;|")
    if not (2 <= len(tok) <= 40) or not re.search(r"\d", tok) or tok.lower() in _STOP:
        return None
    ds = find_dates(tok)
    if ds and len(ds[0].raw) == len(tok):
        return None
    if parse_amount(tok) is not None and re.fullmatch(r"[\d.,]+", tok) and ("." in tok or "," in tok):
        return None
    return tok


def extract_invoice_number(lines: list[DocLine], ctx: Ctx) -> FieldValue:
    best: tuple[float, str, Hit] | None = None
    for h in labeled_hits(lines, L.INVOICE_NUMBER):
        val = clean_invoice_number(h.text)
        if not val:
            continue
        score = h.weight + (0.6 if h.line_idx < 30 else 0.0) + (0.1 if h.via == "same_cell" else 0.0)
        if best is None or score > best[0]:
            best = (score, val, h)
    if best:
        _, val, h = best
        conf = min(0.97, 0.55 + 0.14 * h.weight) * h.factor
        return FieldValue(val, round(conf, 3), h.evidence, h.page)
    for i, ln in enumerate(lines[:40]):
        m = _INVNO_FALLBACK.search(ln.plain)
        if m:
            return FieldValue(m.group(1), round(0.5 * ocr_factor(ln), 3), clip(ln.plain), ln.page, note="No 'invoice number' label; read from a number pattern.")
    return FieldValue()


# ------------------------------------------------------------------------------------------ dates


@dataclass
class DateHit:
    parsed: ParsedDate
    kind: str  # invoice | due | other | none
    weight: float
    line_idx: int
    page: int
    evidence: str
    factor: float


def _label_candidates(lines: list[DocLine], i: int, ci: int, before: str) -> list[str]:
    """Where a date's label might be: in the same cell, the cell to the left, or the cell above it."""
    out: list[str] = []
    before = before.strip(" :-|")
    if before:
        out.append(before)
    ln = lines[i]
    if ci > 0:
        out.append(ln.cells[ci - 1].text)
    above = _aligned_above(lines, i, ln.cells[ci])
    if above:
        out.append(above.text)
    return out


def _classify_date_label(label: str) -> tuple[str, float] | None:
    for pat, w in L.DUE_DATE:
        if pat.match(label):
            return "due", w
    for pat, w in L.INVOICE_DATE:
        if pat.match(label):
            return "invoice", w
    if L.OTHER_DATE_LABEL.match(label.strip()):
        return "other", 0.0
    return None


def date_hits(lines: list[DocLine], ctx: Ctx) -> list[DateHit]:
    out: list[DateHit] = []
    for i, ln in enumerate(lines):
        for ci, cell in enumerate(ln.cells):
            for pd in find_dates(cell.text, ctx.order):
                kind, weight = "none", 0.0
                for cand in _label_candidates(lines, i, ci, cell.text[: pd.start]):
                    got = _classify_date_label(L.repair_label(cand))
                    if got:
                        kind, weight = got
                        break
                out.append(DateHit(pd, kind, weight, i, ln.page, clip(ln.plain), ocr_factor(ln)))
    return out


def _date_field(h: DateHit, base: float) -> FieldValue:
    conf = base * h.factor
    note = None
    if h.parsed.ambiguous:
        conf -= 0.25
        alt = h.parsed.alt.isoformat() if h.parsed.alt else "?"
        note = f"Ambiguous: read as day/month ({h.parsed.value.isoformat()}); month/day would be {alt}."
    fv = FieldValue(h.parsed.value.isoformat(), round(max(conf, 0.1), 3), h.evidence, h.page, note=note)
    return fv


def extract_dates(lines: list[DocLine], ctx: Ctx) -> tuple[FieldValue, FieldValue]:
    hits = date_hits(lines, ctx)
    inv_hits = [h for h in hits if h.kind == "invoice"]
    due_hits = [h for h in hits if h.kind == "due"]
    invoice = FieldValue()
    if inv_hits:
        h = max(inv_hits, key=lambda x: (x.weight, -x.line_idx))
        invoice = _date_field(h, min(0.97, 0.55 + 0.14 * h.weight))
    else:
        loose = [h for h in hits if h.kind == "none" and h.line_idx < 40]
        if loose:
            h = loose[0]
            distinct = {x.parsed.value for x in loose}
            invoice = _date_field(h, 0.6 if len(distinct) == 1 else 0.5)
            invoice.note = ((invoice.note + " ") if invoice.note else "") + "No 'date' label; took the first date near the top."
    due = FieldValue()
    if due_hits:
        h = max(due_hits, key=lambda x: (x.weight, -x.line_idx))
        due = _date_field(h, min(0.95, 0.55 + 0.13 * h.weight))
    return invoice, due


# ----------------------------------------------------------------------------------------- parties


def is_address_like(t: str) -> bool:
    if L.ADDRESS_WORDS.search(t) and re.search(r"\d", t):
        return True
    if L.COUNTRY_WORDS.search(t) and "," in t:
        return True
    if re.search(r"\b\d{4,6}\b", t) and "," in t:
        return True
    if re.match(r"^\d+[\s,/-]", t) and L.ADDRESS_WORDS.search(t):
        return True
    return False


def is_labelish(t: str) -> bool:
    if not L.NON_NAME_LABEL.match(t):
        return False
    return bool(re.match(r"^[\w\s/.&-]{1,30}?\s*[:#]", t)) or len(t.split()) <= 2


def looks_like_name(t: str) -> bool:
    t = t.strip(" ,;|")
    if not (2 <= len(t) <= 80):
        return False
    letters = sum(ch.isalpha() for ch in t)
    if letters < 2 or letters < 0.5 * len(t.replace(" ", "")):
        return False
    if L.EMAIL_RX.search(t) or L.URL_RX.search(t):
        return False
    if len(re.sub(r"\D", "", t)) >= 8 and L.PHONE_RX.search(t):
        return False
    if is_address_like(t) or is_labelish(t):
        return False
    if find_dates(t) or (parse_amount(t) is not None):
        return False
    return True


def _party_from_hits(lines: list[DocLine], patterns) -> tuple[FieldValue, set[int]]:
    """Find a party name beside or below a label. Returns the field and the line indexes of its block."""
    best: tuple[float, str, int, int, str, str] | None = None
    block: set[int] = set()
    for i, ln in enumerate(lines):
        if ln.blank or i > 120:
            continue
        for ci, cell in enumerate(ln.cells):
            txt = L.repair_label(cell.text)
            for pat, weight in patterns:
                m = pat.match(txt)
                if not m:
                    continue
                v = (m.groupdict().get("v") or "").strip(" :-\t,")
                cand: list[tuple[str, int]] = []
                if v:
                    cand.append((v, i))
                else:
                    if ci + 1 < len(ln.cells) and looks_like_name(ln.cells[ci + 1].text):
                        cand.append((ln.cells[ci + 1].text, i))
                    # name on the following lines, in the same column as the label
                    for j in range(i + 1, min(i + 5, len(lines))):
                        nl = lines[j]
                        if nl.blank:
                            if cand:
                                break
                            continue
                        near = [c for c in nl.cells if abs(c.col - cell.col) <= 8 or (c.col <= cell.end and c.end >= cell.col)]
                        near.sort(key=lambda c: abs(c.col - cell.col))
                        if not near:
                            continue  # the other column has text on this line; ours has none
                        if looks_like_name(near[0].text):
                            cand.append((near[0].text, j))
                            break
                        if near and not (is_address_like(near[0].text) or is_labelish(near[0].text)):
                            continue
                        break
                for name, j in cand:
                    name = name.strip(" ,;|")
                    if looks_like_name(name):
                        score = weight + (0.3 if j == i else 0.0)
                        if best is None or score > best[0]:
                            best = (score, name, j, ln.page, clip(lines[j].plain), ci)
                            block = set(range(i, min(j + 4, len(lines))))
                        break
                break
    if best:
        score, name, j, page, ev, _ = best
        conf = min(0.95, 0.5 + 0.15 * score) * ocr_factor(lines[j])
        return FieldValue(name, round(conf, 3), ev, page), block
    return FieldValue(), block


_BAD_SUPPLIER = re.compile(r"^(?:page|date|ref|inv)\b", re.I)


def guess_supplier(lines: list[DocLine], skip_idx: set[int], skip_text: set[str]) -> FieldValue:
    """The business name is normally the first prominent text at the top of the first page."""
    first_page = lines[0].page if lines else 1
    cands: list[tuple[float, int, str, DocLine]] = []
    seen = 0
    for i, ln in enumerate(lines):
        if ln.page != first_page:
            break
        if ln.blank:
            continue
        seen += 1
        if seen > 14 or header_roles(ln):
            break
        if i in skip_idx:
            continue
        for ci, cell in enumerate(ln.cells):
            t = cell.text.strip(" ,;|")
            if L.classify_amount_label(t):
                continue
            if not looks_like_name(t) and re.search(r"\s[-\u2013\u2014\u00b7\u2022|]\s", t):
                first = re.split(r"\s[-\u2013\u2014\u00b7\u2022|]\s", t, maxsplit=1)[0].strip(" ,;|")
                if looks_like_name(first):
                    t = first
            if not looks_like_name(t) or L.TITLE_WORDS.match(t) or _BAD_SUPPLIER.match(t) or t.casefold() in skip_text:
                continue
            if L.INVOICE_NUMBER[0][0].match(t) or L.INVOICE_DATE[0][0].match(t):
                continue
            score = max(0, 4 - seen) + (1.0 if ci == 0 else 0.0)
            if L.LEGAL_SUFFIX.search(t):
                score += 2.5
            words = t.split()
            if 2 <= len(words) <= 6:
                score += 1.0
            if t.isupper() or sum(w[0].isupper() for w in words if w[0].isalpha()) >= max(1, len(words) - 1):
                score += 0.5
            if any(ch.isdigit() for ch in t):
                score -= 1.5
            if len(t) < 4:
                score -= 2.0
            cands.append((score, i, t, ln))
    if not cands:
        return FieldValue()
    score, _, name, ln = max(cands, key=lambda c: (c[0], -c[1]))
    if score < 2.0:
        return FieldValue()
    conf = 0.85 if score >= 6 else 0.75 if score >= 4.5 else 0.6 if score >= 3 else 0.45
    return FieldValue(name, round(conf * ocr_factor(ln), 3), clip(ln.plain), ln.page, note="No supplier label; read from the top of the page.")


def extract_parties(lines: list[DocLine], ctx: Ctx, used_texts: set[str]) -> tuple[FieldValue, FieldValue]:
    customer, cust_block = _party_from_hits(lines, L.CUSTOMER)
    supplier, sup_block = _party_from_hits(lines, L.SUPPLIER)
    if supplier.value and customer.value and supplier.value.casefold() == customer.value.casefold():
        supplier = FieldValue()
    if not supplier.value:
        skip_text = set(used_texts)
        if customer.value:
            skip_text.add(customer.value.casefold())
        supplier = guess_supplier(lines, cust_block, skip_text)
        if supplier.value and customer.value and supplier.value.casefold() == customer.value.casefold():
            supplier = FieldValue()
    return supplier, customer


# ----------------------------------------------------------------------------------------- amounts


@dataclass
class AmountHit:
    kind: str
    rank: int
    money: Money
    line_idx: int
    page: int
    evidence: str
    label: str
    factor: float


_TRAIL_AMOUNT = re.compile(r"^(?P<l>.*?[A-Za-z\)])[\s:]*(?P<a>(?:[A-Z]{3}\s?)?[$€£¥₹৳]?\s?[-(]?\d[\d.,' ]*\)?(?:\s?[A-Z]{3})?)$")


def amount_hits(lines: list[DocLine], ctx: Ctx) -> list[AmountHit]:
    hits: list[AmountHit] = []
    for i, ln in enumerate(lines):
        if ln.blank:
            continue
        done = False
        for ci, cell in enumerate(ln.cells):
            label_cls = L.classify_amount_label(cell.text)
            if label_cls:
                kind, rank = label_cls
                money: Money | None = None
                for c in reversed(ln.cells[ci + 1:]):
                    m = parse_amount(c.text, ctx.decimal_hint)
                    if m is not None and not m.percent:
                        money = m
                        break
                ev = clip(ln.plain)
                if money is None:
                    below = _aligned_below(lines, i, cell)
                    if below:
                        m = parse_amount(below[0].text, ctx.decimal_hint)
                        if m is not None and not m.percent:
                            money = m
                            ev = clip(ln.plain + " | " + lines[below[1]].plain)
                if money is not None:
                    hits.append(AmountHit(kind, rank, money, i, ln.page, ev, cell.text, ocr_factor(ln)))
                    done = True
                break
            tm = _TRAIL_AMOUNT.match(L.repair_label(cell.text))
            if tm:
                cls = L.classify_amount_label(tm.group("l"))
                if cls:
                    money = parse_amount(tm.group("a"), ctx.decimal_hint)
                    if money is not None and not money.percent:
                        hits.append(AmountHit(cls[0], cls[1], money, i, ln.page, clip(ln.plain), tm.group("l"), ocr_factor(ln)))
                        done = True
                        break
        if done:
            continue
    return hits


_RANK_CONF = {5: 0.97, 4: 0.95, 3: 0.93, 2: 0.85, 1: 0.8}


def _fv(h: AmountHit, conf: float, note: str | None = None) -> FieldValue:
    return FieldValue(h.money.as_float(), round(min(conf, 0.99) * h.factor, 3), h.evidence, h.page, note=note)


def _balanced(sub: float | None, tax: float | None, disc: float | None, ship: float | None, total: float | None, tol: float) -> bool | None:
    """True/False when the amounts can be checked, None when there is too little to check."""
    if total is None or sub is None:
        return None
    expected = sub + (tax or 0.0) - abs(disc or 0.0) + (ship or 0.0)
    return abs(expected - total) <= max(tol, 1e-9)


def extract_amounts(lines: list[DocLine], ctx: Ctx) -> dict[str, FieldValue]:
    hits = amount_hits(lines, ctx)
    out: dict[str, FieldValue] = {}
    pos = lambda h: (h.page, h.line_idx)  # noqa: E731

    subs = sorted([h for h in hits if h.kind == "subtotal"], key=pos)
    taxes = sorted([h for h in hits if h.kind == "tax"], key=pos)
    discs = sorted([h for h in hits if h.kind == "discount"], key=pos)
    ships = sorted([h for h in hits if h.kind == "shipping"], key=pos)
    totals = sorted([h for h in hits if h.kind == "total"], key=lambda h: (-h.rank, tuple(-x for x in pos(h))))

    sub_h = subs[-1] if subs else None
    disc_h = discs[-1] if discs else None
    ship_h = ships[-1] if ships else None

    tax_fv: FieldValue | None = None
    tax_val: float | None = None
    if taxes:
        top = [h for h in taxes if h.rank == 2]
        if top:
            tax_fv, tax_val = _fv(top[-1], 0.9), top[-1].money.as_float()
        elif len(taxes) == 1:
            tax_fv, tax_val = _fv(taxes[0], 0.9), taxes[0].money.as_float()
        else:
            distinct = []
            for h in taxes:
                if all(abs(h.money.as_float() - d.money.as_float()) > 1e-9 or h.line_idx != d.line_idx for d in distinct):
                    distinct.append(h)
            vals = {h.money.as_float() for h in distinct}
            if len(vals) == 1:
                tax_fv, tax_val = _fv(distinct[-1], 0.8), distinct[-1].money.as_float()
            else:
                s = float(sum(Decimal(str(h.money.as_float())) for h in distinct))
                ev = " | ".join(clip(h.evidence, 60) for h in distinct)
                tax_fv = FieldValue(round(s, 4), round(0.8 * min(h.factor for h in distinct), 3), ev, distinct[-1].page, note=f"Sum of {len(distinct)} tax lines.")
                tax_val = round(s, 4)

    # Choose the total: highest ranked label, but prefer a candidate that makes the arithmetic work.
    total_h: AmountHit | None = None
    sub_v = sub_h.money.as_float() if sub_h else None
    disc_v = disc_h.money.as_float() if disc_h else None
    ship_v = ship_h.money.as_float() if ship_h else None
    if totals:
        total_h = totals[0]
        if sub_v is not None and _balanced(sub_v, tax_val, disc_v, ship_v, total_h.money.as_float(), ctx.tolerance) is False:
            for cand in totals[1:]:
                if _balanced(sub_v, tax_val, disc_v, ship_v, cand.money.as_float(), ctx.tolerance):
                    total_h = cand
                    break
    total_v = total_h.money.as_float() if total_h else None
    balanced = _balanced(sub_v, tax_val, disc_v, ship_v, total_v, ctx.tolerance)

    boost = 0.98 if balanced else None
    if sub_h:
        out["subtotal"] = _fv(sub_h, max(0.9, boost or 0))
    if tax_fv is not None:
        if boost:
            tax_fv.confidence = round(max(tax_fv.confidence, boost * (min(h.factor for h in taxes))), 3)
        out["tax"] = tax_fv
    if disc_h:
        out["discount"] = _fv(disc_h, 0.85)
    if ship_h:
        out["shipping"] = _fv(ship_h, 0.85)
    if total_h:
        base = _RANK_CONF.get(total_h.rank, 0.8)
        note = None
        same_rank = [h for h in totals if h.rank == total_h.rank and abs(h.money.as_float() - total_h.money.as_float()) > 1e-9]
        if same_rank and balanced is None:
            base -= 0.15
            note = "Several different totals were found; took the last one."
        out["total"] = _fv(total_h, max(base, boost or 0), note)
    return out


# ----------------------------------------------------------------------------------------- record


def extract_invoice(lines: list[DocLine], ctx: Ctx) -> ExtractedRecord:
    rec = ExtractedRecord(method="rules")
    rec.page_start = lines[0].page if lines else None
    rec.page_end = lines[-1].page if lines else None
    rec.text = "\n".join(ln.text for ln in lines)

    inv_no = extract_invoice_number(lines, ctx)
    inv_date, due_date = extract_dates(lines, ctx)
    used = {inv_no.value.casefold()} if inv_no.value else set()
    supplier, customer = extract_parties(lines, ctx, used)
    amounts = extract_amounts(lines, ctx)

    rec.fields["invoice_number"] = inv_no
    rec.fields["invoice_date"] = inv_date
    rec.fields["due_date"] = due_date
    rec.fields["supplier_name"] = supplier
    rec.fields["customer_name"] = customer
    for name in ("subtotal", "tax", "total", "discount", "shipping"):
        rec.fields[name] = amounts.get(name, FieldValue())

    text = "\n".join(ln.plain for ln in lines)
    prefer = [amounts[k].evidence for k in ("total", "subtotal") if k in amounts and amounts[k].evidence]
    code, ev, conf = detect_currency(text, prefer)
    rec.fields["currency"] = FieldValue(code, round(conf, 3), ev, None) if code else FieldValue()

    rec.line_items = extract_line_items(lines, ctx)
    ocr_lines = [ln.ocr_conf for ln in lines if ln.source == "ocr" and ln.ocr_conf is not None]
    rec.ocr_confidence = round(sum(ocr_lines) / len(ocr_lines), 3) if ocr_lines else None
    if due_date.value and not inv_date.value:
        rec.notes.append("A due date was found but no invoice date.")
    return rec
