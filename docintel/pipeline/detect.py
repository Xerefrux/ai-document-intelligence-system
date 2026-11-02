"""Decide what a file really is from its first bytes, not from its name.

Upload accepts PDF, JPG, PNG, TIFF, XLSX, XLS and CSV. A file whose name and content disagree
(a PNG called invoice.pdf) is processed as what it is. A file that is something else entirely
(an executable called invoice.pdf) is rejected.
"""

from __future__ import annotations

import zipfile
from dataclasses import dataclass, field
from pathlib import Path

EXT_FAMILY = {
    ".pdf": "pdf",
    ".jpg": "image",
    ".jpeg": "image",
    ".png": "image",
    ".tif": "image",
    ".tiff": "image",
    ".xlsx": "xlsx",
    ".xls": "xls",
    ".csv": "csv",
}
SUPPORTED_EXTENSIONS = tuple(EXT_FAMILY)

MIME = {"pdf": "application/pdf", "image": "image/*", "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "xls": "application/vnd.ms-excel", "csv": "text/csv"}

_KNOWN_OTHER = [
    (b"MZ", "a Windows executable"),
    (b"\x7fELF", "an executable"),
    (b"GIF8", "a GIF image"),
    (b"BM", "a BMP image"),
    (b"RIFF", "a RIFF container (WebP/WAV)"),
    (b"%!PS", "a PostScript file"),
    (b"{\\rtf", "an RTF document"),
    (b"\x1f\x8b", "a gzip archive"),
    (b"7z\xbc\xaf", "a 7z archive"),
    (b"Rar!", "a RAR archive"),
]


@dataclass
class Sniffed:
    file_type: str | None  # pdf | image | xlsx | xls | csv | None when unsupported
    reason: str = ""  # why it was rejected
    warnings: list[str] = field(default_factory=list)
    code: str = "UNSUPPORTED_TYPE"  # stable error code when file_type is None


def _looks_like_text(head: bytes) -> bool:
    if not head:
        return False
    if b"\x00" in head:
        # UTF-16 text has NULs; accept it only with a BOM
        return head.startswith((b"\xff\xfe", b"\xfe\xff"))
    printable = sum(1 for b in head if b in (9, 10, 13) or 32 <= b < 127 or b >= 128)
    return printable / len(head) > 0.95


def sniff(path: str | Path, filename: str | None = None) -> Sniffed:
    path = Path(path)
    name = filename or path.name
    ext = Path(name).suffix.lower()
    if ext not in EXT_FAMILY:
        return Sniffed(None, f"File type {ext or '(no extension)'} is not supported. Use PDF, JPG, PNG, TIFF, XLSX, XLS or CSV.")
    try:
        with open(path, "rb") as fh:
            head = fh.read(8192)
    except OSError as exc:
        return Sniffed(None, f"File could not be read: {exc}")
    if not head:
        return Sniffed(None, "File is empty.", code="EMPTY_FILE")

    found: str | None = None
    if b"%PDF-" in head[:1024]:
        found = "pdf"
    elif head.startswith(b"\xff\xd8\xff") or head.startswith(b"\x89PNG\r\n\x1a\n") or head[:4] in (b"II*\x00", b"MM\x00*", b"II+\x00", b"MM\x00+"):
        found = "image"
    elif head.startswith(b"PK\x03\x04") or head.startswith(b"PK\x05\x06"):
        try:
            with zipfile.ZipFile(path) as zf:
                names = set(zf.namelist())
            found = "xlsx" if ("[Content_Types].xml" in names and any(n.startswith("xl/") for n in names)) else "zip"
        except zipfile.BadZipFile:
            # Header says zip but the archive is damaged. If the name says xlsx, let the reader report it.
            found = "xlsx" if ext == ".xlsx" else "zip"
    elif head.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        found = "xls" if ext in (".xls", ".xlsx") else "ole"
    else:
        for sig, what in _KNOWN_OTHER:
            if head.startswith(sig):
                return Sniffed(None, f"File content is {what}, not a supported document type.")
        stripped = head.lstrip().lower()
        if stripped.startswith((b"<!doctype html", b"<html", b"<?xml", b"<svg")):
            return Sniffed(None, "File content is markup, not a supported document type.")
        if ext == ".csv" and _looks_like_text(head):
            found = "csv"

    if found in ("zip", "ole"):
        return Sniffed(None, "File is an archive or Office document that is not an Excel workbook.")
    if found is None:
        return Sniffed(None, f"File content does not look like a {ext[1:].upper()} file (damaged, or renamed from another format).")

    warnings: list[str] = []
    expected = EXT_FAMILY[ext]
    if found != expected:
        warnings.append(f"Name says {ext[1:].upper()} but the content is {found}; processed as {found}.")
    return Sniffed(found, "", warnings)
