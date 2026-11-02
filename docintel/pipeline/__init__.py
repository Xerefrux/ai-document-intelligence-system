"""Document readers. `open_reader` returns an object with page_count, try_text_page(i) and close()."""

from __future__ import annotations

from ..config import Settings
from .detect import SUPPORTED_EXTENSIONS, Sniffed, sniff  # noqa: F401
from .image import ImageReader
from .pdf import PdfReader
from .spreadsheet import SheetReader
from .types import PageContent, UnsupportedFileError  # noqa: F401


def open_reader(path: str, file_type: str, settings: Settings):
    if file_type == "pdf":
        return PdfReader(path, settings)
    if file_type == "image":
        return ImageReader(path, settings)
    if file_type in ("xlsx", "xls", "csv"):
        return SheetReader(path, file_type, settings)
    raise UnsupportedFileError(f"Unsupported file type: {file_type}")
