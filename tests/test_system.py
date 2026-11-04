"""The whole service end to end: upload, background processing, results, corrections, search, security and failure handling."""

from __future__ import annotations

import random
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from docintel.api.app import create_app
from docintel.db.engine import session_scope
from docintel.jobs import queue as Q
from docintel.jobs.worker import Worker
from docintel.pipeline.types import CorruptFileError, OcrError
from evaluation.synth import faults as fl
from tests.conftest import make_settings
from tests.factory import LEDGER_HEADER, Factory


def up(c: TestClient, *files: Path, **params):
    parts = [("files", (f.name, f.read_bytes(), "application/octet-stream")) for f in files]
    return c.post("/api/documents", files=parts, params=params)


def drain(app, settings):
    return Worker(settings, app.state.factory, name="test-worker").drain()


def by_name(c: TestClient):
    return {d["filename"]: d for d in c.get("/api/documents", params={"limit": 200}).json()["items"]}


@pytest.fixture()
def env(tmp_path):
    s = make_settings(tmp_path)
    app = create_app(s, start_workers=False)
    c = TestClient(app, raise_server_exceptions=False)
    return s, app, c, Factory(tmp_path / "in")


@pytest.fixture()
def loaded(env):
    """A service that already holds a mix of processed files."""
    s, app, c, make = env
    files = []
    for i, tpl in enumerate(["T1", "T2", "T4", "T5", "T6"]):
        p, _ = make.pdf(f"inv-{tpl}.pdf", seed=60 + i, template=tpl, tax_rate=10.0)
        files.append(p)
    files.append(make.xlsx("register.xlsx", {"Register": [LEDGER_HEADER, ["L-1", "2026-03-01", "Shapla Traders", "Karim", "USD", 100.0, 10.0, 110.0], ["L-2", "2026-03-02", "Shapla Traders", "Karim", "USD", 200.0, 20.0, 220.0]]}))
    files.append(make.truncated(files[0], "broken.pdf", 0.4))
    r = up(c, *files)
    assert r.status_code in (200, 201, 202), r.text
    assert drain(app, s) == len(files)
    return s, app, c, make


# ---------------------------------------------------------------------------- upload and background processing


def test_many_files_are_accepted_and_each_gets_its_own_status(env):
    s, app, c, make = env
    ok, _ = make.pdf("a.pdf", seed=1)
    bad = make.raw("virus.pdf", b"MZ" + b"\x00" * 200)
    empty = make.raw("empty.pdf", b"")
    txt = make.raw("notes.txt", b"hello")
    r = up(c, ok, bad, empty, txt)
    items = {i["filename"]: i for i in r.json()["items"]}
    assert items["a.pdf"]["status"] == "queued"
    assert items["virus.pdf"]["status"] == "rejected" and items["virus.pdf"]["code"] == "UNSUPPORTED_TYPE"
    assert items["empty.pdf"]["code"] == "EMPTY_FILE" and items["notes.txt"]["status"] == "rejected"


def test_a_document_is_queued_until_a_worker_takes_it(env):
    s, app, c, make = env
    p, _ = make.pdf("q.pdf", seed=2)
    up(c, p)
    d = by_name(c)["q.pdf"]
    assert d["status"] == "queued"
    drain(app, s)
    d = c.get(f"/api/documents/{d['id']}").json()
    assert d["status"] == "completed" and d["progress"] == 1.0 and d["record_count"] == 1
    assert d["events"] and d["events"][0]["stage"] in ("upload", "queued")


def test_results_include_every_field_with_evidence_and_line_items(loaded):
    s, app, c, make = loaded
    d = c.get(f"/api/documents/{by_name(c)['inv-T1.pdf']['id']}").json()
    rec = c.get(f"/api/records/{d['records'][0]['id']}").json()
    f = rec["fields"]["invoice_number"]
    assert f["value"] and f["evidence"] and f["page"] == 1 and 0 < f["confidence"] <= 1
    assert len(rec["line_items"]) == 4 and rec["document"]["filename"] == "inv-T1.pdf"
    page = c.get(f"/api/documents/{d['id']}/pages/1/image")
    assert page.status_code == 200 and page.headers["content-type"].startswith("image/")
    assert "sandbox" in page.headers["content-security-policy"]


def test_failed_file_shows_why_and_is_not_retried_forever(loaded):
    s, app, c, make = loaded
    d = by_name(c)["broken.pdf"]
    assert d["status"] == "failed" and d["error_code"] == "CORRUPT_FILE" and d["error_message"]
    jobs = c.get("/api/jobs", params={"status": "failed"}).json()["items"]
    assert any(j["document_id"] == d["id"] and j["attempts"] == 1 for j in jobs)  # permanent errors are not retried


def test_the_same_file_twice_is_recognised_by_its_content(env):
    s, app, c, make = env
    p, _ = make.pdf("one.pdf", seed=3)
    copy = make.raw("copy-of-one.pdf", p.read_bytes())
    up(c, p)
    r = up(c, copy).json()
    assert r["items"][0]["status"] == "duplicate"
    assert len(by_name(c)) == 1
    forced = up(c, copy, force="true").json()
    assert forced["items"][0]["status"] == "queued"


def test_the_same_invoice_in_a_different_file_is_flagged_after_processing(env):
    s, app, c, make = env
    inv = make.invoice(70, tax_rate=10.0)
    a, _ = make.pdf("orig.pdf", invoices=[inv], template="T1")
    b, _ = make.pdf("reissue.pdf", invoices=[inv], template="T4")
    up(c, a)
    drain(app, s)
    up(c, b)
    drain(app, s)
    d = c.get(f"/api/documents/{by_name(c)['reissue.pdf']['id']}").json()
    assert d["duplicate_of"] or any(i["code"] in ("DUPLICATE_INVOICE", "POSSIBLE_DUPLICATE") for i in d["issues"])
    assert d["review_status"] == "needs_review"


# ---------------------------------------------------------------------------- validation through the API


def test_faulty_invoices_end_up_in_the_review_queue(env):
    s, app, c, make = env
    for k, fault in enumerate(["total_wrong", "missing_number", "line_amount_wrong"]):
        p, _ = make.pdf(f"{fault}.pdf", invoices=[fl.faulty_invoice(random.Random(80 + k), fault, n_items=4)])
        up(c, p)
    drain(app, s)
    issues = c.get("/api/issues").json()
    assert {"TOTAL_MISMATCH", "MISSING_FIELD", "LINE_ITEM_MISMATCH"} <= set(issues["by_code"])
    needs = c.get("/api/records", params={"review_status": "needs_review"}).json()
    assert needs["total"] == 3 and all(r["issues"] for r in needs["items"])


# ---------------------------------------------------------------------------- manual corrections


def _first_record(c, name="inv-T1.pdf"):
    d = c.get(f"/api/documents/{by_name(c)[name]['id']}").json()
    return c.get(f"/api/records/{d['records'][0]['id']}").json()


def test_a_correction_is_validated_versioned_and_kept_in_the_audit_trail(loaded):
    s, app, c, make = loaded
    rec = _first_record(c)
    r = c.patch(f"/api/records/{rec['id']}", json={"fields": {"total": "1,234.50"}, "note": "typo on the scan", "expected_version": rec["version"]})
    j = r.json()
    assert r.status_code == 200 and j["changed"] and j["record"]["version"] == rec["version"] + 1
    assert j["record"]["fields"]["total"]["value"] == 1234.5 and j["record"]["fields"]["total"]["source"] == "manual"
    assert "TOTAL_MISMATCH" in {i["code"] for i in j["record"]["issues"]}  # checks run again on the new value
    assert j["record"]["corrections"][0]["field"] == "total" and j["record"]["corrections"][0]["note"] == "typo on the scan"
    back = c.patch(f"/api/records/{rec['id']}", json={"fields": {"total": rec["fields"]["total"]["value"]}}).json()["record"]
    assert "TOTAL_MISMATCH" not in {i["code"] for i in back["issues"]} and back["review_status"] in ("corrected", "ok")


def test_two_people_cannot_silently_overwrite_each_other(loaded):
    s, app, c, make = loaded
    rec = _first_record(c)
    first = c.patch(f"/api/records/{rec['id']}", json={"fields": {"customer_name": "A"}, "expected_version": rec["version"]})
    second = c.patch(f"/api/records/{rec['id']}", json={"fields": {"customer_name": "B"}, "expected_version": rec["version"]})
    assert first.status_code == 200 and second.status_code == 409 and second.json()["error"]["code"] == "VERSION_CONFLICT"


@pytest.mark.parametrize("body", [{"fields": {"invoice_date": "31/12/2025"}}, {"fields": {"total": "abc"}}, {"fields": {"nonsense": 1}}, {}])
def test_bad_corrections_are_refused_with_a_reason(loaded, body):
    s, app, c, make = loaded
    r = c.patch(f"/api/records/{_first_record(c)['id']}", json=body)
    assert r.status_code == 422 and r.json()["error"]["message"]


def test_line_items_can_be_replaced_and_are_rechecked(loaded):
    s, app, c, make = loaded
    rec = _first_record(c)
    items = [{"description": "Only item", "quantity": 1, "unit_price": 5.0, "amount": 5.0}]
    j = c.patch(f"/api/records/{rec['id']}", json={"line_items": items}).json()["record"]
    assert len(j["line_items"]) == 1
    assert "SUBTOTAL_MISMATCH" in {i["code"] for i in j["issues"]}


def test_approving_marks_the_record_checked_even_with_open_issues(loaded):
    s, app, c, make = loaded
    rec = _first_record(c)
    c.patch(f"/api/records/{rec['id']}", json={"fields": {"total": "9.99"}})
    j = c.post(f"/api/records/{rec['id']}/approve", json={"note": "checked against paper"}).json()
    assert j["review_status"] == "approved"
    assert any("open issues" in (x["note"] or "") for x in j["corrections"])


def test_reprocessing_asks_before_throwing_away_a_persons_corrections(loaded):
    s, app, c, make = loaded
    rec = _first_record(c)
    c.patch(f"/api/records/{rec['id']}", json={"fields": {"customer_name": "Changed"}})
    doc_id = rec["document"]["id"]
    assert c.post(f"/api/documents/{doc_id}/reprocess").json()["error"]["code"] == "CORRECTIONS_WOULD_BE_LOST"
    assert c.post(f"/api/documents/{doc_id}/reprocess", json={"confirm": True}).status_code in (200, 202)
    drain(app, s)
    assert _first_record(c)["fields"]["customer_name"]["source"] != "manual"


def test_reprocess_failed_skips_files_that_can_never_work(loaded):
    s, app, c, make = loaded
    j = c.post("/api/documents/reprocess-failed").json()
    assert j["requeued"] == 0 and any(x["filename"] == "broken.pdf" for x in j["skipped"])


def test_a_record_that_should_not_exist_can_be_deleted_and_one_can_be_added(loaded):
    s, app, c, make = loaded
    rec = _first_record(c)
    assert c.delete(f"/api/records/{rec['id']}").status_code == 204
    assert c.get(f"/api/records/{rec['id']}").status_code == 404
    added = c.post(f"/api/documents/{rec['document']['id']}/records")
    assert added.status_code == 201 and added.json()["review_status"] == "needs_review"


# ---------------------------------------------------------------------------- search


def test_keyword_search_finds_an_invoice_number_exactly_and_highlights_it(loaded):
    s, app, c, make = loaded
    num = _first_record(c)["fields"]["invoice_number"]["value"]
    j = c.get("/api/search", params={"q": num, "mode": "keyword"}).json()
    assert j["records"] and j["records"][0]["invoice_number"] == num and "\x02" in (j["records"][0]["snippet"] or "")
    assert len(j["records"]) == 1  # a number is not matched loosely


def test_search_by_supplier_text_filters_and_typos(loaded):
    s, app, c, make = loaded
    rec = _first_record(c)
    sup = rec["fields"]["supplier_name"]["value"]
    word = sup.split()[0]
    assert c.get("/api/search", params={"q": word, "mode": "keyword"}).json()["total_records"] >= 1
    typo = word[:-1] + ("x" if word[-1] != "x" else "y")
    sem = c.get("/api/search", params={"q": typo.lower(), "mode": "semantic"}).json()
    assert any(r["id"] == rec["id"] for r in sem["records"]) and sem["semantic_kind"] == "fuzzy"
    flt = c.get("/api/search", params={"q": "", "supplier": word, "min_total": 0.01, "review_status": rec["review_status"]}).json()
    assert any(r["id"] == rec["id"] for r in flt["records"])
    assert c.get("/api/search", params={"q": "zzzqqqxxnotthere", "mode": "hybrid"}).json()["total_records"] == 0


def test_every_search_hit_has_the_same_fields_with_or_without_a_text_query(loaded):
    """The web page reads score, matched_by and snippet on every hit. An empty search once left them out and the page went blank."""
    s, app, c, make = loaded
    word = _first_record(c)["fields"]["supplier_name"]["value"].split()[0]
    for params in ({"q": ""}, {"q": "", "supplier": word}, {"q": word}, {"q": word, "mode": "keyword"}, {"q": word, "mode": "semantic"}):
        hits = c.get("/api/search", params=params).json()["records"]
        assert hits, params
        for h in hits:
            assert isinstance(h["matched_by"], list) and "snippet" in h and isinstance(h["score"], (int, float)), (params, h)
    browse = c.get("/api/search", params={"q": ""}).json()["records"]
    assert all(h["matched_by"] == [] and h["snippet"] is None for h in browse)


def test_search_covers_the_full_text_of_pages_and_deleted_documents_disappear(loaded):
    s, app, c, make = loaded
    d = by_name(c)["inv-T2.pdf"]
    text_page = c.get(f"/api/documents/{d['id']}/text").json()["pages"][0]["text"]
    word = next(w for w in text_page.split() if w.isalpha() and len(w) > 6)
    assert c.get("/api/search", params={"q": word, "mode": "keyword"}).json()["pages"]
    assert c.delete(f"/api/documents/{d['id']}").status_code in (200, 204)
    assert not [p for p in c.get("/api/search", params={"q": word, "mode": "keyword"}).json()["pages"] if p["document_id"] == d["id"]]
    assert not list(s.uploads_dir.glob(d["id"] + "*"))


def test_filters_work_without_a_text_query(loaded):
    s, app, c, make = loaded
    j = c.get("/api/records", params={"file_type": "xlsx", "limit": 10}).json()
    assert j["total"] == 2 and {r["filename"] for r in j["items"]} == {"register.xlsx"}


# ---------------------------------------------------------------------------- export and cross verification


def test_export_neutralises_spreadsheet_formulas(loaded):
    s, app, c, make = loaded
    rec = _first_record(c)
    c.patch(f"/api/records/{rec['id']}", json={"fields": {"supplier_name": "=HYPERLINK(\"http://evil\",\"x\")"}})
    body = c.get("/api/export/records", params={"format": "csv"}).text
    assert "'=HYPERLINK" in body and ",=HYPERLINK" not in body
    assert body.splitlines()[0].lstrip("﻿").startswith("record_id,")
    assert isinstance(c.get("/api/export/records", params={"format": "json"}).json(), list)


def test_cross_verification_compares_invoices_with_the_register(env):
    s, app, c, make = env
    inv = make.invoice(90, tax_rate=10.0)
    pdf, truth = make.pdf("matching.pdf", invoices=[inv], template="T1")
    t = truth[0]
    rows = [LEDGER_HEADER, [t["invoice_number"], t["invoice_date"], t["supplier_name"], t["customer_name"] or "", "USD", t["subtotal"], t["tax"], t["total"] + 5.0]]
    up(c, pdf, make.xlsx("reg.xlsx", {"R": rows}))
    drain(app, s)
    j = c.post("/api/reconciliations", json={"mode": "auto"}).json()
    assert j["summary"]["MISMATCH"] == 1 and j["items"][0]["diffs"][0]["field"] == "total"
    assert c.get(f"/api/reconciliations/{j['id']}").status_code == 200


# ---------------------------------------------------------------------------- queue, workers and crashes


def test_a_temporary_failure_is_retried_and_then_succeeds(env, monkeypatch):
    s, app, c, make = env
    import docintel.jobs.runner as runner

    real, calls = runner.open_reader, {"n": 0}

    def flaky(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OcrError("engine crashed")
        return real(*a, **k)

    monkeypatch.setattr(runner, "open_reader", flaky)
    p, _ = make.pdf("flaky.pdf", seed=4)
    up(c, p)
    w = Worker(s, app.state.factory, name="w")
    w.drain()
    time.sleep(0.2)  # the retry waits job_backoff_seconds
    w.drain()
    d = by_name(c)["flaky.pdf"]
    assert d["status"] == "completed" and calls["n"] == 2
    job = c.get("/api/jobs").json()["items"][0]
    assert job["attempts"] == 2 and job["status"] == "succeeded"


def test_a_job_that_keeps_failing_stops_after_the_attempt_limit(env, monkeypatch):
    s, app, c, make = env
    import docintel.jobs.runner as runner

    monkeypatch.setattr(runner, "open_reader", lambda *a, **k: (_ for _ in ()).throw(OcrError("always broken")))
    p, _ = make.pdf("doomed.pdf", seed=5)
    up(c, p)
    w = Worker(s, app.state.factory, name="w")
    for _ in range(6):
        w.drain()
        time.sleep(0.3)
    d = by_name(c)["doomed.pdf"]
    assert d["status"] == "failed" and d["error_code"] == "OCR_FAILED"
    assert c.get("/api/jobs").json()["items"][0]["attempts"] == s.job_max_attempts


def test_a_worker_that_dies_mid_job_does_not_lose_the_document(env):
    s, app, c, make = env
    p, _ = make.pdf("orphan.pdf", seed=6)
    up(c, p)
    job_id = Q.claim_next(app.state.factory, "ghost-worker")  # claimed, then the worker vanished
    assert job_id
    with session_scope(app.state.factory) as sess:
        sess.execute(text("UPDATE jobs SET heartbeat_at = datetime('now', '-1 hour') WHERE id = :i"), {"i": job_id})
    assert Q.recover_stale(app.state.factory, s) == 1
    drain(app, s)
    assert by_name(c)["orphan.pdf"]["status"] == "completed"
    events = c.get(f"/api/documents/{by_name(c)['orphan.pdf']['id']}").json()["events"]
    assert any("worker stopped" in e["message"].lower() for e in events)


def test_two_workers_never_take_the_same_job(env):
    s, app, c, make = env
    files = [make.pdf(f"p{i}.pdf", seed=200 + i)[0] for i in range(12)]
    up(c, *files)
    ws = [Worker(s, app.state.factory, name=f"w{i}") for i in range(3)]
    threads = [threading.Thread(target=w.drain) for w in ws]
    [t.start() for t in threads]
    [t.join(120) for t in threads]
    jobs = c.get("/api/jobs", params={"limit": 50}).json()["items"]
    assert len(jobs) == 12 and all(j["status"] == "succeeded" and j["attempts"] == 1 for j in jobs)
    assert sum(w.done for w in ws) == 12


def test_a_queued_document_can_be_cancelled(env):
    s, app, c, make = env
    p, _ = make.pdf("cancel.pdf", seed=7)
    up(c, p)
    d = by_name(c)["cancel.pdf"]
    assert c.post(f"/api/documents/{d['id']}/cancel").status_code in (200, 202)
    assert drain(app, s) == 0
    assert c.get(f"/api/documents/{d['id']}").json()["status"] == "cancelled"


def test_health_reports_a_stalled_queue(env):
    s, app, c, make = env
    assert c.get("/api/health").json()["status"] == "ok"
    p, _ = make.pdf("stuck.pdf", seed=8)
    up(c, p)
    with session_scope(app.state.factory) as sess:
        sess.execute(text("UPDATE jobs SET created_at = datetime('now', '-5 minutes')"))
    h = c.get("/api/health").json()
    assert h["status"] == "degraded" and "worker" in h["message"].lower()


# ---------------------------------------------------------------------------- security and robustness


def test_api_key_protects_everything_except_health(tmp_path):
    s = make_settings(tmp_path, api_key="secret123")
    c = TestClient(create_app(s, start_workers=False), raise_server_exceptions=False)
    assert c.get("/api/health").status_code == 200
    assert c.get("/api/documents").status_code == 401
    assert c.get("/api/documents", headers={"X-API-Key": "nope"}).status_code == 401
    assert c.get("/api/documents", headers={"X-API-Key": "secret123"}).status_code == 200
    assert c.get("/api/documents", headers={"Authorization": "Bearer secret123"}).status_code == 200
    assert "secret123" not in c.get("/api/config", headers={"X-API-Key": "secret123"}).text


def test_too_many_requests_get_429_with_retry_after(tmp_path):
    s = make_settings(tmp_path, rate_limit_per_minute=5)
    c = TestClient(create_app(s, start_workers=False), raise_server_exceptions=False)
    codes = [c.get("/api/documents").status_code for _ in range(9)]
    assert codes[:5] == [200] * 5 and 429 in codes
    r = c.get("/api/documents")
    assert r.status_code == 429 and int(r.headers["retry-after"]) >= 1 and r.json()["error"]["code"] == "RATE_LIMITED"


def test_an_upload_that_is_too_large_is_refused(tmp_path):
    s = make_settings(tmp_path, max_upload_mb=1)
    c = TestClient(create_app(s, start_workers=False), raise_server_exceptions=False)
    big = Factory(tmp_path / "x").raw("big.pdf", b"%PDF-1.4\n" + b"0" * (2 * 1024 * 1024))
    item = up(c, big).json()["items"][0]
    assert item["status"] == "rejected" and item["code"] in ("FILE_TOO_LARGE", "TOO_LARGE")
    assert not list(s.uploads_dir.glob("*"))


def test_a_hostile_file_name_cannot_escape_the_upload_folder(env):
    s, app, c, make = env
    p, _ = make.pdf("ok.pdf", seed=9)
    r = c.post("/api/documents", files=[("files", ("../../etc/passwd.pdf", p.read_bytes(), "application/pdf"))])
    assert r.status_code in (200, 201, 202)
    stored = [f for f in s.data_dir.rglob("*") if f.is_file() and f.name.endswith(".pdf")]
    assert stored and all(s.uploads_dir.resolve() in f.resolve().parents for f in stored)
    assert not (s.data_dir.parent / "etc").exists()


def test_errors_have_one_shape_and_security_headers_are_set(env):
    s, app, c, make = env
    for r in (c.get("/api/nope"), c.get("/api/documents/zzz"), c.get("/api/documents", params={"limit": 9999}), c.get("/api/records/zzz")):
        err = r.json()["error"]
        assert {"code", "message"} <= set(err) and r.status_code in (404, 422)
    h = c.get("/api/health").headers
    assert h["x-content-type-options"] == "nosniff" and "frame-ancestors" in h.get("content-security-policy", "") + "frame-ancestors"


def test_the_original_file_is_served_in_a_sandbox(loaded):
    s, app, c, make = loaded
    d = by_name(c)["inv-T1.pdf"]
    r = c.get(f"/api/documents/{d['id']}/file", params={"inline": "true"})
    assert r.status_code == 200 and "sandbox" in r.headers["content-security-policy"] and r.headers["x-content-type-options"] == "nosniff"


def test_stats_report_success_failure_and_time(loaded):
    s, app, c, make = loaded
    st = c.get("/api/stats").json()
    assert st["documents"]["total"] == 7 and st["documents"]["by_status"]["failed"] == 1
    assert st["documents"]["success_rate"] == pytest.approx(6 / 7, abs=0.001)
    assert st["processing"]["median_seconds"] is not None


def test_the_web_app_is_served_by_the_same_server(env):
    s, app, c, make = env
    r = c.get("/")
    assert r.status_code == 200 and ("<div id=\"root\"" in r.text or "DocIntel" in r.text)
