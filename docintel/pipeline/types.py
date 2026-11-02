"""Shared types for the document readers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


class DocIntelError(Exception):
    """Base class. `code` is stable and shown to users; `retryable` tells the queue whether a retry can help."""

    code = "PROCESSING_ERROR"
    retryable = False

    def __init__(self, message: str, *, code: str | None = None, retryable: bool | None = None, details: dict | None = None):
        super().__init__(message)
        if code:
            self.code = code
        if retryable is not None:
            self.retryable = retryable
        self.details = details or {}


class CorruptFileError(DocIntelError):
    code = "CORRUPT_FILE"


class PasswordProtectedError(DocIntelError):
    code = "PASSWORD_PROTECTED"


class UnsupportedFileError(DocIntelError):
    code = "UNSUPPORTED_TYPE"


class LimitExceededError(DocIntelError):
    code = "LIMIT_EXCEEDED"


class OcrError(DocIntelError):
    code = "OCR_FAILED"
    retryable = True


class OcrUnavailableError(DocIntelError):
    code = "OCR_UNAVAILABLE"


@dataclass
class Segment:
    """A run of text with its bounding box. y grows downwards. Units are whatever the source uses."""

    text: str
    x0: float
    y0: float
    x1: float
    y1: float
    conf: float | None = None

    @property
    def h(self) -> float:
        return max(self.y1 - self.y0, 0.0)

    @property
    def w(self) -> float:
        return max(self.x1 - self.x0, 0.0)

    @property
    def yc(self) -> float:
        return (self.y0 + self.y1) / 2


@dataclass
class PageContent:
    """What a reader returns for one page of a PDF, one image frame, or one chunk of a sheet."""

    page_no: int  # 1 based
    source: str  # text | ocr | sheet | failed
    text: str
    label: str | None = None  # sheet name
    width: float | None = None
    height: float | None = None
    ocr_confidence: float | None = None
    warnings: list[str] = field(default_factory=list)
    grid: list[list[Any]] | None = None  # spreadsheets only, not stored
    meta: dict = field(default_factory=dict)

    @property
    def char_count(self) -> int:
        return len(self.text)


@dataclass
class SheetContent:
    index: int  # 1 based position in the workbook
    name: str
    rows: list[list[Any]]
    hidden: bool = False
    truncated: bool = False
    warnings: list[str] = field(default_factory=list)
