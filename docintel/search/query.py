"""Search: keyword (SQLite FTS5 with bm25), semantic (vector similarity), or both fused by rank.

Structured filters (supplier, dates, amounts, status, ...) apply to every mode. Hybrid search uses
reciprocal rank fusion: each list contributes 1 / (60 + rank), so a result that is high in both
lists beats one that is first in only one of them, and no score scaling is needed.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import numpy as np
from sqlalchemy import text
from sqlalchemy.orm import Session

from ..config import Settings
from .embeddings import EmbeddingError, cosine_topk, from_blob, get_embedder

HL_OPEN, HL_CLOSE = "\x02", "\x03"  # highlight markers in snippets; the UI turns them into <mark> without using innerHTML
RRF_K = 60
REL_COSINE = 0.45  # a semantic hit must also be at least this fraction of the best one


@dataclass
class Filters:
    supplier: str | None = None
    customer: str | None = None
    invoice_number: str | None = None
    date_from: date | None = None
    date_to: date | None = None
    min_total: float | None = None
    max_total: float | None = None
    currency: str | None = None
    review_status: str | None = None
    document_id: str | None = None
    has_issues: bool | None = None
    issue_code: str | None = None
    file_type: str | None = None
    params: dict[str, Any] = field(default_factory=dict)

    def sql(self) -> tuple[str, dict[str, Any]]:
        """WHERE fragments over alias r (records) and d (documents), with bound parameters."""
        c: list[str] = []
        p: dict[str, Any] = {}

        def like(col: str, key: str, val: str) -> None:
            c.append(f"lower({col}) LIKE :{key} ESCAPE '\\'")
            p[key] = "%" + val.lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"

        if self.supplier:
            like("r.supplier_name", "f_sup", self.supplier)
        if self.customer:
            like("r.customer_name", "f_cus", self.customer)
        if self.invoice_number:
            like("r.invoice_number", "f_inv", self.invoice_number)
        if self.date_from:
            c.append("r.invoice_date >= :f_d1")
            p["f_d1"] = self.date_from
        if self.date_to:
            c.append("r.invoice_date <= :f_d2")
            p["f_d2"] = self.date_to
        if self.min_total is not None:
            c.append("r.total >= :f_t1")
            p["f_t1"] = self.min_total
        if self.max_total is not None:
            c.append("r.total <= :f_t2")
            p["f_t2"] = self.max_total
        if self.currency:
            c.append("r.currency = :f_cur")
            p["f_cur"] = self.currency.upper()
        if self.review_status:
            c.append("r.review_status = :f_rs")
            p["f_rs"] = self.review_status
        if self.document_id:
            c.append("r.document_id = :f_doc")
            p["f_doc"] = self.document_id
        if self.file_type:
            c.append("d.file_type = :f_ft")
            p["f_ft"] = self.file_type
        if self.has_issues is True:
            c.append("EXISTS (SELECT 1 FROM validation_issues v WHERE v.record_id = r.id AND v.severity IN ('error','warning'))")
        if self.has_issues is False:
            c.append("NOT EXISTS (SELECT 1 FROM validation_issues v WHERE v.record_id = r.id AND v.severity IN ('error','warning'))")
        if self.issue_code:
            c.append("EXISTS (SELECT 1 FROM validation_issues v WHERE v.record_id = r.id AND v.code = :f_code)")
            p["f_code"] = self.issue_code
        return (" AND " + " AND ".join(c)) if c else "", p


def _tokens(q: str) -> list[str]:
    return re.findall(r"[^\W_]+", q, re.UNICODE)[:12]


def fts_query(q: str, mode: str = "and") -> str | None:
    """Turn what a person typed into a safe FTS5 query. Every token is quoted, so operators and punctuation are plain text.

    phrase: the words next to each other, in order (an invoice number such as INV-2025-07745 is found this way)
    and:    all words, anywhere in the text
    or:     any word
    """
    tokens = _tokens(q)
    if not tokens:
        return None
    parts = [f'"{t}"' for t in tokens]
    parts[-1] += "*"  # the last word may still be half typed
    if mode == "phrase":
        return '"' + " ".join(tokens) + '"*' if len(tokens) > 1 else parts[0]
    return (" OR " if mode == "or" else " ").join(parts)


def _ladder(q: str) -> list[str]:
    """Which query shapes to try, in order. The first one that finds something wins.

    The "any word" step is only for ordinary sentences. A query with digits is an identifier, a date or an amount, and
    matching on one of its digit groups would return unrelated documents.
    """
    toks = _tokens(q)
    if len(toks) < 2:
        return ["and"]
    steps = ["phrase", "and"]
    if not any(any(ch.isdigit() for ch in t) for t in toks):
        steps.append("or")
    return steps


def _record_rows(session: Session, ids: list[str]) -> dict[str, dict[str, Any]]:
    if not ids:
        return {}
    marks = ", ".join(f":i{n}" for n in range(len(ids)))
    sql = f"""SELECT r.id, r.document_id, d.original_filename, r.record_index, r.kind, r.invoice_number, r.invoice_date, r.supplier_name, r.customer_name,
                     r.total, r.currency, r.review_status, r.confidence, r.page_start,
                     (SELECT count(*) FROM validation_issues v WHERE v.record_id = r.id AND v.severity IN ('error','warning')) AS issue_count
              FROM records r JOIN documents d ON d.id = r.document_id WHERE r.id IN ({marks})"""
    rows = session.execute(text(sql), {f"i{n}": v for n, v in enumerate(ids)}).mappings().all()
    return {
        r["id"]: {
            "type": "record", "id": r["id"], "document_id": r["document_id"], "filename": r["original_filename"], "kind": r["kind"], "invoice_number": r["invoice_number"],
            "invoice_date": r["invoice_date"].isoformat() if hasattr(r["invoice_date"], "isoformat") else r["invoice_date"], "supplier_name": r["supplier_name"],
            "customer_name": r["customer_name"], "total": r["total"], "currency": r["currency"], "review_status": r["review_status"], "confidence": r["confidence"],
            "page": r["page_start"], "issue_count": r["issue_count"],
        }
        for r in rows
    }


def _keyword_records(session: Session, q: str, flt: Filters, limit: int) -> list[tuple[str, str]]:
    """(record id, snippet) best match first."""
    where, params = flt.sql()
    for mode in _ladder(q):
        match = fts_query(q, mode)
        if not match:
            return []
        sql = f"""SELECT r.id AS id, snippet(record_fts, 0, '{HL_OPEN}', '{HL_CLOSE}', '…', 16) AS snip
                  FROM record_fts JOIN records r ON r.rowid = record_fts.rowid JOIN documents d ON d.id = r.document_id
                  WHERE record_fts MATCH :m {where} ORDER BY bm25(record_fts) LIMIT :lim"""
        rows = session.execute(text(sql), {**params, "m": match, "lim": limit}).all()
        if rows:
            return [(r[0], r[1]) for r in rows]
    return []


def _keyword_pages(session: Session, q: str, limit: int, document_id: str | None) -> list[dict[str, Any]]:
    for mode in _ladder(q):
        match = fts_query(q, mode)
        if not match:
            return []
        extra = " AND p.document_id = :doc" if document_id else ""
        sql = f"""SELECT p.id AS id, p.document_id AS document_id, d.original_filename AS filename, p.page_no AS page_no, p.label AS label,
                         snippet(page_fts, 0, '{HL_OPEN}', '{HL_CLOSE}', '…', 24) AS snip
                  FROM page_fts JOIN pages p ON p.id = page_fts.rowid JOIN documents d ON d.id = p.document_id
                  WHERE page_fts MATCH :m {extra} ORDER BY bm25(page_fts) LIMIT :lim"""
        params: dict[str, Any] = {"m": match, "lim": limit}
        if document_id:
            params["doc"] = document_id
        rows = session.execute(text(sql), params).mappings().all()
        if rows:
            return [{"type": "page", "id": str(r["id"]), "document_id": r["document_id"], "filename": r["filename"], "page_no": r["page_no"], "label": r["label"], "snippet": r["snip"]} for r in rows]
    return []


def _semantic_records(session: Session, settings: Settings, q: str, flt: Filters, limit: int) -> list[tuple[str, float]]:
    emb = get_embedder(settings)
    if emb is None:
        return []
    where, params = flt.sql()
    rows = session.execute(
        text(f"""SELECT e.owner_id AS id, e.vector AS vec FROM embeddings e JOIN records r ON r.id = e.owner_id JOIN documents d ON d.id = r.document_id
                 WHERE e.owner_type = 'record' AND e.model = :model {where}"""),
        {**params, "model": emb.name},
    ).all()
    if not rows:
        return []
    matrix = np.vstack([from_blob(r[1]) for r in rows])
    qv = emb.embed([q])[0]
    return _cut([(rows[i][0], s) for i, s in cosine_topk(matrix, qv, limit)], emb.min_sim)


def _cut(hits: list[tuple], min_sim: float) -> list[tuple]:
    """Keep hits that are similar enough in absolute terms and close to the best one."""
    hits = [h for h in hits if h[1] >= min_sim]
    if not hits:
        return []
    best = max(s for _, s in hits)
    return [(k, s) for k, s in hits if s >= REL_COSINE * best]


def _semantic_pages(session: Session, settings: Settings, q: str, limit: int, document_id: str | None) -> list[tuple[int, float]]:
    emb = get_embedder(settings)
    if emb is None:
        return []
    extra = " AND e.document_id = :doc" if document_id else ""
    params: dict[str, Any] = {"model": emb.name}
    if document_id:
        params["doc"] = document_id
    rows = session.execute(text(f"SELECT e.owner_id, e.vector FROM embeddings e WHERE e.owner_type = 'page' AND e.model = :model {extra}"), params).all()
    if not rows:
        return []
    matrix = np.vstack([from_blob(r[1]) for r in rows])
    qv = emb.embed([q])[0]
    return _cut([(int(rows[i][0]), s) for i, s in cosine_topk(matrix, qv, limit)], emb.min_sim)


def _rrf(*ranked: list[str]) -> dict[str, float]:
    scores: dict[str, float] = {}
    for lst in ranked:
        for pos, key in enumerate(lst, 1):
            scores[key] = scores.get(key, 0.0) + 1.0 / (RRF_K + pos)
    return scores


def search(session: Session, settings: Settings, q: str, *, mode: str = "hybrid", filters: Filters | None = None, limit: int = 20, offset: int = 0, pages: bool = True) -> dict[str, Any]:
    t0 = time.perf_counter()
    flt = filters or Filters()
    q = (q or "").strip()
    pool = min(max(limit + offset, 20) * 3, 300)
    out: dict[str, Any] = {"query": q, "mode": mode, "records": [], "pages": [], "total_records": 0, "semantic_available": get_embedder(settings) is not None,
                 "semantic_kind": {"hash": "fuzzy", "none": None}.get(settings.embedding_provider, "semantic")}
    note: str | None = None

    if not q:  # browse by filters only
        where, params = flt.sql()
        ids = [r[0] for r in session.execute(text(f"SELECT r.id FROM records r JOIN documents d ON d.id = r.document_id WHERE 1=1 {where} ORDER BY r.created_at DESC, r.record_index LIMIT :lim OFFSET :off"), {**params, "lim": limit, "off": offset}).all()]
        total = session.execute(text(f"SELECT count(*) FROM records r JOIN documents d ON d.id = r.document_id WHERE 1=1 {where}"), params).scalar() or 0
        rows = _record_rows(session, ids)
        out["records"] = [rows[i] for i in ids if i in rows]
        out["total_records"] = int(total)
        out["took_ms"] = round((time.perf_counter() - t0) * 1000, 1)
        return out

    kw: list[tuple[str, str]] = []
    sem: list[tuple[str, float]] = []
    if mode in ("keyword", "hybrid"):
        kw = _keyword_records(session, q, flt, pool)
    if mode in ("semantic", "hybrid"):
        try:
            sem = _semantic_records(session, settings, q, flt, pool)
        except EmbeddingError as exc:
            note = f"Semantic search is unavailable right now ({exc}); showing keyword results."
        if not out["semantic_available"] and mode == "semantic":
            note = "Semantic search is switched off (DOCINTEL_EMBEDDING_PROVIDER=none)."
    snippets = dict(kw)
    kw_ids = [k for k, _ in kw]
    sem_ids = [k for k, _ in sem]
    sem_score = dict(sem)
    if mode == "keyword":
        fused = {k: 1.0 / (RRF_K + i) for i, k in enumerate(kw_ids, 1)}
    elif mode == "semantic":
        fused = {k: s for k, s in sem}
    else:
        fused = _rrf(kw_ids, sem_ids)
    ranked = sorted(fused, key=lambda k: -fused[k])
    out["total_records"] = len(ranked)
    page_ids = ranked[offset : offset + limit]
    rows = _record_rows(session, page_ids)
    for k in page_ids:
        if k not in rows:
            continue
        item = rows[k]
        item["score"] = round(fused[k], 5)
        item["matched_by"] = [m for m, present in (("keyword", k in snippets), ("semantic", k in sem_score)) if present]
        item["snippet"] = snippets.get(k)
        if k in sem_score:
            item["similarity"] = round(sem_score[k], 3)
        out["records"].append(item)

    if pages:
        kwp = _keyword_pages(session, q, limit, flt.document_id) if mode in ("keyword", "hybrid") else []
        semp: list[tuple[int, float]] = []
        if mode in ("semantic", "hybrid"):
            try:
                semp = _semantic_pages(session, settings, q, limit, flt.document_id)
            except EmbeddingError:
                semp = []
        by_id = {p["id"]: p for p in kwp}
        order = _rrf([p["id"] for p in kwp], [str(i) for i, _ in semp]) if mode == "hybrid" else (
            {p["id"]: 1.0 / (RRF_K + n) for n, p in enumerate(kwp, 1)} if mode == "keyword" else {str(i): s for i, s in semp})
        missing = [pid for pid in order if pid not in by_id]
        if missing:
            marks = ", ".join(f":p{n}" for n in range(len(missing)))
            rows2 = session.execute(
                text(f"SELECT p.id AS id, p.document_id AS document_id, d.original_filename AS filename, p.page_no AS page_no, p.label AS label, substr(p.text, 1, 200) AS snip FROM pages p JOIN documents d ON d.id = p.document_id WHERE p.id IN ({marks})"),
                {f"p{n}": int(v) for n, v in enumerate(missing)},
            ).mappings().all()
            for r in rows2:
                by_id[str(r["id"])] = {"type": "page", "id": str(r["id"]), "document_id": r["document_id"], "filename": r["filename"], "page_no": r["page_no"], "label": r["label"], "snippet": r["snip"]}
        out["pages"] = [{**by_id[k], "score": round(order[k], 5)} for k in sorted(order, key=lambda k: -order[k]) if k in by_id][:limit]
    if note:
        out["note"] = note
    out["took_ms"] = round((time.perf_counter() - t0) * 1000, 1)
    return out
