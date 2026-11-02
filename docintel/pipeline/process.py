"""Read every page of a file (text layer, OCR or spreadsheet) and extract records from it.

`read_pages` is shared by the background worker and by `process_file`, which runs the whole
pipeline on one file without a database. The evaluation and the command line tool use the latter.
"""

from __future__ import annotations

import logging
import time
from concurrent.futures import FIRST_COMPLETED, Future, wait
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

from ..config import Settings
from ..extraction.document import ExtractionOutcome, PageText, extract_document
from ..extraction.llm import LLMExtractor
from ..extraction.schema import ExtractedRecord
from ..validation.rules import Issue, RecordData, validate_record
from . import open_reader, sniff
from .ocr import OcrPool, OcrTask
from .types import CorruptFileError, DocIntelError, OcrError, PageContent, UnsupportedFileError

log = logging.getLogger(__name__)


class Cancelled(Exception):
    """Raised between pages when the document was cancelled or the worker is shutting down."""


def source_kind(file_type: str, pages: Iterable[PageContent]) -> str:
    sources = {p.source for p in pages}
    if file_type == "pdf":
        if sources <= {"text"}:
            return "text_pdf"
        if sources <= {"ocr", "failed"}:
            return "scanned_pdf"
        return "mixed_pdf"
    if file_type == "image":
        return "image"
    return "csv" if file_type == "csv" else "spreadsheet"


def _failed_page(index: int, exc: BaseException) -> PageContent:
    code = getattr(exc, "code", "PAGE_FAILED")
    return PageContent(page_no=index + 1, source="failed", text="", warnings=[f"{code}: {exc}"], meta={"error_code": code})


def read_pages(
    reader,
    path: str,
    file_type: str,
    settings: Settings,
    pool: OcrPool | None = None,
    *,
    skip: Iterable[int] = (),
    on_page: Callable[[PageContent], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> list[PageContent]:
    """Return all pages in order. `skip` holds page numbers (1 based) that are already stored from an earlier attempt.

    A page that cannot be read becomes a page with source "failed" and the others carry on. If every
    page fails, the first error is raised so the document fails with a meaningful code.
    """
    skipped = set(skip)
    results: dict[int, PageContent] = {}
    first_error: BaseException | None = None
    need_ocr: list[int] = []

    def done(pc: PageContent) -> None:
        results[pc.page_no] = pc
        if on_page:
            on_page(pc)

    for i in range(reader.page_count):
        if (i + 1) in skipped:
            continue
        if should_stop and should_stop():
            raise Cancelled()
        try:
            pc = reader.try_text_page(i)
        except DocIntelError as exc:
            first_error = first_error or exc
            done(_failed_page(i, exc))
            continue
        if pc is None:
            need_ocr.append(i)
        else:
            done(pc)

    if need_ocr:
        own_pool = pool is None
        active = pool or OcrPool(settings, processes=1)
        kind = "pdf" if file_type == "pdf" else "image"
        window = max(2, active.n * 2)
        pending: dict[Future, tuple[int, float]] = {}
        todo = list(need_ocr)
        try:
            while todo or pending:
                if should_stop and should_stop():
                    raise Cancelled()
                while todo and len(pending) < window:
                    idx = todo.pop(0)
                    pending[active.submit(OcrTask(kind, path, idx))] = (idx, time.monotonic())
                finished, _ = wait(list(pending), timeout=1.0, return_when=FIRST_COMPLETED)
                for fut in finished:
                    idx, _t0 = pending.pop(fut)
                    try:
                        done(fut.result())
                    except DocIntelError as exc:
                        first_error = first_error or exc
                        done(_failed_page(idx, exc))
                    except Exception as exc:  # noqa: BLE001 - a crashed OCR process must not take the document down
                        err = OcrError(f"OCR crashed on page {idx + 1}: {exc}")
                        first_error = first_error or err
                        done(_failed_page(idx, err))
                        active.restart()
                        for f2, (i2, _t) in list(pending.items()):  # in flight tasks died with the old pool
                            pending.pop(f2)
                            todo.insert(0, i2)
                        break
                now = time.monotonic()
                stuck = [f for f, (_i, t0) in pending.items() if now - t0 > settings.ocr_page_timeout_seconds]
                if stuck:
                    for f in stuck:
                        idx, _t = pending.pop(f)
                        err = OcrError(f"OCR took longer than {settings.ocr_page_timeout_seconds} seconds on page {idx + 1}", code="OCR_TIMEOUT")
                        first_error = first_error or err
                        done(_failed_page(idx, err))
                    active.restart()
                    for f2, (i2, _t) in list(pending.items()):
                        pending.pop(f2)
                        todo.insert(0, i2)
        finally:
            if own_pool:
                active.close()

    ordered = [results[k] for k in sorted(results)]
    if ordered and all(p.source == "failed" for p in ordered) and not skipped and first_error is not None:
        raise first_error
    return ordered


def to_page_texts(pages: Iterable[PageContent]) -> list[PageText]:
    return [PageText(p.page_no, p.text, p.ocr_confidence, p.source) for p in pages if p.source != "failed"]


def record_data(rec: ExtractedRecord) -> RecordData:
    return RecordData(kind=rec.kind, fields=rec.fields, line_items=rec.line_items, method=rec.method, ocr_confidence=rec.ocr_confidence, dropped=rec.dropped, disagreements=rec.disagreements)


def attach_ocr_confidence(records: list[ExtractedRecord], pages: list[PageContent]) -> None:
    by_no = {p.page_no: p for p in pages}
    for rec in records:
        if rec.page_start is None:
            continue
        confs = [by_no[n].ocr_confidence for n in range(rec.page_start, (rec.page_end or rec.page_start) + 1) if n in by_no and by_no[n].ocr_confidence is not None]
        if confs:
            rec.ocr_confidence = round(sum(confs) / len(confs), 4)


@dataclass
class ProcessResult:
    file_type: str
    pages: list[PageContent]
    outcome: ExtractionOutcome
    issues: list[list[Issue]]  # one list per record, same order as outcome.records
    warnings: list[str] = field(default_factory=list)
    timings: dict[str, float] = field(default_factory=dict)

    @property
    def records(self) -> list[ExtractedRecord]:
        return self.outcome.records

    @property
    def kind(self) -> str:
        return source_kind(self.file_type, self.pages)


def process_file(
    path: str | Path,
    settings: Settings,
    *,
    llm: LLMExtractor | None = None,
    pool: OcrPool | None = None,
    filename: str | None = None,
) -> ProcessResult:
    """Run the whole pipeline on one file: detect, read, extract, validate. Raises DocIntelError on failure."""
    path = str(path)
    t0 = time.perf_counter()
    sniffed = sniff(path, filename)
    if sniffed.file_type is None:
        raise UnsupportedFileError(sniffed.reason, code=sniffed.code)
    reader = open_reader(path, sniffed.file_type, settings)
    warnings = list(sniffed.warnings) + list(getattr(reader, "warnings", []))
    try:
        t1 = time.perf_counter()
        pages = read_pages(reader, path, sniffed.file_type, settings, pool)
        t2 = time.perf_counter()
        sheets = reader.sheets if sniffed.file_type in ("xlsx", "xls", "csv") else None
        outcome = extract_document(to_page_texts(pages), sheets, settings, llm)
    finally:
        reader.close()
    t3 = time.perf_counter()
    attach_ocr_confidence(outcome.records, pages)
    issues = [validate_record(record_data(r), settings) for r in outcome.records]
    for p in pages:
        warnings.extend(f"page {p.page_no}: {w}" for w in p.warnings)
    t4 = time.perf_counter()
    return ProcessResult(
        sniffed.file_type, pages, outcome, issues, warnings,
        {"open_s": t1 - t0, "read_s": t2 - t1, "extract_s": t3 - t2, "validate_s": t4 - t3, "total_s": t4 - t0},
    )
