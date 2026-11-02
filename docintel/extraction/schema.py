"""The structured result of extraction: field values with confidence and evidence, plus line items."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

CORE_FIELDS = ("invoice_number", "invoice_date", "supplier_name", "customer_name", "subtotal", "tax", "total", "currency")
OPTIONAL_FIELDS = ("due_date", "discount", "shipping")
ALL_FIELDS = CORE_FIELDS + OPTIONAL_FIELDS
DATE_FIELDS = ("invoice_date", "due_date")
MONEY_FIELDS = ("subtotal", "tax", "total", "discount", "shipping")
TEXT_FIELDS = ("invoice_number", "supplier_name", "customer_name", "currency")
REQUIRED_FIELDS = ("invoice_number", "invoice_date", "supplier_name", "total")

# Weights used for the overall confidence of a record. Missing fields count as zero.
FIELD_WEIGHTS = {
    "invoice_number": 1.0,
    "invoice_date": 1.0,
    "supplier_name": 1.0,
    "total": 1.5,
    "customer_name": 0.5,
    "subtotal": 0.5,
    "tax": 0.5,
    "currency": 0.5,
}
LINE_ITEMS_WEIGHT = 1.0


@dataclass
class FieldValue:
    value: Any = None
    confidence: float = 0.0
    evidence: str | None = None
    page: int | None = None
    source: str = "rules"  # rules | llm | both | manual
    note: str | None = None

    def to_json(self) -> dict:
        return {
            "value": self.value,
            "confidence": round(float(self.confidence), 3),
            "evidence": self.evidence,
            "page": self.page,
            "source": self.source,
            "note": self.note,
        }

    @classmethod
    def from_json(cls, d: dict | None) -> "FieldValue":
        d = d or {}
        return cls(
            value=d.get("value"),
            confidence=float(d.get("confidence") or 0.0),
            evidence=d.get("evidence"),
            page=d.get("page"),
            source=d.get("source") or "rules",
            note=d.get("note"),
        )


@dataclass
class LineItemValue:
    description: str | None = None
    quantity: float | None = None
    unit_price: float | None = None
    amount: float | None = None
    confidence: float = 0.0
    page: int | None = None
    evidence: str | None = None

    def to_json(self) -> dict:
        return {
            "description": self.description,
            "quantity": self.quantity,
            "unit_price": self.unit_price,
            "amount": self.amount,
            "confidence": round(float(self.confidence), 3),
            "page": self.page,
            "evidence": self.evidence,
        }

    @classmethod
    def from_json(cls, d: dict) -> "LineItemValue":
        return cls(
            description=d.get("description"),
            quantity=d.get("quantity"),
            unit_price=d.get("unit_price"),
            amount=d.get("amount"),
            confidence=float(d.get("confidence") or 0.0),
            page=d.get("page"),
            evidence=d.get("evidence"),
        )


@dataclass
class ExtractedRecord:
    fields: dict[str, FieldValue] = field(default_factory=dict)
    line_items: list[LineItemValue] = field(default_factory=list)
    kind: str = "invoice"  # invoice | ledger_row
    method: str = "rules"  # rules | llm | hybrid | manual
    page_start: int | None = None
    page_end: int | None = None
    sheet_name: str | None = None
    row_ref: str | None = None
    notes: list[str] = field(default_factory=list)
    ocr_confidence: float | None = None
    text: str = ""  # the source text of this record; used for LLM prompts and grounding, not stored
    dropped: list[dict] = field(default_factory=list)  # LLM values that were not found in the document
    disagreements: list[dict] = field(default_factory=list)  # fields where rules and LLM gave different values
    llm_error: str | None = None

    def value(self, name: str) -> Any:
        fv = self.fields.get(name)
        return fv.value if fv else None

    def conf(self, name: str) -> float:
        fv = self.fields.get(name)
        return fv.confidence if fv and fv.value is not None else 0.0

    def overall_confidence(self) -> float:
        total_w = sum(FIELD_WEIGHTS.values()) + LINE_ITEMS_WEIGHT
        got = sum(FIELD_WEIGHTS[f] * self.conf(f) for f in FIELD_WEIGHTS)
        if self.line_items:
            got += LINE_ITEMS_WEIGHT * (sum(li.confidence for li in self.line_items) / len(self.line_items))
        return round(got / total_w, 3)

    def to_json(self) -> dict:
        return {
            "kind": self.kind,
            "method": self.method,
            "page_start": self.page_start,
            "page_end": self.page_end,
            "sheet_name": self.sheet_name,
            "row_ref": self.row_ref,
            "fields": {k: v.to_json() for k, v in self.fields.items()},
            "line_items": [li.to_json() for li in self.line_items],
            "notes": self.notes,
            "dropped": self.dropped,
            "disagreements": self.disagreements,
            "llm_error": self.llm_error,
        }
