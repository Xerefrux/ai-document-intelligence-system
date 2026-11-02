"""Time the whole system on one very large text PDF: about 1000 pages, many invoices.

    python -m evaluation.build --perf --out evaluation/dataset
    python -m evaluation.perf

The file goes in through the real API, a worker processes it, and the results are read back from the database, so the
figures include reading, extraction, validation, saving and indexing. Accuracy is checked against the ground truth that
the generator wrote next to the file. This is a digital (text layer) PDF; scanned pages are far slower and are not covered here.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from pathlib import Path

try:
    import resource  # Linux and macOS only
except ImportError:  # Windows: the memory figure is left out
    resource = None

from fastapi.testclient import TestClient
from sqlalchemy import select

from docintel.api.app import create_app
from docintel.config import Settings
from docintel.db.engine import session_scope
from docintel.db.models import Record
from docintel.extraction.normalize import normalize_invoice_number
from docintel.jobs.worker import Worker


def peak_memory_mb() -> int | None:
    """Highest memory use of this process so far, or None where the system cannot say (Windows)."""
    if resource is None:
        return None
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return round(peak / (1024 * 1024 if sys.platform == "darwin" else 1024))  # macOS reports bytes, Linux kilobytes


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", default="evaluation/dataset/perf")
    ap.add_argument("--json", default=None)
    a = ap.parse_args()
    root = Path(a.dataset)
    truth = json.loads((root / "text_truth.json").read_text(encoding="utf-8"))
    pdf = root / truth["file"]
    with tempfile.TemporaryDirectory() as tmp:
        settings = Settings(data_dir=Path(tmp), workers=0, rate_limit_per_minute=0, embedding_provider="hash", llm_provider="none", extraction_mode="rules", _env_file=None)
        app = create_app(settings, start_workers=False)
        client = TestClient(app)
        data = pdf.read_bytes()
        t0 = time.perf_counter()
        r = client.post("/api/documents", files=[("files", (pdf.name, data, "application/pdf"))])
        upload_s = time.perf_counter() - t0
        doc_id = r.json()["items"][0]["document_id"]
        t1 = time.perf_counter()
        Worker(settings, app.state.factory, name="perf").drain()
        process_s = time.perf_counter() - t1
        doc = client.get(f"/api/documents/{doc_id}").json()
        with session_scope(app.state.factory) as s:
            rows = s.execute(select(Record.invoice_number, Record.supplier_name, Record.total, Record.review_status)).all()
        # Two different suppliers can issue the same invoice number, and the generator does produce such pairs, so a record is
        # looked up by number and supplier together.
        got = {(normalize_invoice_number(n), (sup or "").casefold()): (t, st) for n, sup, t, st in rows if n}
        right_number = right_total = 0
        for t in truth["records"]:
            key = (normalize_invoice_number(t["invoice_number"]), (t["supplier_name"] or "").casefold())
            if key in got:
                right_number += 1
                right_total += got[key][0] is not None and t["total"] is not None and abs(got[key][0] - t["total"]) <= 0.011
        needs_review = sum(1 for _, _, _, st in rows if st == "needs_review")
        q = {}
        for name, params in {"keyword (invoice number)": {"q": truth["records"][len(truth["records"]) // 2]["invoice_number"], "mode": "keyword"}, "keyword (words)": {"q": "thermometer gloves", "mode": "keyword"}, "hybrid (words)": {"q": "thermometer gloves", "mode": "hybrid"}}.items():
            t2 = time.perf_counter()
            res = client.get("/api/search", params=params).json()
            q[name] = {"seconds": round(time.perf_counter() - t2, 3), "records": len(res.get("records", [])), "pages": len(res.get("pages", []))}
    out = {
        "file": pdf.name, "size_mb": round(len(data) / 1e6, 1), "pages": truth["pages"], "invoices_in_file": truth["invoices"],
        "status": doc["status"], "upload_seconds": round(upload_s, 2), "processing_seconds": round(process_s, 1),
        "pages_per_second": round(truth["pages"] / process_s, 1), "invoices_found": len(rows), "invoice_numbers_right": right_number,
        "totals_right": int(right_total), "records_needing_review": needs_review, "peak_memory_mb": peak_memory_mb(),
        "search": q,
    }
    print(json.dumps(out, indent=1))
    if a.json:
        Path(a.json).write_text(json.dumps(out, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
