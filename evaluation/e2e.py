"""End to end check through the real service: upload over the API, background worker, database, validation.

    python -m evaluation.e2e --split holdout

`extract_run` measures what the extractor reads. This script measures what the whole system does with it:

  faults       invoices with one injected error (wrong total, wrong line amount, missing number, ...).
               Was the expected validation code raised?
  duplicates   exact copies of a file, the same invoice in a different file, near duplicates, and look-alikes that
               are NOT duplicates. Was the right code raised, and only then?
  clean        ordinary invoices with nothing wrong. How many were still sent to manual review?

Files are uploaded in the order the manifest gives (originals first), the way a person would send them.
"""

from __future__ import annotations

import argparse
import json
import tempfile
import time
from collections import defaultdict
from pathlib import Path

from fastapi.testclient import TestClient

from docintel.api.app import create_app
from docintel.config import Settings
from docintel.jobs.worker import Worker

GROUPS = ("fault", "duplicate", "digital_pdf", "corrupt")


def run(split: str, dataset: str = "evaluation/dataset") -> dict:
    root = Path(dataset) / split
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    entries = [e for e in manifest["entries"] if e["group"] in GROUPS]
    with tempfile.TemporaryDirectory() as tmp:
        settings = Settings(data_dir=Path(tmp), workers=0, rate_limit_per_minute=0, embedding_provider="hash", llm_provider="none", extraction_mode="rules", _env_file=None)
        app = create_app(settings, start_workers=False)
        client = TestClient(app)
        worker = Worker(settings, app.state.factory, name="e2e")
        uploaded: dict[str, dict] = {}
        t0 = time.perf_counter()
        for e in (x for x in entries if x["group"] == "corrupt"):
            r = client.post("/api/documents", files=[("files", (Path(e["file"]).name, (root / e["file"]).read_bytes(), "application/octet-stream"))])
            uploaded[e["id"]] = r.json()["items"][0]
        worker.drain()
        for step in sorted({e.get("step", 0) for e in entries if e["group"] != "corrupt"}):
            for e in (x for x in entries if x["group"] != "corrupt" and x.get("step", 0) == step):
                data = (root / e["file"]).read_bytes()
                r = client.post("/api/documents", files=[("files", (Path(e["file"]).name, data, "application/octet-stream"))])
                uploaded[e["id"]] = r.json()["items"][0]
            worker.drain()
        wall = time.perf_counter() - t0
        detail: dict[str, dict] = {}
        for e in entries:
            up = uploaded[e["id"]]
            if up["status"] == "queued":
                detail[e["id"]] = client.get(f"/api/documents/{up['document_id']}").json()
    return {"entries": entries, "uploaded": uploaded, "detail": detail, "wall_seconds": wall, "files": len(entries)}


def summarize(res: dict) -> dict:
    entries, uploaded, detail = res["entries"], res["uploaded"], res["detail"]

    def codes(eid: str) -> set[str]:
        return {i["code"] for i in detail.get(eid, {}).get("issues", []) if i["severity"] in ("error", "warning")}

    faults: dict[str, list[bool]] = defaultdict(list)
    not_in_file: dict[str, int] = defaultdict(int)
    for e in (x for x in entries if x["group"] == "fault"):
        if not e.get("realized", True):
            not_in_file[e["fault"]] += 1  # the layout could not print this fault, so there is nothing to detect
            continue
        faults[e["fault"]].append(set(e["expect_codes"]) <= codes(e["id"]))
    dups: dict[str, list[bool]] = defaultdict(list)
    for e in (x for x in entries if x["group"] == "duplicate"):
        role = e.get("dup_role")
        if role == "exact_file":
            dups[role].append(uploaded[e["id"]]["status"] == "duplicate")
        elif role in ("reissue", "near"):
            dups[role].append(set(e["expect_codes"]) <= codes(e["id"]))
        elif role == "negative":
            dups[role].append(not (set(e.get("expect_not") or []) & codes(e["id"])))
        elif role == "original":
            dups["original_not_flagged"].append(not ({"DUPLICATE_INVOICE", "POSSIBLE_DUPLICATE"} & codes(e["id"])))
    broken: dict[str, list[bool]] = defaultdict(list)
    broken_misses: list[str] = []
    for e in (x for x in entries if x["group"] == "corrupt"):
        up, d = uploaded[e["id"]], detail.get(e["id"], {})
        kind, _, code = e["expect"].partition(":")
        if kind == "rejected":
            ok = up["status"] == "rejected" and up.get("code") == code
        elif kind == "failed":
            ok = d.get("status") == "failed" and d.get("error_code") == code
        else:  # empty: processed to the end, found no invoice, and says so
            ok = d.get("status") == "completed" and (d.get("record_count") == 0 or "NO_INVOICE_DETECTED" in {i["code"] for i in d.get("issues", [])})
        broken[kind].append(ok)
        if not ok:
            broken_misses.append(f"{e['file']} expected {e['expect']}, got {up.get('status')}/{up.get('code') or d.get('status')}/{d.get('error_code')}")
    clean = [e for e in entries if e["group"] == "digital_pdf"]
    clean_flagged = [e["id"] for e in clean if detail.get(e["id"], {}).get("review_status") == "needs_review"]
    completed = sum(1 for d in detail.values() if d.get("status") == "completed")
    failed = sum(1 for d in detail.values() if d.get("status") == "failed")
    return {
        "files_uploaded": res["files"],
        "documents_processed": len(detail),
        "completed": completed,
        "failed": failed,
        "wall_seconds": round(res["wall_seconds"], 1),
        "faults": {k: {"n": len(v), "detected": sum(v), "not_printable": not_in_file.get(k, 0)} for k, v in sorted(faults.items())},
        "fault_detection_rate": round(sum(sum(v) for v in faults.values()) / max(1, sum(len(v) for v in faults.values())), 4),
        "duplicates": {k: {"n": len(v), "correct": sum(v)} for k, v in sorted(dups.items())},
        "broken_files": {k: {"n": len(v), "as_expected": sum(v)} for k, v in sorted(broken.items())},
        "broken_misses": broken_misses,
        "clean_invoices": len(clean),
        "clean_sent_to_review": len(clean_flagged),
        "clean_sent_to_review_ids": clean_flagged[:20],
    }


def render(s: dict, split: str) -> str:
    lines = [f"end to end check, split {split}", f"{s['files_uploaded']} files uploaded through the API, {s['documents_processed']} queued and processed ({s['completed']} completed, {s['failed']} failed) in {s['wall_seconds']} s", ""]
    lines.append("injected faults: was the expected code raised?")
    for k, v in s["faults"].items():
        extra = f"   ({v['not_printable']} more files could not print this fault and are left out)" if v["not_printable"] else ""
        lines.append(f"  {k:<20} {v['detected']:>3} of {v['n']:<3}{extra}")
    lines.append(f"  overall detection rate {s['fault_detection_rate']:.3f}")
    lines += ["", "duplicates"]
    for k, v in s["duplicates"].items():
        lines.append(f"  {k:<22} {v['correct']:>3} of {v['n']:<3} correct")
    lines += ["", "broken and hostile files: did the system end where it should?"]
    for k, v in s["broken_files"].items():
        lines.append(f"  {k:<10} {v['as_expected']:>3} of {v['n']:<3} as expected")
    lines += [f"  MISS {m}" for m in s["broken_misses"]]
    lines += ["", f"clean invoices sent to manual review: {s['clean_sent_to_review']} of {s['clean_invoices']}"]
    if s["clean_sent_to_review_ids"]:
        lines.append("  " + ", ".join(s["clean_sent_to_review_ids"]))
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="dev")
    ap.add_argument("--dataset", default="evaluation/dataset")
    ap.add_argument("--json", default=None, help="also write the numbers to this file")
    a = ap.parse_args()
    s = summarize(run(a.split, a.dataset))
    print(render(s, a.split))
    if a.json:
        Path(a.json).parent.mkdir(parents=True, exist_ok=True)
        Path(a.json).write_text(json.dumps(s, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
