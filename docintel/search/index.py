"""Building the searchable text and the vectors for records and pages."""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..config import Settings
from ..db.models import Embedding, LineItem, Page, Record
from .embeddings import Embedder, EmbeddingError, get_embedder, text_hash, to_blob

log = logging.getLogger(__name__)

MAX_PAGES_EMBEDDED = 50
PAGE_TEXT_CHARS = 3000


def record_search_text(fields: dict[str, Any], items: list[dict[str, Any]]) -> str:
    """One line of plain text per record: what a person would type to find it."""
    parts: list[str] = []
    for key in ("invoice_number", "supplier_name", "customer_name", "invoice_date", "due_date", "currency"):
        v = fields.get(key)
        if v:
            parts.append(str(v))
    total = fields.get("total")
    if total is not None:
        parts.append(f"{float(total):.2f}")
    for it in items[:60]:
        if it.get("description"):
            parts.append(str(it["description"]))
    return " | ".join(parts)


def embedding_text(rec: Record, descriptions: list[str]) -> str:
    """What a record is about, for the vector: who, what number, what was bought. Dates and amounts add only noise to a vector."""
    parts = [rec.supplier_name, rec.customer_name, rec.invoice_number] + descriptions[:20]
    return " | ".join(str(p) for p in parts if p)


def _descriptions(session: Session, record_ids: list[str]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {i: [] for i in record_ids}
    if not record_ids:
        return out
    rows = session.execute(select(LineItem.record_id, LineItem.description).where(LineItem.record_id.in_(record_ids), LineItem.description.is_not(None)).order_by(LineItem.record_id, LineItem.position)).all()
    for rid, desc in rows:
        out[rid].append(desc)
    return out


def _replace_vectors(session: Session, embedder: Embedder, owners: list[tuple[str, str, str, str]], document_id: str) -> int:
    """owners: (owner_type, owner_id, text). Replaces any vector the owner has for this model."""
    if not owners:
        return 0
    texts = [o[2] for o in owners]
    matrix = embedder.embed(texts)
    for (otype, oid, text, _), vec in zip(owners, matrix):
        session.execute(delete(Embedding).where(Embedding.owner_type == otype, Embedding.owner_id == oid, Embedding.model == embedder.name))
        session.add(Embedding(owner_type=otype, owner_id=oid, document_id=document_id, model=embedder.name, dim=embedder.dim, vector=to_blob(vec), text_hash=text_hash(text)))
    return len(owners)


def index_document(session: Session, document_id: str, settings: Settings, embedder: Embedder | None = None) -> int:
    """(Re)build vectors for a document's records and its first pages. Never raises: search is optional, processing is not."""
    embedder = embedder or get_embedder(settings)
    if embedder is None:
        return 0
    try:
        owners: list[tuple[str, str, str, str]] = []
        recs = session.scalars(select(Record).where(Record.document_id == document_id)).all()
        desc = _descriptions(session, [r.id for r in recs])
        for rec in recs:
            t = embedding_text(rec, desc[rec.id])
            if t:
                owners.append(("record", rec.id, t, document_id))
        for pg in session.scalars(select(Page).where(Page.document_id == document_id, Page.char_count > 20).order_by(Page.page_no).limit(MAX_PAGES_EMBEDDED)):
            owners.append(("page", str(pg.id), (pg.text or "")[:PAGE_TEXT_CHARS], document_id))
        session.execute(delete(Embedding).where(Embedding.document_id == document_id, Embedding.model == embedder.name))
        n = _replace_vectors(session, embedder, owners, document_id)
        return n
    except EmbeddingError as exc:
        log.warning("Embedding failed for document %s, semantic search will miss it until it is reindexed: %s", document_id, exc)
        return 0
    except Exception:  # noqa: BLE001
        log.exception("Indexing document %s failed", document_id)
        return 0


def index_record(session: Session, record: Record, settings: Settings, embedder: Embedder | None = None) -> None:
    """Refresh one record's vector after its text changed (a manual correction)."""
    embedder = embedder or get_embedder(settings)
    if embedder is None:
        return
    text = embedding_text(record, _descriptions(session, [record.id])[record.id])
    if not text:
        return
    try:
        _replace_vectors(session, embedder, [("record", record.id, text, record.document_id)], record.document_id)
    except Exception:  # noqa: BLE001
        log.warning("Could not refresh the vector of record %s", record.id)


def line_items_payload(session: Session, record_id: str) -> list[dict[str, Any]]:
    rows = session.scalars(select(LineItem).where(LineItem.record_id == record_id).order_by(LineItem.position)).all()
    return [{"description": r.description, "quantity": r.quantity, "unit_price": r.unit_price, "amount": r.amount} for r in rows]
