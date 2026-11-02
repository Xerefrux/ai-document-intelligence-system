"""Search over records and extracted page text: keyword, semantic or both, with structured filters."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Query, Request

from ...db.engine import session_scope
from ...search.query import Filters, search
from ..deps import factory_of, filters_dep, settings_of

router = APIRouter(tags=["search"])


@router.get("/search", summary="Search documents")
def search_documents(
    request: Request,
    q: str = Query("", max_length=300, description="What to look for. Empty means filters only."),
    mode: Literal["hybrid", "keyword", "semantic"] = Query("hybrid", description="keyword: exact words (bm25). semantic: similarity of meaning (see the note on the embedding model). hybrid: both, merged by rank."),
    pages: bool = Query(True, description="Also search the full extracted text of every page"),
    flt: Filters = Depends(filters_dep),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0, le=5000),
):
    with session_scope(factory_of(request)) as s:
        out = search(s, settings_of(request), q, mode=mode, filters=flt, limit=limit, offset=offset, pages=pages)
    out["limit"], out["offset"] = limit, offset
    return out
