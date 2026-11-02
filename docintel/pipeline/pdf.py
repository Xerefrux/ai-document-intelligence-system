"""PDF reading with pypdfium2.

Each page is checked on its own. A page with a usable text layer is read directly (fast, exact).
A page without one, or with a text layer full of garbage, is handed to OCR. A scanned PDF, a digital
PDF and a mix of both all go through the same code.
"""

from __future__ import annotations

import logging
import re
import unicodedata

import pypdfium2 as pdfium

from ..config import Settings
from .layout import build_layout
from .pdfium_lock import PDFIUM_LOCK
from .types import CorruptFileError, LimitExceededError, PageContent, PasswordProtectedError, Segment

log = logging.getLogger(__name__)

_WS = re.compile(r"[ \t\r\n  -​  　]+")


def _clean(text: str) -> str:
    return _WS.sub(" ", text).strip()


def _garbage_ratio(text: str) -> float:
    """Share of characters that are replacement characters, private use glyphs or control codes."""
    if not text:
        return 0.0
    bad = 0
    for ch in text:
        if ch == "�":
            bad += 1
            continue
        cat = unicodedata.category(ch)
        if cat in ("Co", "Cn", "Cs") or (cat == "Cc" and ch not in "\t\n\r"):
            bad += 1
    return bad / len(text)


class PdfReader:
    kind = "pdf"

    def __init__(self, path: str, settings: Settings):
        self.path = path
        self.settings = settings
        try:
            with PDFIUM_LOCK:
                self.doc = pdfium.PdfDocument(path)
        except pdfium.PdfiumError as exc:
            msg = str(exc)
            if "password" in msg.lower():
                raise PasswordProtectedError("The PDF is password protected. Remove the password and upload it again.") from exc
            raise CorruptFileError(f"The PDF could not be opened ({msg}). It may be damaged or incomplete.") from exc
        except Exception as exc:  # noqa: BLE001
            raise CorruptFileError(f"The PDF could not be opened ({exc}).") from exc
        with PDFIUM_LOCK:
            self.page_count = len(self.doc)
        if self.page_count == 0:
            self.close()
            raise CorruptFileError("The PDF has no pages.")
        if self.page_count > settings.max_pdf_pages:
            self.close()
            raise LimitExceededError(
                f"The PDF has {self.page_count} pages; the limit is {settings.max_pdf_pages}.", code="TOO_MANY_PAGES",
                details={"pages": self.page_count, "limit": settings.max_pdf_pages},
            )

    def close(self) -> None:
        try:
            with PDFIUM_LOCK:
                self.doc.close()
        except Exception:  # pragma: no cover
            pass

    def try_text_page(self, index: int) -> PageContent | None:
        """Read a page from its text layer. Returns None when the page needs OCR."""
        with PDFIUM_LOCK:
            raw = self._text_segments(index)
        if raw is None:
            return None
        segments, width, height = raw
        joined = " ".join(s.text for s in segments)
        if len(joined.replace(" ", "")) < self.settings.text_layer_min_chars:
            return None
        if _garbage_ratio(joined) > 0.3:
            return None
        return PageContent(page_no=index + 1, source="text", text=build_layout(segments), width=float(width), height=float(height))

    def _text_segments(self, index: int) -> tuple[list[Segment], float, float] | None:
        """Everything that touches PDFium for one page. The caller holds PDFIUM_LOCK."""
        try:
            page = self.doc[index]
        except Exception as exc:  # noqa: BLE001
            raise CorruptFileError(f"Page {index + 1} could not be loaded: {exc}") from exc
        tp = None
        try:
            if page.get_rotation() != 0:
                return None  # rotated pages are rendered and read by OCR, which handles orientation
            width, height = page.get_size()
            tp = page.get_textpage()
            if tp.count_chars() < self.settings.text_layer_min_chars:
                return None
            segments: list[Segment] = []
            for k in range(tp.count_rects()):
                left, bottom, right, top = tp.get_rect(k)
                txt = _clean(tp.get_text_bounded(left, bottom, right, top))
                if txt:
                    segments.append(Segment(txt, left, height - top, right, height - bottom))
            return segments, width, height
        except pdfium.PdfiumError as exc:
            log.warning("pdfium could not read the text of page %d: %s", index + 1, exc)
            return None
        finally:
            if tp is not None:
                tp.close()
            page.close()
