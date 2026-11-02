"""From the pages of one document to its list of extracted records."""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from ..config import Settings
from ..pipeline.types import SheetContent
from .lines import build_lines
from .llm import LLMError, LLMExtractor
from .merge import merge_records
from .rules import Ctx, _balanced, extract_invoice, make_ctx
from .schema import REQUIRED_FIELDS, ExtractedRecord
from .segment import split_invoices
from .sheets import extract_from_sheet

log = logging.getLogger(__name__)


@dataclass
class PageText:
    page_no: int
    text: str
    ocr_confidence: float | None = None
    source: str = "text"


@dataclass
class ExtractionOutcome:
    records: list[ExtractedRecord] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    llm_calls: int = 0
    llm_failures: int = 0


def looks_like_invoice(rec: ExtractedRecord) -> bool:
    """A record needs at least an invoice number, a total or a line item. Otherwise it is a letter, a blank page or noise, and
    inventing an empty invoice for it would only clutter the lists."""
    return rec.value("invoice_number") is not None or rec.value("total") is not None or bool(rec.line_items)


def is_shaky(rec: ExtractedRecord, settings: Settings) -> bool:
    """True when the rules result is incomplete, low confidence or does not add up, so a second opinion helps."""
    if any(rec.value(f) is None for f in REQUIRED_FIELDS):
        return True
    if any(rec.conf(f) < settings.review_confidence for f in REQUIRED_FIELDS):
        return True
    bal = _balanced(rec.value("subtotal"), rec.value("tax"), rec.value("discount"), rec.value("shipping"), rec.value("total"), settings.amount_tolerance)
    if bal is False:
        return True
    return rec.overall_confidence() < 0.6


def _apply_llm(rec: ExtractedRecord, pages: list[PageText], llm: LLMExtractor, settings: Settings, out: ExtractionOutcome) -> ExtractedRecord:
    mode = settings.extraction_mode
    wanted = [(p.page_no, p.text) for p in pages if rec.page_start is None or rec.page_start <= p.page_no <= (rec.page_end or p.page_no)]
    if not wanted:
        return rec
    source = pages[0].source if pages else "text"
    confs = [p.ocr_confidence for p in pages if p.ocr_confidence is not None]
    try:
        out.llm_calls += 1
        got = llm.extract(wanted, ocr_conf=(sum(confs) / len(confs)) if confs else None, source=source)
    except LLMError as exc:
        out.llm_failures += 1
        rec.llm_error = str(exc)
        rec.notes.append(f"LLM extraction failed ({exc.kind}); the rules result is used.")
        return rec
    got.kind = rec.kind
    if mode == "llm" and not any(f.value is not None for f in got.fields.values()) and not got.line_items:
        rec.notes.append("The LLM found nothing; the rules result is used.")
        return rec
    merged = merge_records(rec, got, tolerance=settings.amount_tolerance, review_conf=settings.review_confidence)
    merged.dropped = got.dropped
    return merged


def extract_document(
    pages: list[PageText],
    sheets: list[SheetContent] | None,
    settings: Settings,
    llm: LLMExtractor | None = None,
) -> ExtractionOutcome:
    out = ExtractionOutcome()
    use_llm = bool(llm and llm.enabled and settings.extraction_mode != "rules")
    mode = settings.extraction_mode

    if sheets is not None:
        for sh in sheets:
            recs = extract_from_sheet(sh, settings.date_order, settings.amount_tolerance)
            if not recs and use_llm and sh.rows:
                text = "\n".join("\t".join("" if c is None else str(c) for c in row) for row in sh.rows[:300])
                p = PageText(sh.index, text, None, "sheet")
                stub = ExtractedRecord(kind="invoice", method="rules", sheet_name=sh.name, text=text)
                stub.page_start = stub.page_end = sh.index
                rec = _apply_llm(stub, [p], llm, settings, out)  # type: ignore[arg-type]
                if rec.method == "hybrid" and any(v.value is not None for v in rec.fields.values()):
                    rec.method = "llm"
                    recs = [rec]
            out.records.extend(recs)
        if not out.records:
            out.notes.append("No invoice or ledger table was recognised in the workbook.")
        return out

    page_lines = [build_lines([(p.page_no, p.text, p.ocr_confidence, p.source)]) for p in pages]
    doc_text = "\n".join(ln.plain for pl in page_lines[:50] for ln in pl)
    base_ctx = make_ctx([ln for pl in page_lines[:50] for ln in pl], settings.date_order, settings.amount_tolerance)
    segs = split_invoices(page_lines, base_ctx)
    if len(segs) > settings.max_records_per_document:
        out.notes.append(f"Only the first {settings.max_records_per_document} invoices were extracted.")
        segs = segs[: settings.max_records_per_document]

    recs: list[ExtractedRecord] = []
    for seg in segs:
        lines = [ln for pl in page_lines[seg.start : seg.end + 1] for ln in pl]
        ctx = make_ctx(lines, settings.date_order, settings.amount_tolerance)
        if ctx.order is None:
            ctx = Ctx(order=base_ctx.order, decimal_hint=ctx.decimal_hint, tolerance=ctx.tolerance)
        rec = extract_invoice(lines, ctx)
        rec.page_start, rec.page_end = pages[seg.start].page_no, pages[seg.end].page_no
        recs.append(rec)

    if use_llm:
        todo = [i for i, r in enumerate(recs) if mode in ("llm", "hybrid") or is_shaky(r, settings)]
        if todo:
            workers = max(1, min(settings.llm_concurrency, len(todo)))

            def run(i: int) -> tuple[int, ExtractedRecord]:
                local = ExtractionOutcome()
                r = _apply_llm(recs[i], pages, llm, settings, local)  # type: ignore[arg-type]
                return i, r, local  # type: ignore[return-value]

            if workers == 1:
                results = [run(i) for i in todo]
            else:
                with ThreadPoolExecutor(max_workers=workers) as ex:
                    results = list(ex.map(run, todo))
            for i, r, local in results:  # type: ignore[misc]
                recs[i] = r
                out.llm_calls += local.llm_calls
                out.llm_failures += local.llm_failures
    kept = [r for r in recs if looks_like_invoice(r)]
    if not kept and recs:
        out.notes.append("No invoice was found: there is no invoice number, no total and no line items. It may be a different kind of document, or the page may be unreadable.")
    out.records = kept
    return out
