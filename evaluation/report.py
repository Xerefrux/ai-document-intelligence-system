"""Score a predictions file against its manifest.

    python -m evaluation.report --split dev                    # summary tables
    python -m evaluation.report --split dev --errors 40        # plus the first wrong fields, for debugging
"""

from __future__ import annotations

import argparse
import json
import statistics
from collections import defaultdict
from pathlib import Path

from .metrics import CORE, HEADLINE, aggregate, match_records, score_record


def load(split: str, dataset: str = "evaluation/dataset", predictions: str | None = None) -> tuple[dict, dict]:
    manifest = json.loads((Path(dataset) / split / "manifest.json").read_text(encoding="utf-8"))
    pred = json.loads(Path(predictions or f"evaluation/work/{split}_predictions.json").read_text(encoding="utf-8"))
    return manifest, pred


def score_entries(manifest: dict, pred: dict, *, groups: set[str] | None = None) -> tuple[list[dict], dict[str, dict]]:
    by_id = {r["id"]: r for r in pred["results"]}
    rows: list[dict] = []
    for e in manifest["entries"]:
        if groups and e["group"] not in groups:
            continue
        r = by_id.get(e["id"])
        if r is None or not e.get("records"):
            continue
        preds = r.get("records", []) if r["status"] == "completed" else []
        pairs = match_records(preds, e["records"])
        scored = [score_record(p, t) for p, t in pairs if t is not None]
        extra = sum(1 for p, t in pairs if t is None)
        rows.append({"entry": e, "result": r, "scored": scored, "extra": extra})
    return rows, by_id


def summarize(rows: list[dict], key) -> dict[str, dict]:
    buckets: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        buckets[key(row["entry"])].append(row)
    out = {}
    for k, rs in sorted(buckets.items()):
        scored = [s for r in rs for s in r["scored"]]
        agg = aggregate(scored)
        agg["files"] = len(rs)
        agg["files_completed"] = sum(r["result"]["status"] == "completed" for r in rs)
        agg["files_with_records"] = sum(1 for r in rs if r["result"].get("records"))
        agg["extra_records"] = sum(r.get("extra", 0) for r in rs)
        secs = [r["result"]["seconds"] for r in rs if r["result"]["status"] == "completed"]
        agg["seconds_median"] = round(statistics.median(secs), 3) if secs else None
        out[k] = agg
    return out


def silent_errors(rows: list[dict]) -> dict:
    """Documents with a wrong or invented headline value that were NOT flagged for review. These are the dangerous ones."""
    wrong_docs = flagged_wrong = silent = completed = 0
    for row in rows:
        r = row["result"]
        if r["status"] != "completed":
            continue
        completed += 1
        bad = any(o in ("wrong", "hallucinated") and f in HEADLINE for s in row["scored"] for f, o in s["fields"].items())
        flagged = any(r.get("needs_review") or [])
        if bad:
            wrong_docs += 1
            flagged_wrong += bool(flagged)
            silent += not flagged
    return {"documents": completed, "with_wrong_value": wrong_docs, "of_which_flagged": flagged_wrong, "silent": silent,
            "silent_rate": round(silent / completed, 4) if completed else None}


def fmt_table(title: str, table: dict[str, dict]) -> str:
    lines = [f"\n== {title}"]
    head = f"{'':22}{'files':>6}{'recs':>6}{'micro':>8}{'strict':>8}{'lineF1':>8}{'qty/unit':>9}{'halluc':>8}{'med s':>8}   " + " ".join(f"{f[:7]:>7}" for f in HEADLINE)
    lines.append(head)
    for k, a in table.items():
        accs = " ".join(f"{(a['field_accuracy'].get(f) if a['field_accuracy'].get(f) is not None else float('nan')):7.2f}" for f in HEADLINE)
        h = a["hallucination_rate"]
        lines.append(f"{k:22}{a['files']:6d}{a['records']:6d}{a['field_accuracy_micro']:8.3f}{(a['document_strict_accuracy'] or 0):8.3f}{(a['line_item_f1'] if a['line_item_f1'] is not None else float('nan')):8.3f}{(a['line_qty_unit_accuracy'] if a.get('line_qty_unit_accuracy') is not None else float('nan')):9.3f}{(h if h is not None else float('nan')):8.3f}{(a['seconds_median'] or 0):8.2f}   {accs}")
    return "\n".join(lines)


def print_errors(rows: list[dict], limit: int) -> None:
    shown = 0
    for row in rows:
        e, r = row["entry"], row["result"]
        if r["status"] != "completed":
            print(f"[FAILED] {e['id']} {r.get('code')} {r.get('message', '')[:100]}")
            shown += 1
            continue
        for s, (p, t) in zip(row["scored"], [pt for pt in match_records(r["records"], e["records"]) if pt[1] is not None]):
            bad = {f: (o, (p or {}).get(f), t.get(f)) for f, o in s["fields"].items() if o in ("wrong", "missed", "hallucinated", "missing_record") and (f in HEADLINE or f == "due_date")}
            tp, npred, ntrue = s["lines"]
            if bad or npred != ntrue or tp != ntrue:
                print(f"[{e['id']}] {e.get('template', '')} {e.get('scan', {}).get('level', '')}")
                for f, (o, got, want) in bad.items():
                    print(f"    {f:15} {o:12} got={got!r} want={want!r}")
                if npred != ntrue or tp != ntrue:
                    print(f"    line items: matched {tp}, predicted {npred}, true {ntrue}")
                shown += 1
        if shown >= limit:
            break


def _pct(values: list[float], p: float) -> float | None:
    if not values:
        return None
    v = sorted(values)
    k = (len(v) - 1) * p
    lo, hi = int(k), min(int(k) + 1, len(v) - 1)
    return v[lo] + (v[hi] - v[lo]) * (k - lo)


def overall(manifest: dict, pred: dict) -> str:
    """Success and failure rates and processing time over every valid file (the deliberately broken ones are reported by e2e.py)."""
    by_id = {r["id"]: r for r in pred["results"]}
    valid = [e for e in manifest["entries"] if e["group"] != "corrupt" and e["id"] in by_id]
    res = [(e, by_id[e["id"]]) for e in valid]
    done = [(e, r) for e, r in res if r["status"] == "completed"]
    failed = [(e, r) for e, r in res if r["status"] != "completed"]
    clean_groups = ("digital_pdf", "multi_invoice", "scan", "spreadsheet")  # the fault and duplicate files are flagged on purpose
    flagged = [r for e, r in done if e["group"] in clean_groups and ((not r.get("records")) or any(r.get("needs_review") or []))]
    n_clean_done = sum(1 for e, r in done if e["group"] in clean_groups)
    secs = [r["seconds"] for e, r in done]
    per_page = [r["seconds"] / max(1, r.get("pages") or 1) for e, r in done]
    lines = [
        "== overall (every valid file, deliberately broken files excluded)",
        f"files                     {len(res)}",
        f"completed                 {len(done)}   success rate {len(done) / max(1, len(res)):.3f}",
        f"failed or crashed         {len(failed)}   failure rate {len(failed) / max(1, len(res)):.3f}",
        f"flagged for review       {len(flagged)} of {n_clean_done} ordinary files ({len(flagged) / max(1, n_clean_done):.3f}); fault and duplicate files are left out because they are flagged on purpose",
        f"seconds per file          median {_pct(secs, 0.5):.2f}   p90 {_pct(secs, 0.9):.2f}   max {max(secs):.1f}",
        f"seconds per page          median {_pct(per_page, 0.5):.3f}   p90 {_pct(per_page, 0.9):.2f}",
        f"batch wall time           {pred.get('wall_seconds', 0):.1f} s for {len(pred['results'])} files with {pred.get('jobs')} parallel jobs",
    ]
    for g in sorted({e["group"] for e, _ in res}):
        gs = [r["seconds"] for e, r in done if e["group"] == g]
        gf = [1 for e, r in failed if e["group"] == g]
        if gs or gf:
            lines.append(f"  {g:<14} files {len(gs) + len(gf):>3}  failed {len(gf):>2}  median s {_pct(gs, 0.5) or 0:>6.2f}  p90 s {_pct(gs, 0.9) or 0:>6.2f}")
    for e, r in failed[:20]:
        lines.append(f"  FAILED {e['id']}: {r.get('code')} {str(r.get('message'))[:120]}")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="dev")
    ap.add_argument("--predictions", default=None)
    ap.add_argument("--groups", nargs="*", default=None)
    ap.add_argument("--errors", type=int, default=0)
    a = ap.parse_args()
    manifest, pred = load(a.split, predictions=a.predictions)
    rows, _ = score_entries(manifest, pred, groups=set(a.groups) if a.groups else None)
    print(overall(manifest, pred))
    print()
    print(fmt_table("by group", summarize(rows, lambda e: e["group"])))
    print(fmt_table("by template / layout", summarize(rows, lambda e: f"{e['group'][:6]}:{e.get('template') or e.get('layout') or '-'}")))
    for g in sorted({r["entry"]["group"] for r in rows}):
        print(f"silent errors, {g}: {silent_errors([r for r in rows if r['entry']['group'] == g])}")
    if any(r["entry"]["group"] == "scan" for r in rows):
        print(fmt_table("scans by level", summarize([r for r in rows if r["entry"]["group"] == "scan"], lambda e: e["scan"]["level"])))
        print(fmt_table("scans by format", summarize([r for r in rows if r["entry"]["group"] == "scan"], lambda e: e["format"])))
    if a.errors:
        print("\n== errors")
        print_errors(rows, a.errors)


if __name__ == "__main__":
    main()
