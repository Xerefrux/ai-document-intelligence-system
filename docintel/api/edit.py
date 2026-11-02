"""Manual corrections: check what a person typed, store it with an audit trail, and re-run validation."""

from __future__ import annotations

import json
import math
import re
from datetime import date
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..config import Settings
from ..db.models import Correction, Document, LineItem, Record, utcnow
from ..extraction.normalize import parse_amount
from ..extraction.schema import ALL_FIELDS, DATE_FIELDS, MONEY_FIELDS, FieldValue
from ..jobs.records import refresh_document_review, refresh_search_text, set_record_status, sync_columns, validate_stored_record
from ..search.index import index_record

TEXT_LIMITS = {"invoice_number": 128, "supplier_name": 300, "customer_name": 300, "currency": 8}


class EditError(ValueError):
    def __init__(self, field: str, message: str):
        super().__init__(message)
        self.field, self.message = field, message


def coerce_field(name: str, value: Any) -> Any:
    """Turn what the client sent into the stored type, or raise EditError."""
    if name not in ALL_FIELDS:
        raise EditError(name, f"Unknown field '{name}'.")
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if name in DATE_FIELDS:
        if isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value.strip()):
            try:
                return date.fromisoformat(value.strip()).isoformat()
            except ValueError:
                pass
        raise EditError(name, f"{name.replace('_', ' ').capitalize()} must be a real date written as YYYY-MM-DD.")
    if name in MONEY_FIELDS:
        if isinstance(value, bool):
            raise EditError(name, f"{name.capitalize()} must be a number.")
        if isinstance(value, (int, float)):
            num = float(value)
        else:
            m = parse_amount(str(value))
            if m is None:
                raise EditError(name, f"{name.capitalize()} must be a number.")
            num = m.as_float()
        if not math.isfinite(num) or abs(num) >= 1e12:
            raise EditError(name, f"{name.capitalize()} is out of range.")
        return round(num, 4)
    text = " ".join(str(value).split())
    if name == "currency":
        text = text.upper()
        if not re.fullmatch(r"[A-Z]{3}", text):
            raise EditError(name, "Currency must be a three letter code such as USD or BDT.")
    if len(text) > TEXT_LIMITS[name]:
        raise EditError(name, f"{name.replace('_', ' ').capitalize()} is longer than {TEXT_LIMITS[name]} characters.")
    return text


def coerce_item(raw: dict[str, Any], pos: int) -> dict[str, Any]:
    out: dict[str, Any] = {}
    desc = raw.get("description")
    out["description"] = " ".join(str(desc).split())[:500] if desc not in (None, "") else None
    for k in ("quantity", "unit_price", "amount"):
        v = raw.get(k)
        if v in (None, ""):
            out[k] = None
            continue
        try:
            num = float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else (parse_amount(str(v)).as_float() if parse_amount(str(v)) else None)
        except (TypeError, ValueError):
            num = None
        if num is None or not math.isfinite(num) or abs(num) >= 1e12:
            raise EditError(f"line_items[{pos}].{k}", f"Line {pos}: {k.replace('_', ' ')} must be a number.")
        out[k] = round(num, 4)
    return out


def _show(v: Any) -> str | None:
    return None if v is None else (v if isinstance(v, str) else json.dumps(v))


def apply_edit(session: Session, rec: Record, settings: Settings, *, fields: dict[str, Any] | None, line_items: list[dict[str, Any]] | None, note: str | None) -> list[str]:
    """Apply a correction. Returns the names of the fields that actually changed."""
    stored = {k: FieldValue.from_json(v) for k, v in (rec.fields_json or {}).items()}
    changed: list[str] = []
    for name, raw in (fields or {}).items():
        new = coerce_field(name, raw)
        fv = stored.get(name) or FieldValue()
        old = fv.value
        if (old is None and new is None) or (old is not None and new is not None and str(old) == str(new)):
            continue
        session.add(Correction(record_id=rec.id, field=name, old_value=_show(old), new_value=_show(new), note=note))
        evidence = fv.evidence if new is not None and fv.evidence else None
        stored[name] = FieldValue(new, 1.0 if new is not None else 0.0, evidence, fv.page, "manual", f"Set by a person{': ' + note if note else '.'}")
        changed.append(name)
    if line_items is not None:
        items = [coerce_item(it, i + 1) for i, it in enumerate(line_items[:2000])]
        old_items = session.scalars(select(LineItem).where(LineItem.record_id == rec.id).order_by(LineItem.position)).all()
        old_view = [[i.description, i.quantity, i.unit_price, i.amount] for i in old_items]
        new_view = [[i["description"], i["quantity"], i["unit_price"], i["amount"]] for i in items]
        if old_view != new_view:
            session.execute(delete(LineItem).where(LineItem.record_id == rec.id))
            for pos, it in enumerate(items, 1):
                session.add(LineItem(record_id=rec.id, position=pos, confidence=1.0, **it))
            session.add(Correction(record_id=rec.id, field="line_items", old_value=f"{len(old_view)} line(s)", new_value=f"{len(new_view)} line(s)", note=note))
            changed.append("line_items")
    if not changed:
        return []
    sync_columns(rec, stored)
    session.flush()
    live_items = session.scalars(select(LineItem).where(LineItem.record_id == rec.id).order_by(LineItem.position)).all()
    refresh_search_text(rec, list(live_items))
    rec.version += 1
    rec.updated_at = utcnow()
    if rec.review_status == "approved":
        rec.review_status = "needs_review"  # an approval does not survive an edit
    validate_stored_record(session, rec, settings)
    set_record_status(session, rec, human_touched=True)
    doc = session.get(Document, rec.document_id)
    if doc is not None:
        refresh_document_review(session, doc)
    index_record(session, rec, settings)
    return changed
