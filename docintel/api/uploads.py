"""Receiving uploaded files: stream to disk, check what they really are, drop exact repeats, queue the rest."""

from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path
from typing import Any, BinaryIO

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from ..config import Settings
from ..db.engine import session_scope
from ..db.models import Document, new_id
from ..jobs.queue import add_event, enqueue
from ..pipeline.detect import SUPPORTED_EXTENSIONS, sniff
from .security import safe_display_name

log = logging.getLogger(__name__)
CHUNK = 1024 * 1024
FILE_EXT = {"pdf": ".pdf", "xlsx": ".xlsx", "xls": ".xls", "csv": ".csv"}


class _Reject(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


def stored_extension(file_type: str, path: Path) -> str:
    if file_type in FILE_EXT:
        return FILE_EXT[file_type]
    with open(path, "rb") as fh:
        head = fh.read(8)
    if head.startswith(b"\x89PNG"):
        return ".png"
    if head.startswith(b"\xff\xd8"):
        return ".jpg"
    return ".tif"


def media_type(file_type: str, stored_name: str) -> str:
    if file_type == "pdf":
        return "application/pdf"
    if file_type == "image":
        return {".png": "image/png", ".jpg": "image/jpeg", ".tif": "image/tiff"}.get(Path(stored_name).suffix.lower(), "application/octet-stream")
    if file_type == "xlsx":
        return "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    if file_type == "xls":
        return "application/vnd.ms-excel"
    if file_type == "csv":
        return "text/csv; charset=utf-8"
    return "application/octet-stream"


def ingest_stream(settings: Settings, factory: sessionmaker[Session], stream: BinaryIO, filename: str | None, *, batch_id: str | None, force: bool = False) -> dict[str, Any]:
    """Store one uploaded file and queue it. Returns a per file result; never raises for a bad file.

    status is "queued", "duplicate" (same bytes already stored, nothing queued) or "rejected".
    """
    name = safe_display_name(filename)
    base: dict[str, Any] = {"filename": name}
    ext = Path(name).suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        return {**base, "status": "rejected", "code": "UNSUPPORTED_TYPE", "message": f"File type {ext or '(no extension)'} is not supported. Use PDF, JPG, PNG, TIFF, XLSX, XLS or CSV."}
    settings.ensure_dirs()
    tmp = settings.uploads_dir / f".incoming-{new_id()}"
    final: Path | None = None
    try:
        digest, size = hashlib.sha256(), 0
        with open(tmp, "wb") as out:
            while True:
                chunk = stream.read(CHUNK)
                if not chunk:
                    break
                size += len(chunk)
                if size > settings.max_upload_bytes:
                    raise _Reject("FILE_TOO_LARGE", f"File is larger than the {settings.max_upload_mb} MB limit.")
                digest.update(chunk)
                out.write(chunk)
        if size == 0:
            raise _Reject("EMPTY_FILE", "File is empty.")
        sn = sniff(tmp, name)
        if sn.file_type is None:
            raise _Reject(sn.code, sn.reason)
        sha = digest.hexdigest()
        doc_id = new_id()
        with session_scope(factory) as s:
            if settings.dedupe_files and not force:
                prior = s.scalars(select(Document).where(Document.sha256 == sha, Document.status.in_(("queued", "processing", "completed"))).order_by(Document.created_at).limit(1)).first()
                if prior is not None:
                    return {**base, "status": "duplicate", "document_id": prior.id, "duplicate_of": prior.id,
                            "message": f"An identical file is already stored as '{prior.original_filename}'. Nothing was queued."}
            final = settings.uploads_dir / f"{doc_id}{stored_extension(sn.file_type, tmp)}"
            os.replace(tmp, final)
            s.add(Document(id=doc_id, batch_id=batch_id, original_filename=name, stored_name=final.name, sha256=sha, size_bytes=size, file_type=sn.file_type, status="queued", stage="queued"))
            s.flush()
            add_event(s, doc_id, "upload", f"Received {name} ({size:,} bytes).")
            for w in sn.warnings:
                add_event(s, doc_id, "upload", w, level="warning")
            enqueue(s, doc_id, settings)
        return {**base, "status": "queued", "document_id": doc_id, "file_type": sn.file_type, "size_bytes": size, "warnings": sn.warnings}
    except _Reject as r:
        return {**base, "status": "rejected", "code": r.code, "message": r.message}
    except Exception:  # noqa: BLE001
        log.exception("Storing upload %r failed", name)
        if final is not None:
            final.unlink(missing_ok=True)
        return {**base, "status": "rejected", "code": "UPLOAD_FAILED", "message": "The file could not be stored. Please try again."}
    finally:
        tmp.unlink(missing_ok=True)


def ingest_path(settings: Settings, factory: sessionmaker[Session], path: str | Path, *, batch_id: str | None = None, force: bool = False) -> dict[str, Any]:
    """Same as an upload, for a file already on disk (the command line and the tests)."""
    p = Path(path)
    with open(p, "rb") as fh:
        return ingest_stream(settings, factory, fh, p.name, batch_id=batch_id, force=force)
