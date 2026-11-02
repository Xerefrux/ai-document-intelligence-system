"""Run the processing pipeline over a built dataset split and write the raw predictions.

    python -m evaluation.extract_run --split dev --groups digital_pdf spreadsheet --jobs 2

The same `process_file` function the background worker uses, so the numbers describe the real pipeline.
Nothing here touches the database; duplicate and cross-document checks are measured by evaluation/e2e.py.
"""

from __future__ import annotations

import argparse
import json
import os
import time
from multiprocessing import get_context
from pathlib import Path

os.environ.setdefault("OMP_THREAD_LIMIT", "1")

from docintel.config import Settings  # noqa: E402
from docintel.pipeline.process import process_file  # noqa: E402
from docintel.pipeline.types import DocIntelError  # noqa: E402
from docintel.validation.rules import review_needed  # noqa: E402


def record_to_dict(rec) -> dict:
    d = {name: fv.value for name, fv in rec.fields.items()}
    d["line_items"] = [{"description": li.description, "quantity": li.quantity, "unit_price": li.unit_price, "amount": li.amount} for li in rec.line_items]
    d["_conf"] = {name: fv.confidence for name, fv in rec.fields.items() if fv.value is not None}
    d["_kind"] = rec.kind
    d["_method"] = rec.method
    return d


def one(args: tuple[str, str, dict]) -> dict:
    entry_id, path, overrides = args
    settings = Settings(**overrides)
    t0 = time.perf_counter()
    try:
        res = process_file(path, settings)
    except DocIntelError as exc:
        return {"id": entry_id, "status": "failed", "code": exc.code, "message": str(exc), "seconds": time.perf_counter() - t0}
    except Exception as exc:  # noqa: BLE001 - an unexpected crash is a result too
        return {"id": entry_id, "status": "crashed", "code": type(exc).__name__, "message": str(exc)[:300], "seconds": time.perf_counter() - t0}
    recs = [record_to_dict(r) for r in res.records]
    issues = [[{"code": i.code, "severity": i.severity, "field": i.field} for i in lst] for lst in res.issues]
    return {
        "id": entry_id, "status": "completed", "seconds": time.perf_counter() - t0, "timings": res.timings, "pages": len(res.pages),
        "kind": res.kind, "records": recs, "issues": issues, "needs_review": [review_needed(lst) for lst in res.issues],
        "ocr_conf": [p.ocr_confidence for p in res.pages if p.ocr_confidence is not None],
        "warnings": res.warnings[:10], "notes": res.outcome.notes,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="dev")
    ap.add_argument("--dataset", default="evaluation/dataset")
    ap.add_argument("--groups", nargs="*", default=None)
    ap.add_argument("--jobs", type=int, default=2)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--out", default=None)
    ap.add_argument("--set", nargs="*", default=[], help="settings overrides like extraction_mode=rules")
    a = ap.parse_args()
    root = Path(a.dataset) / a.split
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    entries = [e for e in manifest["entries"] if not a.groups or e["group"] in a.groups]
    if a.limit:
        entries = entries[: a.limit]
    overrides = dict(kv.split("=", 1) for kv in a.set)
    overrides.setdefault("ocr_processes", "1")
    work = [(e["id"], str(root / e["file"]), overrides) for e in entries]
    t0 = time.perf_counter()
    ctx = get_context("spawn")
    with ctx.Pool(a.jobs) as pool:
        results = pool.map(one, work, chunksize=1)
    wall = time.perf_counter() - t0
    out = Path(a.out or f"evaluation/work/{a.split}_predictions.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"wall_seconds": wall, "jobs": a.jobs, "overrides": overrides, "results": results}, default=str), encoding="utf-8")
    ok = sum(r["status"] == "completed" for r in results)
    print(f"{len(results)} files in {wall:.1f}s, {ok} completed, wrote {out}")


if __name__ == "__main__":
    main()
