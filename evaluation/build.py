"""Build the labeled synthetic test set.

    python -m evaluation.build --split dev --out evaluation/dataset
    python -m evaluation.build --split holdout --out evaluation/dataset
    python -m evaluation.build --perf --out evaluation/dataset      # the 1000 page files

Everything is generated from a seed, so the same command always gives the same files. The ground
truth is stored in manifest.json next to the files. All of it is synthetic and written by the
same author as the extractor; the evaluation report says so.
"""

from __future__ import annotations

import argparse
import copy
import json
import random
import shutil
import time
from datetime import date
from pathlib import Path

from .synth import degrade as dg
from .synth import faults as fl
from .synth import sheets as sh
from .synth.corrupt import build_corrupt
from .synth.data import Invoice, make_invoice, truth_record
from .synth.templates import DEV_TEMPLATES, cur_printed, paginate, render_pdf

SPLITS = {
    "dev": {"seed": 1001, "per_template": 10, "scans_per_template": 4},
    "holdout": {"seed": 2002, "per_template": 10, "scans_per_template": 4},
}
LEVELS = ["light", "medium", "heavy"]
SCAN_FORMATS = ["pdf", "jpg", "png", "tiff"]


def registry_for(split: str) -> dict:
    if split == "holdout":
        from .synth.holdout import HOLDOUT_TEMPLATES  # written after the extractor was frozen

        return HOLDOUT_TEMPLATES
    return DEV_TEMPLATES


def random_invoice(rng: random.Random, *, plain: bool = False, **kw) -> Invoice:
    """`plain` means no discount and no shipping, for ledgers that only have subtotal, tax and total columns."""
    n = kw.pop("n_items", None) or rng.choice([1, 2, 3, 3, 4, 5, 5, 6, 7, 8, 9, 11, 14, 20])
    disc, ship = rng.random() < 0.15, rng.random() < 0.15
    return make_invoice(rng, n_items=n, with_discount=disc and not plain, with_shipping=ship and not plain, **kw)


class Builder:
    def __init__(self, split: str, out: Path):
        self.split = split
        self.cfg = SPLITS.get(split, SPLITS["dev"])
        self.rng = random.Random(self.cfg["seed"])
        self.registry = registry_for(split)
        self.templates = list(self.registry)
        self.root = out / split
        self.files = self.root / "files"
        if self.root.exists():
            shutil.rmtree(self.root)
        self.files.mkdir(parents=True)
        self.entries: list[dict] = []
        self.crosscheck: dict = {}
        self.counter = 0

    # -- helpers

    def nid(self, prefix: str) -> str:
        self.counter += 1
        return f"{self.split}-{prefix}-{self.counter:03d}"

    def entry(self, id_: str, name: str, group: str, records: list[dict], **meta) -> dict:
        e = {"id": id_, "file": f"files/{name}", "group": group, "records": records, **meta}
        self.entries.append(e)
        return e

    def render(self, inv_list: list[Invoice], tpl: str, path: Path) -> list[dict]:
        infos = render_pdf(str(path), inv_list, tpl, self.rng, self.registry)
        self.last_infos = infos
        return [truth_record(inv, info["prints"], currency_printed=cur_printed(info["look"])) for inv, info in zip(inv_list, infos)]

    # -- groups

    def digital(self) -> None:
        for tpl in self.templates:
            for _ in range(self.cfg["per_template"]):
                inv = random_invoice(self.rng)
                i = self.nid(f"pdf-{tpl}")
                path = self.files / f"{i}.pdf"
                self.entry(i, path.name, "digital_pdf", self.render([inv], tpl, path), template=tpl, format="pdf")

    def scans(self) -> None:
        k = 0
        for tpl in self.templates:
            for _ in range(self.cfg["scans_per_template"]):
                inv = random_invoice(self.rng, n_items=self.rng.choice([2, 3, 4, 5, 6, 8]))
                level = LEVELS[k % len(LEVELS)]
                fmt = SCAN_FORMATS[(k // 2) % len(SCAN_FORMATS)]
                k += 1
                tmp = self.root / "_tmp.pdf"
                truth = self.render([inv], tpl, tmp)
                dpi = dg.scan_dpi(self.rng, level)
                pages = [dg.degrade(im, self.rng, level) for im in dg.rasterize(tmp, dpi)]
                tmp.unlink()
                if len(pages) > 1 and fmt in ("jpg", "png"):
                    fmt = "pdf"
                i = self.nid(f"scan-{tpl}")
                name = f"{i}.{fmt}"
                path = self.files / name
                if fmt == "pdf":
                    dg.save_image_pdf(pages, path, dpi)
                elif fmt == "tiff":
                    dg.save_tiff(pages, path, dpi)
                elif fmt == "jpg":
                    pages[0].save(path, "JPEG", quality=self.rng.randint(55, 85), dpi=(dpi, dpi))
                else:
                    pages[0].save(path, "PNG", dpi=(dpi, dpi))
                self.entry(i, name, "scan", truth, template=tpl, format=fmt, scan={"level": level, "dpi": round(dpi), "pages": len(pages)})

    def spreadsheets(self) -> None:
        rng = self.rng
        # one invoice per sheet: plain xlsx, formulas recalculated, legacy xls
        for variant, count in (("xlsx", 4), ("xlsx_formulas", 2), ("xls", 3)):
            for _ in range(count):
                inv = random_invoice(rng, n_items=rng.choice([2, 3, 4, 5, 6, 8]))
                ext = "xls" if variant == "xls" else "xlsx"
                i = self.nid(f"sheet-{variant}")
                path = self.files / f"{i}.{ext}"
                info = sh.write_invoice_xlsx(inv, path, rng, formulas=variant == "xlsx_formulas", legacy_xls=variant == "xls", label_set=1 if self.split == "holdout" else 0)
                self.entry(i, path.name, "spreadsheet", [truth_record(inv, info["prints"], currency_printed=info["currency_printed"])], format=ext, layout="invoice_sheet", variant=variant, formulas_used=info["formulas_used"])
        # ledgers: one record per row
        specs = [
            ("xlsx", dict(header_variant=0, date_style="dmy_mon", include_currency=True)),
            ("xlsx", dict(header_variant=1, date_style="iso", include_currency=False, title_rows=2)),
            ("xls", dict(header_variant=2, date_style="dmy_slash", include_currency=True)),
            ("csv", dict(header_variant=0, date_style="iso", include_currency=True)),
            ("csv", dict(header_variant=1, date_style="dmy_slash", include_currency=True, as_text_numbers=True, num_style="eu", csv_delim=";")),
            ("csv", dict(header_variant=2, date_style="dmy_mon", include_currency=False, csv_encoding="utf-16")),
        ]
        for fmt, kw in specs:
            invs = [random_invoice(rng, n_items=3, plain=True) for _ in range(rng.randint(8, 14))]
            if kw.get("as_text_numbers"):
                for inv in invs:
                    inv.currency = "EUR"
            i = self.nid(f"ledger-{fmt}")
            path = self.files / f"{i}.{fmt}"
            kw = copy.deepcopy(kw)
            if self.split == "holdout":
                kw["header_variant"] += 3  # other column names than the development ledgers
            truths = sh.write_ledger(invs, path, rng, fmt, **kw)
            self.entry(i, path.name, "spreadsheet", truths, format=fmt, layout="ledger")
        # grouped line items
        for with_total in (True, False):
            invs = [random_invoice(rng, n_items=rng.randint(2, 5)) for _ in range(rng.randint(4, 6))]
            i = self.nid("grouped")
            path = self.files / f"{i}.xlsx"
            self.entry(i, path.name, "spreadsheet", sh.write_grouped(invs, path, rng, with_invoice_total=with_total), format="xlsx", layout="grouped")
        # workbook with several sheets, one hidden
        i = self.nid("multisheet")
        path = self.files / f"{i}.xlsx"
        invs = [random_invoice(rng, n_items=3, plain=True) for _ in range(5)]
        inv_a = random_invoice(rng, n_items=4)
        ledger_truth, inv_info = sh.write_multisheet(inv_a, invs, path, rng)
        invoice_truth = truth_record(inv_a, inv_info[0]["prints"], currency_printed=inv_info[0]["currency_printed"])
        self.entry(i, path.name, "spreadsheet", ledger_truth + [invoice_truth], format="xlsx", layout="multisheet",
                   note="Summary sheet ledger (5 rows), Invoice sheet (1 invoice), Notes sheet and a hidden sheet that must be ignored")

    def multi_invoice(self) -> None:
        for n_inv, tpl in ((3, self.templates[0]), (4, self.templates[1 % len(self.templates)]), (3, self.templates[2 % len(self.templates)]), (5, self.templates[-1])):
            invs = [random_invoice(self.rng, n_items=self.rng.choice([2, 3, 5, 8])) for _ in range(n_inv)]
            i = self.nid("multi")
            path = self.files / f"{i}.pdf"
            self.entry(i, path.name, "multi_invoice", self.render(invs, tpl, path), template=tpl, format="pdf")

    @staticmethod
    def fault_realized(fault: str, info: dict) -> bool:
        """Did the layout really print what the fault needs? Some layouts have no due date, and some fix their own date format."""
        if fault == "due_before_date":
            return "due" in info["prints"]
        if fault == "ambiguous_date":
            return info["look"].date_style in ("dmy_slash", "dmy_dot")
        return True

    def fault_docs(self, per_fault: int = 4) -> None:
        full = [t for t in self.templates if t != "T3"] or self.templates
        for fault in fl.FAULTS:
            pool = self.templates if fault in fl.HEADER_ONLY else full
            for k in range(per_fault):
                tpl = pool[(k + len(fault)) % len(pool)]
                inv = fl.faulty_invoice(self.rng, fault, n_items=self.rng.choice([2, 3, 4, 5, 6, 8]))
                i = self.nid(f"fault-{fault}")
                path = self.files / f"{i}.pdf"
                truth = self.render([inv], tpl, path)
                realized = self.fault_realized(fault, self.last_infos[0])
                self.entry(i, path.name, "fault", truth, template=tpl, format="pdf", fault=fault, expect_codes=sorted(inv.expect_codes), realized=realized)

    def duplicates(self, n: int = 10) -> None:
        full = [t for t in self.templates if t != "T3"] or self.templates
        for k in range(n):
            tpl = full[k % len(full)]
            other = full[(k + 1) % len(full)]
            inv = random_invoice(self.rng, n_items=self.rng.choice([3, 4, 5, 6]))
            base = f"dup{k:02d}"
            path = self.files / f"{self.split}-{base}-orig.pdf"
            oid = f"{self.split}-{base}-orig"
            self.entry(oid, path.name, "duplicate", self.render([inv], tpl, path), template=tpl, format="pdf", dup_role="original", step=0)
            # exact copy of the same file under another name
            cid = f"{self.split}-{base}-exact"
            cpath = self.files / f"{cid}.pdf"
            shutil.copyfile(path, cpath)
            self.entry(cid, cpath.name, "duplicate", [], format="pdf", dup_role="exact_file", dup_of=oid, step=1, expect="duplicate_file")
            # the same invoice drawn by another template (what a re-sent copy looks like)
            rid = f"{self.split}-{base}-reissue"
            rpath = self.files / f"{rid}.pdf"
            clone = copy.deepcopy(inv)
            self.entry(rid, rpath.name, "duplicate", self.render([clone], other, rpath), template=other, format="pdf", dup_role="reissue", dup_of=oid, step=1, expect_codes=["DUPLICATE_INVOICE"])
            # same supplier, date and total but a different invoice number: a probable duplicate
            nid_ = f"{self.split}-{base}-near"
            npath = self.files / f"{nid_}.pdf"
            near = copy.deepcopy(inv)
            num = near.number
            near.p_number = num[:-1] + str((int(num[-1]) + 3) % 10) if num[-1].isdigit() else num + "A"
            self.entry(nid_, npath.name, "duplicate", self.render([near], tpl, npath), template=tpl, format="pdf", dup_role="near", dup_of=oid, step=1, expect_codes=["POSSIBLE_DUPLICATE"])
        # same invoice number from two different suppliers is normal and must not be flagged
        for k in range(6):
            tpl = full[k % len(full)]
            a = random_invoice(self.rng, n_items=3)
            b = random_invoice(self.rng, n_items=3)
            b.number = b.p_number = a.number
            base = f"neg{k:02d}"
            for role, inv in (("a", a), ("b", b)):
                i = f"{self.split}-{base}-{role}"
                path = self.files / f"{i}.pdf"
                self.entry(i, path.name, "duplicate", self.render([inv], tpl, path), template=tpl, format="pdf", dup_role="negative", dup_of=None, step=0 if role == "a" else 1, expect_codes=[], expect_not=["DUPLICATE_INVOICE", "POSSIBLE_DUPLICATE"])

    def cross_check(self) -> None:
        rng = self.rng
        full = [t for t in self.templates if t != "T3"] or self.templates
        invs = [random_invoice(rng, n_items=rng.choice([2, 3, 4, 5]), plain=True) for _ in range(10)]
        for inv in invs:
            inv.currency = inv.currency
        pdfs: list[dict] = []
        for k, inv in enumerate(invs):
            i = f"{self.split}-xc-pdf-{k:02d}"
            path = self.files / f"{i}.pdf"
            truth = self.render([inv], full[k % len(full)], path)
            pdfs.append({"id": i, "file": f"files/{i}.pdf", "records": truth})
        # ledger: first 8 PDFs are in it (2 with a different total), plus 4 rows with no PDF
        ledger_invs = [copy.deepcopy(v) for v in invs[:8]]
        expected: dict[str, str] = {}
        for k, v in enumerate(ledger_invs):
            expected[v.p_number] = "MATCH"
        for k in (2, 5):
            ledger_invs[k].p_total = round(ledger_invs[k].p_total + rng.choice([50.0, 120.0, 12.5]), 2)
            expected[ledger_invs[k].p_number] = "MISMATCH"
        extra = [random_invoice(rng, n_items=3, plain=True) for _ in range(4)]
        for v in extra:
            expected[v.p_number] = "MISSING_LEFT"
        for v in invs[8:]:
            expected[v.p_number] = "MISSING_RIGHT"
        lid = f"{self.split}-xc-ledger"
        lpath = self.files / f"{lid}.xlsx"
        for v in ledger_invs + extra:
            v.currency = v.currency
        sh.write_ledger(ledger_invs + extra, lpath, rng, "xlsx", header_variant=0, date_style="dmy_mon", include_currency=True)
        self.crosscheck = {"ledger": f"files/{lid}.xlsx", "pdfs": pdfs, "expected": expected}

    def corrupt(self) -> None:
        for e in build_corrupt(self.files, self.rng):
            e["id"] = f"{self.split}-{e['id']}"
            self.entries.append(e)

    def finish(self) -> Path:
        manifest = {
            "split": self.split, "seed": self.cfg["seed"], "built": date.today().isoformat(),
            "note": "Synthetic documents generated by evaluation/synth. Not real invoices.",
            "entries": self.entries, "crosscheck": self.crosscheck,
        }
        (self.root / "manifest.json").write_text(json.dumps(manifest, indent=1, default=str), encoding="utf-8")
        return self.root / "manifest.json"


def build_split(split: str, out: Path) -> Path:
    b = Builder(split, out)
    t0 = time.time()
    b.digital()
    b.scans()
    b.spreadsheets()
    b.multi_invoice()
    b.fault_docs()
    b.duplicates()
    b.cross_check()
    b.corrupt()
    path = b.finish()
    counts: dict[str, int] = {}
    for e in b.entries:
        counts[e["group"]] = counts.get(e["group"], 0) + 1
    print(f"{split}: {len(b.entries)} files in {time.time() - t0:.0f}s  {counts}")
    return path


def build_perf(out: Path, pages_target: int = 1000, seed: int = 3003) -> None:
    """One text PDF of exactly `pages_target` pages made of many invoices, with the ground truth."""
    rng = random.Random(seed)
    root = out / "perf"
    root.mkdir(parents=True, exist_ok=True)
    invoices: list[Invoice] = []
    pages = 0
    while pages < pages_target:
        left = pages_target - pages
        n_items = rng.choice([8, 30, 30, 55, 58, 60]) if left > 3 else 3
        counts = paginate(n_items, 24, 36)
        if len(counts) > left:
            n_items = 3
            counts = paginate(n_items, 24, 36)
        inv = random_invoice(rng, n_items=n_items)
        invoices.append(inv)
        pages += len(counts)
    path = root / f"text_{pages}p.pdf"
    infos = render_pdf(str(path), invoices, "T1", rng)
    truths = [truth_record(inv, info["prints"], currency_printed=cur_printed(info["look"])) for inv, info in zip(invoices, infos)]
    page_total = sum(i["pages"] for i in infos)
    (root / "text_truth.json").write_text(json.dumps({"file": path.name, "pages": page_total, "invoices": len(truths), "records": truths}, default=str), encoding="utf-8")
    print(f"perf text pdf: {page_total} pages, {len(truths)} invoices, {path.stat().st_size / 1e6:.1f} MB")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", choices=["dev", "holdout"])
    ap.add_argument("--perf", action="store_true")
    ap.add_argument("--out", default="evaluation/dataset")
    a = ap.parse_args()
    out = Path(a.out)
    if a.split:
        build_split(a.split, out)
    if a.perf:
        build_perf(out)


if __name__ == "__main__":
    main()
