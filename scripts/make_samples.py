"""Build the demo files in samples/ from the generated development dataset.

    python -m evaluation.build --split dev        # once, if evaluation/dataset/dev does not exist
    python scripts/make_samples.py

Everything in samples/ is synthetic: invented companies, addresses and numbers, drawn by evaluation/synth.
The files come from the development split, which the extractor was tuned on, so they show the system working
as intended. They must not be used to judge its accuracy; EVALUATION.md does that on files it never saw.
"""

from __future__ import annotations

import json
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import openpyxl  # noqa: E402

from docintel.config import Settings  # noqa: E402
from docintel.pipeline.process import process_file  # noqa: E402
from evaluation.extract_run import record_to_dict  # noqa: E402
from evaluation.metrics import score_record  # noqa: E402

DATA = ROOT / "evaluation" / "dataset" / "dev"
OUT = ROOT / "samples"
SETTINGS = Settings(extraction_mode="rules", llm_provider="none", ocr_processes=1, _env_file=None)

manifest = json.loads((DATA / "manifest.json").read_text(encoding="utf-8"))
ENTRIES = manifest["entries"]
BY_ID = {e["id"]: e for e in ENTRIES if e["group"] != "corrupt"}
log: list[tuple[str, str, str]] = []  # (folder/file, what it shows, what to expect)


def src(entry: dict) -> Path:
    return DATA / entry["file"]


def put(folder: str, name: str, source: Path, shows: str, expect: str) -> None:
    dest = OUT / folder / name
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, dest)
    log.append((f"{folder}/{name}", shows, expect))


def safe(text: str | None) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", text or "unnumbered").strip("-")


def good(entry: dict) -> bool:
    """Does the pipeline read this file exactly right and raise nothing that needs a person?"""
    res = process_file(str(src(entry)), SETTINGS)
    recs = [record_to_dict(r) for r in res.records]
    if len(recs) != len(entry["records"]):
        return False
    from evaluation.metrics import match_records

    scored = [score_record(p, t) for p, t in match_records(recs, entry["records"]) if t is not None and p is not None]
    flagged = any(any(i.severity in ("error", "warning") for i in lst) for lst in res.issues)
    return len(scored) == len(entry["records"]) and all(s["strict"] for s in scored) and not flagged


def first(group: str, **match) -> dict | None:
    for e in ENTRIES:
        if e["group"] == group and all(e.get(k) == v for k, v in match.items()):
            return e
    return None


def main() -> None:
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir()

    # 1. one clean digital invoice per layout
    chosen: list[dict] = []
    for tpl in ("T1", "T2", "T3", "T4", "T5", "T6"):
        for e in ENTRIES:
            if e["group"] == "digital_pdf" and e.get("template") == tpl and good(e):
                chosen.append(e)
                t = e["records"][0]
                put("invoices", f"layout-{tpl[1]}-{safe(t['invoice_number'])}.pdf", src(e), f"A digital PDF in layout {tpl[1]} of 6. Text layer, no OCR needed.",
                    f"Invoice {t['invoice_number']}, total {t['total']} {t.get('currency') or ''}".strip() + ". Nothing flagged.")
                break

    # 2. scans: one light scan per format, plus one that is hard enough to be flagged
    for fmt in ("pdf", "jpg", "png", "tiff"):
        for e in ENTRIES:
            if e["group"] == "scan" and e["format"] == fmt and e["scan"]["level"] == "light" and good(e):
                t = e["records"][0]
                put("scans", f"scan-{fmt}-{safe(t['invoice_number'])}.{'tif' if fmt == 'tiff' else fmt}", src(e), f"A {fmt.upper()} of a scanned invoice (light degradation). Read with OCR.", f"Invoice {t['invoice_number']}. Values are read from pixels, so check the confidence.")
                break
    for e in ENTRIES:
        if e["group"] == "scan" and e["scan"]["level"] == "medium" and not good(e):
            t = e["records"][0]
            put("scans", f"scan-poor-quality-{safe(t['invoice_number'])}.{'tif' if e['format'] == 'tiff' else e['format']}", src(e), "A poor quality scan (blur, noise, low resolution).", "Some values are wrong or missing and the record is flagged for review. This is the case a person has to check against the page image.")
            break

    # 3. spreadsheets (files that the system reads right and does not flag, except the grouped one, which shows a missing total on purpose)
    for eid, name, shows, expect in [
        ("dev-sheet-xlsx-087", "invoice-sheet.xlsx", "One invoice laid out like a printed one, in Excel.", "One record with line items. Nothing flagged."),
        ("dev-sheet-xlsx_formulas-089", "invoice-sheet-with-formulas.xlsx", "The same kind of sheet where the amounts are formulas. The saved results are read.", "One record; line amounts and totals match the formulas. Nothing flagged."),
        ("dev-sheet-xls-092", "invoice-sheet-legacy.xls", "The old binary .xls format.", "One record with line items. Nothing flagged."),
        ("dev-ledger-xlsx-094", "ledger.xlsx", "A register with one invoice per row.", "One record per row (a ledger has no line items). Nothing flagged."),
        ("dev-ledger-csv-097", "ledger.csv", "The same idea as CSV. The delimiter and encoding are detected.", "One record per row. Nothing flagged."),
        ("dev-grouped-101", "line-items-grouped.xlsx", "One row per line item with an invoice number column, and no total column.", "Rows are grouped into invoices, each with its line items. The sheet shows no total, so every invoice is flagged MISSING_FIELD (total): the system does not add up a total that the document does not state."),
        ("dev-multisheet-102", "multi-sheet-workbook.xlsx", "A workbook with several sheets.", "Records from every sheet. Nothing flagged."),
    ]:
        put("spreadsheets", name, src(BY_ID[eid]), shows, expect)

    # 4. a PDF that holds several invoices
    multi = BY_ID["dev-multi-104"]
    put("multi-invoice", f"batch-of-{len(multi['records'])}-invoices.pdf", src(multi), "One PDF with several invoices stacked together, as a supplier statement or a scanned batch would be.", f"{len(multi['records'])} separate records, each with its own invoice number.")

    # 5. invoices with a mistake in them, for the review queue
    for fault, name, expect in [
        ("total_wrong", "total-does-not-add-up.pdf", "TOTAL_MISMATCH: the printed total differs from subtotal plus tax."),
        ("line_amount_wrong", "line-amount-wrong.pdf", "LINE_ITEM_MISMATCH and SUBTOTAL_MISMATCH: quantity times price differs from a line amount."),
        ("subtotal_wrong", "subtotal-wrong.pdf", "SUBTOTAL_MISMATCH (the lines do not add up to the printed subtotal) and TOTAL_MISMATCH (that subtotal plus tax no longer gives the total)."),
        ("missing_number", "missing-invoice-number.pdf", "MISSING_FIELD: no invoice number. The field stays empty, nothing is invented."),
    ]:
        e = next(x for x in ENTRIES if x["group"] == "fault" and x["fault"] == fault and x.get("realized", True) and x["template"] in ("T1", "T2", "T4", "T5"))
        put("review-demo", name, src(e), f"An invoice with a deliberate error ({fault.replace('_', ' ')}).", expect)

    # 6. duplicates (upload the original first)
    for eid, name, shows, expect in [
        ("dev-dup00-orig", "1-original.pdf", "The invoice everything else here is a copy of. Upload this first.", "Processed normally."),
        ("dev-dup00-exact", "2-identical-file.pdf", "Byte for byte the same file.", "Refused at upload as an identical file, nothing is queued."),
        ("dev-dup00-reissue", "3-same-invoice-other-layout.pdf", "The same invoice (same supplier, same number) in a different layout.", "DUPLICATE_INVOICE on the later record."),
        ("dev-dup00-near", "4-same-supplier-date-total-new-number.pdf", "Same supplier, date and total, but a different invoice number.", "POSSIBLE_DUPLICATE (a warning, a person decides)."),
    ]:
        put("duplicates", name, src(next(x for x in ENTRIES if x["id"] == eid)), shows, expect)

    # 7. a register for cross verification
    rows = [["Invoice No", "Invoice Date", "Supplier", "Customer", "Currency", "Subtotal", "Tax", "Total"]]
    truth = [e["records"][0] for e in chosen]
    for k, t in enumerate(truth):
        if k == 4:
            continue  # left out on purpose: this invoice has no partner in the register
        total = t["total"] + (5.0 if k == 1 else 0.0)  # changed on purpose
        rows.append([t["invoice_number"], t["invoice_date"], t["supplier_name"], t["customer_name"], t.get("currency"), t["subtotal"], t["tax"], total])
    rows.append(["REG-9999", "2026-03-15", "Registry Only Trading Ltd.", "Someone Else", "USD", 100.0, 10.0, 110.0])  # in the register only
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Register"
    for r in rows:
        ws.append(r)
    dest = OUT / "cross-check" / "register.xlsx"
    dest.parent.mkdir(parents=True, exist_ok=True)
    wb.save(dest)
    log.append(("cross-check/register.xlsx", "A register of invoices to compare the six PDFs in invoices/ against.",
                f"Compare finds: {len(truth) - 2} matches, 1 mismatch (invoice {truth[1]['invoice_number']}, total off by 5.00), 1 invoice missing from the register ({truth[4]['invoice_number']}), 1 register row with no invoice (REG-9999). The register itself also shows TOTAL_MISMATCH on the changed row, because its subtotal plus tax no longer gives that total."))

    # 8. files that should fail, and how
    corrupt = {e["file"].split("/")[-1]: e for e in ENTRIES if e["group"] == "corrupt"}
    for fname, name, shows, expect in [
        ("truncated.pdf", "truncated.pdf", "A PDF cut off in the middle.", "Accepted, then fails with CORRUPT_FILE and a plain message. No retry, because retrying cannot help."),
        ("locked.pdf", "password-protected.pdf", "A PDF that needs a password.", "Fails with PASSWORD_PROTECTED."),
        ("random_bytes.pdf", "not-really-a-pdf.pdf", "Random bytes with a .pdf name.", "Refused at upload (UNSUPPORTED_TYPE). The content decides, not the name."),
        ("zero_bytes.pdf", "empty-file.pdf", "A file with nothing in it.", "Refused at upload (EMPTY_FILE)."),
        ("letter.pdf", "letter-without-invoice.pdf", "A normal letter, no invoice in it.", "Completes with NO_INVOICE_DETECTED so a person sees it was not skipped."),
        ("blank_page.jpg", "blank-page.jpg", "A white page.", "Completes with NO_INVOICE_DETECTED."),
        ("truncated.xlsx", "truncated-workbook.xlsx", "An Excel file cut in half.", "Fails with CORRUPT_FILE."),
    ]:
        put("failures", name, DATA / corrupt[fname]["file"], shows, expect)

    lines = [
        "# Sample files",
        "",
        "Synthetic files for trying the system and for the demo. Every company, address and number is invented. They were drawn by `evaluation/synth` and copied here by `scripts/make_samples.py`.",
        "",
        "They come from the development split, which the extractor was tuned on. They show the system working as intended and say nothing about accuracy on unseen files; see `EVALUATION.md` for that.",
        "",
        "Load them all with `docintel seed samples`, or drag a folder into the Upload page. To try the Compare page, load things in this order, because Compare checks every invoice against every spreadsheet row in the system and the other ledgers would add unrelated rows:",
        "",
        "```bash",
        "docintel seed samples/invoices       # the six invoices",
        "docintel seed samples/cross-check    # the register",
        "# open Compare in the web app and run it, then load everything else:",
        "docintel seed samples                # files already stored are skipped",
        "```",
        "",
        "In `duplicates/`, upload `1-original.pdf` first, then the others (`docintel seed` sorts by name, so that happens by itself). A full seed ends with \'rejected 2\' (the empty file and the renamed random bytes), one identical file skipped, and three documents in the failed list (the password protected PDF and the two truncated files). That is the expected result.",
        "",
        "| File | What it shows | What you should see |",
        "|---|---|---|",
    ]
    for f, shows, expect in log:
        lines.append(f"| `{f}` | {shows} | {expect} |")
    (OUT / "README.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    total = sum(p.stat().st_size for p in OUT.rglob("*") if p.is_file())
    print(f"{len(log)} files, {total / 1e6:.1f} MB in {OUT}")


if __name__ == "__main__":
    main()
