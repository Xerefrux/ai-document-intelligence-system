"""Cut a multi-page document into invoices.

A PDF of 1000 pages is usually many invoices stacked together. A page that carries a labelled invoice
number different from the one before it starts a new invoice; pages without a number (the second page
of a long invoice, terms and conditions) belong to the invoice before them.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import labels as L
from .lines import DocLine
from .normalize import normalize_invoice_number
from .rules import Ctx, amount_hits, clean_invoice_number, labeled_hits


@dataclass
class Segment:
    start: int  # 0 based index into the list of pages
    end: int  # inclusive
    number: str | None = None


def _page_number(lines: list[DocLine]) -> str | None:
    """The labelled invoice number on a page, if there is a clear one."""
    best: tuple[float, str] | None = None
    for h in labeled_hits(lines, L.INVOICE_NUMBER):
        if h.weight < 2.0:
            continue
        val = clean_invoice_number(h.text)
        if val and (best is None or h.weight > best[0]):
            best = (h.weight, val)
    return best[1] if best else None


def _looks_like_invoice_page(lines: list[DocLine]) -> bool:
    top = [ln for ln in lines if not ln.blank][:10]
    for ln in top:
        for c in ln.cells:
            if L.TITLE_WORDS.match(c.text.strip()) and not c.text.lower().startswith("page"):
                return True
    return False


def split_invoices(page_lines: list[list[DocLine]], ctx: Ctx) -> list[Segment]:
    n = len(page_lines)
    if n == 0:
        return []
    if n == 1:
        return [Segment(0, 0, None)]
    segs: list[Segment] = []
    cur_start: int | None = None
    cur_key: str | None = None
    cur_num: str | None = None
    cur_has_total = False
    for i, lines in enumerate(page_lines):
        num = _page_number(lines)
        key = normalize_invoice_number(num) if num else None
        has_total = any(h.kind == "total" and h.rank >= 3 for h in amount_hits(lines, ctx))
        starts = False
        if cur_start is None:
            starts = bool(key) or has_total or _looks_like_invoice_page(lines)
        elif key and key != cur_key:
            starts = True
        elif not key and cur_has_total and has_total and _looks_like_invoice_page(lines):
            starts = True  # a second receipt with no number of its own
        if starts:
            if cur_start is not None:
                segs.append(Segment(cur_start, i - 1, cur_num))
            cur_start, cur_key, cur_num, cur_has_total = i, key, num, has_total
        elif cur_start is not None:
            cur_has_total = cur_has_total or has_total
    if cur_start is None:
        return [Segment(0, n - 1, None)]
    segs.append(Segment(cur_start, n - 1, cur_num))
    # Pages before the first invoice (a cover sheet) are folded into it so no text is lost.
    segs[0] = Segment(0, segs[0].end, segs[0].number)
    return segs
