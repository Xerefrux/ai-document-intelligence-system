"""The AI extraction client, tested against a pretend server. No real API is called, and none was available while this was built."""

from __future__ import annotations

import json

import httpx
import pytest

from docintel.extraction.document import PageText, extract_document
from docintel.extraction.llm import LLMClient, LLMError, LLMExtractor, LLMUnavailable, parse_llm_answer, redact_pii
from tests.conftest import make_settings

DOC = """ACME Supplies Ltd
Contact: billing@acme.example  Phone: +880 1712-345678
INVOICE
Invoice No: INV-77
Date: 2026-03-27
Bill To: Karim Traders
Description     Qty   Price    Amount
Gloves box        2   10.00     20.00
Sub Total                       20.00
Total                           20.00
"""

GOOD = {
    "invoice_number": {"value": "INV-77", "evidence": "Invoice No: INV-77"},
    "invoice_date": {"value": "2026-03-27", "evidence": "Date: 2026-03-27", "day_month_ambiguous": False},
    "due_date": {"value": None, "evidence": None, "day_month_ambiguous": False},
    "supplier_name": {"value": "ACME Supplies Ltd", "evidence": "ACME Supplies Ltd"},
    "customer_name": {"value": "Karim Traders", "evidence": "Bill To: Karim Traders"},
    "subtotal": {"value": 20.0, "evidence": "Sub Total                       20.00"},
    "tax": {"value": None, "evidence": None},
    "discount": {"value": None, "evidence": None},
    "shipping": {"value": None, "evidence": None},
    "total": {"value": 20.0, "evidence": "Total                           20.00"},
    "currency": {"value": None, "evidence": None},
    "line_items": [{"description": "Gloves box", "quantity": 2, "unit_price": 10.0, "amount": 20.0, "evidence": "Gloves box        2   10.00     20.00"}],
}


def gem(obj, finish="STOP"):
    return {"candidates": [{"content": {"parts": [{"text": json.dumps(obj)}]}, "finishReason": finish}]}


def settings(tmp_path, **kw):
    return make_settings(tmp_path, llm_provider="gemini", gemini_api_key="test-key", extraction_mode="hybrid", **kw)


def client(tmp_path, handler, clock=None, **kw):
    kwargs = {"clock": clock} if clock else {}
    return LLMClient(settings(tmp_path, **kw), transport=httpx.MockTransport(handler), sleep=lambda _s: None, **kwargs)


def test_grounded_answer_becomes_a_record(tmp_path):
    rec = parse_llm_answer(GOOD, [(1, DOC)], ocr_conf=None, source="text", strict=True)
    assert rec.value("invoice_number") == "INV-77" and rec.value("total") == 20.0
    assert rec.value("tax") is None and rec.dropped == []
    assert len(rec.line_items) == 1 and rec.line_items[0].amount == 20.0


def test_values_that_are_not_in_the_document_are_dropped(tmp_path):
    lie = json.loads(json.dumps(GOOD))
    lie["total"] = {"value": 999.0, "evidence": "Total 999.00"}  # quote does not exist
    lie["tax"] = {"value": 2.0, "evidence": "Total                           20.00"}  # quote exists, number is not in it
    lie["line_items"].append({"description": "Invented item", "quantity": 1, "unit_price": 5.0, "amount": 5.0, "evidence": "Invented item 5.00"})
    rec = parse_llm_answer(lie, [(1, DOC)], ocr_conf=None, source="text", strict=True)
    assert rec.value("total") is None and rec.value("tax") is None
    assert len(rec.line_items) == 1
    reasons = {d["field"] for d in rec.dropped}
    assert {"total", "tax", "line_item"} <= reasons


def test_request_is_well_formed_and_the_key_is_not_in_the_url(tmp_path):
    seen = {}

    def handler(req: httpx.Request):
        seen["url"], seen["headers"], seen["body"] = str(req.url), req.headers, json.loads(req.content)
        return httpx.Response(200, json=gem(GOOD))

    c = client(tmp_path, handler)
    assert c.complete_json("SYSTEM", "USER") == GOOD
    assert "test-key" not in seen["url"] and seen["headers"]["x-goog-api-key"] == "test-key"
    gen = seen["body"]["generationConfig"]
    assert gen["temperature"] == 0 and gen["responseMimeType"] == "application/json" and "responseSchema" in gen


def test_transient_errors_are_retried(tmp_path):
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        return httpx.Response(503) if calls["n"] == 1 else httpx.Response(200, json=gem(GOOD))

    c = client(tmp_path, handler)
    assert c.complete_json("s", "u")["invoice_number"]["value"] == "INV-77"
    assert calls["n"] == 2


def test_rate_limit_waits_for_retry_after(tmp_path):
    waits = []
    seq = iter([httpx.Response(429, headers={"retry-after": "3"}), httpx.Response(200, json=gem(GOOD))])
    c = LLMClient(settings(tmp_path), transport=httpx.MockTransport(lambda r: next(seq)), sleep=waits.append)
    c.complete_json("s", "u")
    assert waits == [3.0]


def test_bad_key_stops_at_once_and_pauses_further_calls(tmp_path):
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        return httpx.Response(401, json={"error": "bad key"})

    c = client(tmp_path, handler)
    with pytest.raises(LLMError) as e:
        c.complete_json("s", "u")
    assert e.value.kind == "auth" and calls["n"] == 1 and c.breaker_open
    with pytest.raises(LLMUnavailable):
        c.complete_json("s", "u")
    assert calls["n"] == 1  # the second call never left the machine


def test_circuit_breaker_opens_after_repeated_failures_and_closes_after_the_pause(tmp_path):
    t = [0.0]
    ok = {"on": False}

    def handler(req):
        return httpx.Response(200, json=gem(GOOD)) if ok["on"] else httpx.Response(500)

    c = client(tmp_path, handler, clock=lambda: t[0], llm_max_retries=0, llm_failure_threshold=3, llm_cooldown_seconds=60)
    for _ in range(3):
        with pytest.raises(LLMError):
            c.complete_json("s", "u")
    assert c.breaker_open
    with pytest.raises(LLMUnavailable):
        c.complete_json("s", "u")
    t[0] = 61.0
    ok["on"] = True
    assert c.complete_json("s", "u")  # works again after the pause
    assert not c.breaker_open


@pytest.mark.parametrize(
    "response, kind",
    [
        (httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": "this is not json"}]}, "finishReason": "STOP"}]}), "schema"),
        (httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": "[1, 2]"}]}, "finishReason": "STOP"}]}), "schema"),
        (httpx.Response(200, json=gem(GOOD, finish="MAX_TOKENS")), "truncated"),
        (httpx.Response(200, json={"promptFeedback": {"blockReason": "SAFETY"}}), "schema"),
    ],
)
def test_bad_answers_are_reported_not_trusted(tmp_path, response, kind):
    c = client(tmp_path, lambda r: response)
    with pytest.raises(LLMError) as e:
        c.complete_json("s", "u")
    assert e.value.kind == kind


def test_markdown_fences_around_json_are_accepted(tmp_path):
    body = {"candidates": [{"content": {"parts": [{"text": "```json\n" + json.dumps(GOOD) + "\n```"}]}, "finishReason": "STOP"}]}
    assert client(tmp_path, lambda r: httpx.Response(200, json=body)).complete_json("s", "u")["total"]["value"] == 20.0


def test_openai_style_provider(tmp_path):
    s = make_settings(tmp_path, llm_provider="openai", openai_api_key="k", openai_model="m", extraction_mode="hybrid")
    seen = {}

    def handler(req):
        seen["auth"], seen["body"] = req.headers["authorization"], json.loads(req.content)
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(GOOD)}, "finish_reason": "stop"}]})

    c = LLMClient(s, transport=httpx.MockTransport(handler), sleep=lambda _s: None)
    assert c.complete_json("s", "u")["invoice_number"]["value"] == "INV-77"
    assert seen["auth"] == "Bearer k" and seen["body"]["response_format"]["type"] == "json_schema"


def test_personal_data_is_removed_before_sending_but_amounts_stay():
    out = redact_pii(DOC + "IBAN GB82 WEST 1234 5698 7654 32\nCard 4111 1111 1111 1111\nA/C No: 0021345678")
    assert "billing@acme.example" not in out
    assert "1712-345678" not in out
    assert "GB82" not in out and "4111 1111" not in out and "0021345678" not in out
    assert "Total                           20.00" in out and "INV-77" in out


def test_what_leaves_the_machine_is_redacted_and_wrapped_as_data(tmp_path):
    seen = {}

    def handler(req):
        seen["text"] = json.loads(req.content)["contents"][0]["parts"][0]["text"]
        seen["system"] = json.loads(req.content)["systemInstruction"]["parts"][0]["text"]
        return httpx.Response(200, json=gem(GOOD))

    s = settings(tmp_path)
    ex = LLMExtractor(s, LLMClient(s, transport=httpx.MockTransport(handler), sleep=lambda _x: None))
    rec = ex.extract([(1, DOC)], ocr_conf=None, source="text")
    assert "billing@acme.example" not in seen["text"] and "<document>" in seen["text"]
    assert "ignore them" in seen["system"] and "null" in seen["system"]
    assert rec.value("invoice_number") == "INV-77"


def test_long_documents_are_split_into_several_calls(tmp_path):
    calls = []

    def handler(req):
        calls.append(json.loads(req.content)["contents"][0]["parts"][0]["text"])
        return httpx.Response(200, json=gem({**GOOD, "line_items": []}))

    s = settings(tmp_path, llm_max_input_chars=2000)
    ex = LLMExtractor(s, LLMClient(s, transport=httpx.MockTransport(handler), sleep=lambda _x: None))
    pages = [(i, DOC + ("filler line\n" * 60)) for i in range(1, 6)]
    ex.extract(pages, ocr_conf=None, source="text")
    assert len(calls) >= 3  # one for the header and totals, then the items chunk by chunk
    assert all(len(c) < 4000 for c in calls)


def test_when_the_ai_service_is_down_the_rules_result_is_used(tmp_path):
    s = settings(tmp_path, llm_max_retries=0)
    ex = LLMExtractor(s, LLMClient(s, transport=httpx.MockTransport(lambda r: httpx.Response(500)), sleep=lambda _x: None))
    out = extract_document([PageText(1, DOC)], None, s, ex)
    assert out.llm_failures >= 1
    rec = out.records[0]
    assert rec.value("invoice_number") == "INV-77" and rec.value("total") == 20.0
    assert any("LLM extraction failed" in n for n in rec.notes)


def test_hybrid_mode_merges_and_records_a_disagreement(tmp_path):
    wrong = json.loads(json.dumps(GOOD))
    wrong["invoice_number"] = {"value": "INV-77", "evidence": "Invoice No: INV-77"}
    wrong["supplier_name"] = {"value": "Karim Traders", "evidence": "Bill To: Karim Traders"}  # the model swapped the parties
    s = settings(tmp_path)
    ex = LLMExtractor(s, LLMClient(s, transport=httpx.MockTransport(lambda r: httpx.Response(200, json=gem(wrong))), sleep=lambda _x: None))
    rec = extract_document([PageText(1, DOC)], None, s, ex).records[0]
    assert rec.value("invoice_number") == "INV-77"
    assert rec.method in ("hybrid", "rules", "llm")
    assert rec.disagreements or rec.value("supplier_name") in ("ACME Supplies Ltd", "Karim Traders")
