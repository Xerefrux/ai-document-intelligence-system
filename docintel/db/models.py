"""Database tables (SQLAlchemy 2.0 declarative mapping).

Overview, see docs/DATABASE.md for the diagram:

  documents 1--* pages               extracted text, one row per page (or sheet)
  documents 1--* records             structured results, one per invoice found in the document
  records   1--* line_items
  documents 1--* validation_issues   rule results (record_id is null for document level problems)
  records   1--* corrections         audit trail of manual edits
  documents 1--* processing_events   processing history
  documents 1--* jobs                background work, claimed by worker processes
  records / pages 1--* embeddings    vectors for semantic search
  reconciliations 1--* reconciliation_items   cross verification results
  worker_state                       heartbeat of each worker process

Timestamps are stored as naive UTC. Amounts are stored as floats rounded to four decimals; all
arithmetic checks are done with Decimal in docintel.validation.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def new_id() -> str:
    return uuid.uuid4().hex


class Base(DeclarativeBase):
    pass


# Allowed values, kept here so the API, the worker and the tests agree.
DOC_STATUSES = ("queued", "processing", "completed", "failed", "cancelled")
JOB_STATUSES = ("queued", "running", "succeeded", "failed", "cancelled")
REVIEW_STATUSES = ("ok", "needs_review", "corrected", "approved")
SEVERITIES = ("error", "warning", "info")


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    batch_id: Mapped[str | None] = mapped_column(String(32), index=True)
    original_filename: Mapped[str] = mapped_column(String(500))
    stored_name: Mapped[str] = mapped_column(String(255))
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    size_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    file_type: Mapped[str] = mapped_column(String(16))  # pdf | image | xlsx | xls | csv
    status: Mapped[str] = mapped_column(String(16), default="queued", index=True)
    stage: Mapped[str | None] = mapped_column(String(40))
    page_count: Mapped[int | None] = mapped_column(Integer)
    pages_done: Mapped[int] = mapped_column(Integer, default=0)
    source_kind: Mapped[str | None] = mapped_column(String(24))  # text_pdf | scanned_pdf | mixed_pdf | image | spreadsheet | csv
    ocr_used: Mapped[bool] = mapped_column(Boolean, default=False)
    ocr_engine: Mapped[str | None] = mapped_column(String(24))
    ocr_confidence: Mapped[float | None] = mapped_column(Float)
    text_chars: Mapped[int] = mapped_column(Integer, default=0)
    record_count: Mapped[int] = mapped_column(Integer, default=0)
    review_status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    error_code: Mapped[str | None] = mapped_column(String(40))
    error_message: Mapped[str | None] = mapped_column(Text)
    duplicate_of: Mapped[str | None] = mapped_column(String(32))
    processing_ms: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime)


class Page(Base):
    __tablename__ = "pages"
    __table_args__ = (UniqueConstraint("document_id", "page_no", name="uq_page_doc_no"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    page_no: Mapped[int] = mapped_column(Integer)
    label: Mapped[str | None] = mapped_column(String(200))  # sheet name for spreadsheets
    source: Mapped[str] = mapped_column(String(16))  # text | ocr | sheet | failed
    text: Mapped[str] = mapped_column(Text, default="")
    char_count: Mapped[int] = mapped_column(Integer, default=0)
    ocr_confidence: Mapped[float | None] = mapped_column(Float)
    width: Mapped[float | None] = mapped_column(Float)
    height: Mapped[float | None] = mapped_column(Float)
    warnings: Mapped[list | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Record(Base):
    """One extracted invoice (or one ledger row from a spreadsheet)."""

    __tablename__ = "records"
    __table_args__ = (
        Index("ix_records_doc_idx", "document_id", "record_index"),
        Index("ix_records_review", "review_status"),
        Index("ix_records_supplier", "supplier_name"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"))
    record_index: Mapped[int] = mapped_column(Integer, default=0)
    kind: Mapped[str] = mapped_column(String(16), default="invoice")  # invoice | ledger_row
    page_start: Mapped[int | None] = mapped_column(Integer)
    page_end: Mapped[int | None] = mapped_column(Integer)
    sheet_name: Mapped[str | None] = mapped_column(String(200))
    row_ref: Mapped[str | None] = mapped_column(String(60))

    invoice_number: Mapped[str | None] = mapped_column(String(128), index=True)
    invoice_date: Mapped[date | None] = mapped_column(Date, index=True)
    due_date: Mapped[date | None] = mapped_column(Date)
    supplier_name: Mapped[str | None] = mapped_column(String(300))
    customer_name: Mapped[str | None] = mapped_column(String(300))
    subtotal: Mapped[float | None] = mapped_column(Float)
    tax: Mapped[float | None] = mapped_column(Float)
    total: Mapped[float | None] = mapped_column(Float, index=True)
    currency: Mapped[str | None] = mapped_column(String(8))

    method: Mapped[str] = mapped_column(String(12), default="rules")  # rules | llm | hybrid | manual
    confidence: Mapped[float | None] = mapped_column(Float)
    review_status: Mapped[str] = mapped_column(String(16), default="needs_review")
    fields_json: Mapped[dict | None] = mapped_column(JSON)  # per field value, confidence, evidence, page, source
    search_text: Mapped[str] = mapped_column(Text, default="")
    inv_key: Mapped[str | None] = mapped_column(String(160), index=True)  # normalized invoice number
    dedupe_key: Mapped[str | None] = mapped_column(String(480), index=True)  # normalized supplier + invoice number
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class LineItem(Base):
    __tablename__ = "line_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    record_id: Mapped[str] = mapped_column(ForeignKey("records.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer, default=0)
    description: Mapped[str | None] = mapped_column(Text)
    quantity: Mapped[float | None] = mapped_column(Float)
    unit_price: Mapped[float | None] = mapped_column(Float)
    amount: Mapped[float | None] = mapped_column(Float)
    confidence: Mapped[float | None] = mapped_column(Float)


class ValidationIssue(Base):
    __tablename__ = "validation_issues"
    __table_args__ = (Index("ix_issues_doc", "document_id"), Index("ix_issues_code", "code"))

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"))
    record_id: Mapped[str | None] = mapped_column(ForeignKey("records.id", ondelete="CASCADE"), index=True)
    code: Mapped[str] = mapped_column(String(40))
    severity: Mapped[str] = mapped_column(String(8))  # error | warning | info
    field: Mapped[str | None] = mapped_column(String(40))
    message: Mapped[str] = mapped_column(Text)
    details: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Correction(Base):
    __tablename__ = "corrections"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    record_id: Mapped[str] = mapped_column(ForeignKey("records.id", ondelete="CASCADE"), index=True)
    field: Mapped[str] = mapped_column(String(40))
    old_value: Mapped[str | None] = mapped_column(Text)
    new_value: Mapped[str | None] = mapped_column(Text)
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ProcessingEvent(Base):
    __tablename__ = "processing_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    job_id: Mapped[str | None] = mapped_column(String(32))
    stage: Mapped[str] = mapped_column(String(40))
    level: Mapped[str] = mapped_column(String(8), default="info")  # info | warning | error
    message: Mapped[str] = mapped_column(Text)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    data: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (Index("ix_jobs_claim", "status", "run_after", "priority"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(16), default="process")  # process | reprocess
    status: Mapped[str] = mapped_column(String(12), default="queued")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    priority: Mapped[int] = mapped_column(Integer, default=0)  # lower runs first
    resume: Mapped[bool] = mapped_column(Boolean, default=True)
    run_after: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    locked_by: Mapped[str | None] = mapped_column(String(64))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime)
    progress_done: Mapped[int] = mapped_column(Integer, default=0)
    progress_total: Mapped[int] = mapped_column(Integer, default=0)
    stage: Mapped[str | None] = mapped_column(String(40))
    error_code: Mapped[str | None] = mapped_column(String(40))
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)


class Embedding(Base):
    __tablename__ = "embeddings"
    __table_args__ = (
        UniqueConstraint("owner_type", "owner_id", "model", name="uq_embedding_owner_model"),
        Index("ix_embeddings_owner", "owner_type", "owner_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_type: Mapped[str] = mapped_column(String(12))  # record | page
    owner_id: Mapped[str] = mapped_column(String(40))
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    model: Mapped[str] = mapped_column(String(100))
    dim: Mapped[int] = mapped_column(Integer)
    vector: Mapped[bytes] = mapped_column(LargeBinary)
    text_hash: Mapped[str] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Reconciliation(Base):
    __tablename__ = "reconciliations"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    mode: Mapped[str] = mapped_column(String(12), default="manual")  # manual | auto
    left: Mapped[dict | None] = mapped_column(JSON)
    right: Mapped[dict | None] = mapped_column(JSON)
    summary: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ReconciliationItem(Base):
    __tablename__ = "reconciliation_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    reconciliation_id: Mapped[str] = mapped_column(ForeignKey("reconciliations.id", ondelete="CASCADE"), index=True)
    key: Mapped[str | None] = mapped_column(String(160))
    status: Mapped[str] = mapped_column(String(16))  # MATCH | MISMATCH | MISSING_RIGHT | MISSING_LEFT
    left_record_id: Mapped[str | None] = mapped_column(String(32))
    right_record_id: Mapped[str | None] = mapped_column(String(32))
    diffs: Mapped[list | None] = mapped_column(JSON)


class WorkerState(Base):
    __tablename__ = "worker_state"

    worker_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    pid: Mapped[int] = mapped_column(Integer)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    heartbeat_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    current_job: Mapped[str | None] = mapped_column(String(32))
    jobs_done: Mapped[int] = mapped_column(Integer, default=0)
    jobs_failed: Mapped[int] = mapped_column(Integer, default=0)
