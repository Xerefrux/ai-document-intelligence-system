"""Spreadsheet and CSV reading: XLSX (openpyxl), XLS (xlrd) and CSV.

Every sheet becomes a SheetContent, a grid of cell values that the spreadsheet extractor reads
directly. For storage and search each sheet is also rendered as tab separated text and cut into
pages of 300 rows, so a 100,000 row ledger does not become one giant text field.
"""

from __future__ import annotations

import codecs
import csv
import io
import logging
import zipfile
from datetime import date, datetime, time
from pathlib import Path
from typing import Any

from ..config import Settings
from .types import CorruptFileError, LimitExceededError, PageContent, PasswordProtectedError, SheetContent

log = logging.getLogger(__name__)

ROWS_PER_PAGE = 300
_OLE_ENCRYPTED = "EncryptedPackage".encode("utf-16-le")


def cell_text(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, datetime):
        return v.date().isoformat() if v.time() == time(0, 0) else v.isoformat(sep=" ")
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, float):
        if v != v or v in (float("inf"), float("-inf")):
            return ""
        if v.is_integer() and abs(v) < 1e15:
            return str(int(v))
        return f"{v:.10g}"
    return " ".join(str(v).replace("\t", " ").split())


def _trim_row(row: list[Any]) -> list[Any]:
    out = list(row)
    while out and (out[-1] is None or (isinstance(out[-1], str) and not out[-1].strip())):
        out.pop()
    return out


def _trim_rows(rows: list[list[Any]]) -> list[list[Any]]:
    while rows and not rows[-1]:
        rows.pop()
    return rows


# --------------------------------------------------------------------------- XLSX


def _check_zip(path: str, settings: Settings) -> None:
    try:
        with zipfile.ZipFile(path) as zf:
            infos = zf.infolist()
            if len(infos) > 20000:
                raise LimitExceededError("The workbook contains too many parts.", code="UNSAFE_ARCHIVE")
            total = sum(i.file_size for i in infos)
            if total > settings.max_zip_uncompressed_mb * 1024 * 1024:
                raise LimitExceededError(
                    f"The workbook expands to more than {settings.max_zip_uncompressed_mb} MB.", code="UNSAFE_ARCHIVE"
                )
            for i in infos:
                if i.compress_size and i.file_size > 10 * 1024 * 1024 and i.file_size / i.compress_size > 2000:
                    raise LimitExceededError("The workbook has a suspiciously compressed part.", code="UNSAFE_ARCHIVE")
    except zipfile.BadZipFile as exc:
        raise CorruptFileError("The workbook is damaged: it is not a complete .xlsx archive.") from exc


def _load_xlsx(path: str, settings: Settings) -> list[SheetContent]:
    import openpyxl

    _check_zip(path, settings)
    try:
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    except LimitExceededError:
        raise
    except Exception as exc:  # noqa: BLE001 - openpyxl raises many kinds of errors for bad files
        raise CorruptFileError(f"The workbook could not be read ({type(exc).__name__}: {exc}).") from exc
    sheets: list[SheetContent] = []
    try:
        for idx, ws in enumerate(wb.worksheets, 1):
            if len(sheets) >= settings.max_sheets:
                break
            try:
                ws.reset_dimensions()  # some writers store a wrong dimension; read until the data really ends
            except Exception:  # pragma: no cover
                pass
            rows: list[list[Any]] = []
            truncated = False
            try:
                for n, row in enumerate(ws.iter_rows(values_only=True), 1):
                    if n > settings.max_sheet_rows:
                        truncated = True
                        break
                    rows.append(_trim_row(list(row[: settings.max_sheet_cols])))
            except Exception as exc:  # noqa: BLE001
                raise CorruptFileError(f"Sheet '{ws.title}' could not be read ({type(exc).__name__}: {exc}).") from exc
            warnings = [f"Only the first {settings.max_sheet_rows:,} rows were read."] if truncated else []
            sheets.append(SheetContent(idx, ws.title, _trim_rows(rows), hidden=ws.sheet_state != "visible", truncated=truncated, warnings=warnings))
    finally:
        try:
            wb.close()
        except Exception:  # pragma: no cover
            pass
    return sheets


# --------------------------------------------------------------------------- XLS


def _load_xls(path: str, settings: Settings) -> list[SheetContent]:
    import xlrd
    from xlrd.xldate import xldate_as_datetime

    try:
        book = xlrd.open_workbook(path, on_demand=True, formatting_info=False)
    except Exception as exc:  # noqa: BLE001
        raw_head = b""
        try:
            raw_head = Path(path).read_bytes()[:200_000]
        except OSError:
            pass
        if "encrypt" in str(exc).lower() or _OLE_ENCRYPTED in raw_head:
            raise PasswordProtectedError("The workbook is password protected. Remove the password and upload it again.") from exc
        raise CorruptFileError(f"The workbook could not be read ({type(exc).__name__}: {exc}).") from exc
    sheets: list[SheetContent] = []
    try:
        for idx in range(book.nsheets):
            if len(sheets) >= settings.max_sheets:
                break
            sh = book.sheet_by_index(idx)
            rows: list[list[Any]] = []
            truncated = sh.nrows > settings.max_sheet_rows
            for r in range(min(sh.nrows, settings.max_sheet_rows)):
                vals: list[Any] = []
                for c in range(min(sh.ncols, settings.max_sheet_cols)):
                    cell = sh.cell(r, c)
                    t = cell.ctype
                    if t == xlrd.XL_CELL_DATE:
                        try:
                            vals.append(xldate_as_datetime(cell.value, book.datemode))
                        except Exception:  # noqa: BLE001
                            vals.append(None)
                    elif t == xlrd.XL_CELL_NUMBER:
                        vals.append(cell.value)
                    elif t == xlrd.XL_CELL_BOOLEAN:
                        vals.append(bool(cell.value))
                    elif t in (xlrd.XL_CELL_EMPTY, xlrd.XL_CELL_BLANK, xlrd.XL_CELL_ERROR):
                        vals.append(None)
                    else:
                        vals.append(str(cell.value))
                rows.append(_trim_row(vals))
            warnings = [f"Only the first {settings.max_sheet_rows:,} rows were read."] if truncated else []
            sheets.append(SheetContent(idx + 1, sh.name, _trim_rows(rows), hidden=sh.visibility != 0, truncated=truncated, warnings=warnings))
            book.unload_sheet(idx)
    except CorruptFileError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise CorruptFileError(f"The workbook could not be read ({type(exc).__name__}: {exc}).") from exc
    finally:
        try:
            book.release_resources()
        except Exception:  # pragma: no cover
            pass
    return sheets


# --------------------------------------------------------------------------- CSV


def decode_bytes(raw: bytes) -> tuple[str, str]:
    """Decode CSV bytes. Returns the text and the encoding that worked."""
    if raw.startswith(codecs.BOM_UTF8):
        return raw.decode("utf-8-sig"), "utf-8-sig"
    if raw.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return raw.decode("utf-16"), "utf-16"
    try:
        return raw.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        pass
    try:
        from charset_normalizer import from_bytes

        best = from_bytes(raw).best()
        if best is not None and best.encoding:
            return str(best), best.encoding
    except Exception:  # noqa: BLE001
        pass
    return raw.decode("cp1252", errors="replace"), "cp1252"


def sniff_delimiter(text: str) -> str:
    sample = text[:20000]
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
    except csv.Error:
        pass
    lines = [ln for ln in sample.splitlines() if ln.strip()][:30]
    best, best_score = ",", 0
    for d in (",", ";", "\t", "|"):
        counts = [ln.count(d) for ln in lines]
        if counts and min(counts) > 0:
            score = min(counts) * len(counts) - (max(counts) - min(counts))
            if score > best_score:
                best, best_score = d, score
    return best


def _load_csv(path: str, settings: Settings) -> list[SheetContent]:
    try:
        raw = Path(path).read_bytes()
    except OSError as exc:
        raise CorruptFileError(f"The CSV file could not be read ({exc}).") from exc
    if not raw.strip():
        raise CorruptFileError("The CSV file is empty.", code="EMPTY_FILE")
    text, encoding = decode_bytes(raw)
    delim = sniff_delimiter(text)
    csv.field_size_limit(20_000_000)
    rows: list[list[Any]] = []
    truncated = False
    try:
        for n, rec in enumerate(csv.reader(io.StringIO(text, newline=""), delimiter=delim), 1):
            if n > settings.max_sheet_rows:
                truncated = True
                break
            rows.append(_trim_row([(c.strip() if c.strip() else None) for c in rec[: settings.max_sheet_cols]]))
    except csv.Error as exc:
        raise CorruptFileError(f"The CSV file could not be parsed ({exc}).") from exc
    warnings = [f"Decoded as {encoding}; delimiter {'tab' if delim == chr(9) else repr(delim)}."]
    if truncated:
        warnings.append(f"Only the first {settings.max_sheet_rows:,} rows were read.")
    return [SheetContent(1, "CSV", _trim_rows(rows), truncated=truncated, warnings=warnings)]


# --------------------------------------------------------------------------- reader


class SheetReader:
    kind = "spreadsheet"

    def __init__(self, path: str, file_type: str, settings: Settings):
        self.path, self.file_type, self.settings = path, file_type, settings
        if file_type == "xlsx":
            all_sheets = _load_xlsx(path, settings)
        elif file_type == "xls":
            all_sheets = _load_xls(path, settings)
        else:
            all_sheets = _load_csv(path, settings)
        self.warnings: list[str] = []
        self.sheets = [s for s in all_sheets if not s.hidden]
        hidden = [s.name for s in all_sheets if s.hidden]
        if hidden:
            self.warnings.append(f"Skipped hidden sheet(s): {', '.join(hidden)}.")
        for s in self.sheets:
            self.warnings.extend(f"{s.name}: {w}" for w in s.warnings)
        self._pages = self._render_pages()
        self.page_count = len(self._pages)
        if not any(s.rows for s in self.sheets):
            raise CorruptFileError("The workbook has no data in its visible sheets.", code="EMPTY_FILE")

    def _render_pages(self) -> list[PageContent]:
        pages: list[PageContent] = []
        for sheet in self.sheets:
            lines = [(n, "\t".join(cell_text(c) for c in row).rstrip()) for n, row in enumerate(sheet.rows, 1)]
            lines = [(n, t) for n, t in lines if t]
            chunks = [lines[i : i + ROWS_PER_PAGE] for i in range(0, len(lines), ROWS_PER_PAGE)] or [[]]
            for ci, chunk in enumerate(chunks):
                label = sheet.name if len(chunks) == 1 else f"{sheet.name} (rows {chunk[0][0]}-{chunk[-1][0]})"
                pages.append(
                    PageContent(
                        page_no=len(pages) + 1,
                        source="sheet",
                        text="\n".join(t for _, t in chunk),
                        label=label,
                        meta={"sheet_index": sheet.index, "sheet": sheet.name, "row_start": chunk[0][0] if chunk else 0, "row_end": chunk[-1][0] if chunk else 0},
                    )
                )
        return pages

    def close(self) -> None:  # pragma: no cover
        pass

    def try_text_page(self, index: int) -> PageContent:
        return self._pages[index]
