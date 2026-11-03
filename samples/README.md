# Sample files

Synthetic files for trying the system and for the demo. Every company, address and number is invented. They were drawn by `evaluation/synth` and copied here by `scripts/make_samples.py`.

They come from the development split, which the extractor was tuned on. They show the system working as intended and say nothing about accuracy on unseen files; see `EVALUATION.md` for that.

Load them all with `docintel seed samples`, or drag a folder into the Upload page. To try the Compare page, load things in this order, because Compare checks every invoice against every spreadsheet row in the system and the other ledgers would add unrelated rows:

```bash
docintel seed samples/invoices       # the six invoices
docintel seed samples/cross-check    # the register
# open Compare in the web app and run it, then load everything else:
docintel seed samples                # files already stored are skipped
```

In `duplicates/`, upload `1-original.pdf` first, then the others (`docintel seed` sorts by name, so that happens by itself). A full seed ends with 'rejected 2' (the empty file and the renamed random bytes), one identical file skipped, and three documents in the failed list (the password protected PDF and the two truncated files). That is the expected result.

| File | What it shows | What you should see |
|---|---|---|
| `invoices/layout-1-INV-2025-07745.pdf` | A digital PDF in layout 1 of 6. Text layer, no OCR needed. | Invoice INV-2025-07745, total 40875.54. Nothing flagged. |
| `invoices/layout-2-BD202508018.pdf` | A digital PDF in layout 2 of 6. Text layer, no OCR needed. | Invoice BD202508018, total 290.58 GBP. Nothing flagged. |
| `invoices/layout-3-SI-25-365.pdf` | A digital PDF in layout 3 of 6. Text layer, no OCR needed. | Invoice SI-25-365, total 10168.7. Nothing flagged. |
| `invoices/layout-4-SI-25-287.pdf` | A digital PDF in layout 4 of 6. Text layer, no OCR needed. | Invoice SI-25-287, total 8353.32 EUR. Nothing flagged. |
| `invoices/layout-5-INV-25-7550.pdf` | A digital PDF in layout 5 of 6. Text layer, no OCR needed. | Invoice INV/25/7550, total 1685.42 BDT. Nothing flagged. |
| `invoices/layout-6-A-70962.pdf` | A digital PDF in layout 6 of 6. Text layer, no OCR needed. | Invoice A-70962, total 1462.65 USD. Nothing flagged. |
| `scans/scan-pdf-BD202556968.pdf` | A PDF of a scanned invoice (light degradation). Read with OCR. | Invoice BD202556968. Values are read from pixels, so check the confidence. |
| `scans/scan-jpg-INV0065557.jpg` | A JPG of a scanned invoice (light degradation). Read with OCR. | Invoice INV0065557. Values are read from pixels, so check the confidence. |
| `scans/scan-png-INV-2025-21343.png` | A PNG of a scanned invoice (light degradation). Read with OCR. | Invoice INV-2025-21343. Values are read from pixels, so check the confidence. |
| `scans/scan-tiff-INV-25-1274.tif` | A TIFF of a scanned invoice (light degradation). Read with OCR. | Invoice INV/25/1274. Values are read from pixels, so check the confidence. |
| `scans/scan-poor-quality-INV-2026-84450.png` | A poor quality scan (blur, noise, low resolution). | Some values are wrong or missing and the record is flagged for review. This is the case a person has to check against the page image. |
| `spreadsheets/invoice-sheet.xlsx` | One invoice laid out like a printed one, in Excel. | One record with line items. Nothing flagged. |
| `spreadsheets/invoice-sheet-with-formulas.xlsx` | The same kind of sheet where the amounts are formulas. The saved results are read. | One record; line amounts and totals match the formulas. Nothing flagged. |
| `spreadsheets/invoice-sheet-legacy.xls` | The old binary .xls format. | One record with line items. Nothing flagged. |
| `spreadsheets/ledger.xlsx` | A register with one invoice per row. | One record per row (a ledger has no line items). Nothing flagged. |
| `spreadsheets/ledger.csv` | The same idea as CSV. The delimiter and encoding are detected. | One record per row. Nothing flagged. |
| `spreadsheets/line-items-grouped.xlsx` | One row per line item with an invoice number column, and no total column. | Rows are grouped into invoices, each with its line items. The sheet shows no total, so every invoice is flagged MISSING_FIELD (total): the system does not add up a total that the document does not state. |
| `spreadsheets/multi-sheet-workbook.xlsx` | A workbook with several sheets. | Records from every sheet. Nothing flagged. |
| `multi-invoice/batch-of-4-invoices.pdf` | One PDF with several invoices stacked together, as a supplier statement or a scanned batch would be. | 4 separate records, each with its own invoice number. |
| `review-demo/total-does-not-add-up.pdf` | An invoice with a deliberate error (total wrong). | TOTAL_MISMATCH: the printed total differs from subtotal plus tax. |
| `review-demo/line-amount-wrong.pdf` | An invoice with a deliberate error (line amount wrong). | LINE_ITEM_MISMATCH and SUBTOTAL_MISMATCH: quantity times price differs from a line amount. |
| `review-demo/subtotal-wrong.pdf` | An invoice with a deliberate error (subtotal wrong). | SUBTOTAL_MISMATCH (the lines do not add up to the printed subtotal) and TOTAL_MISMATCH (that subtotal plus tax no longer gives the total). |
| `review-demo/missing-invoice-number.pdf` | An invoice with a deliberate error (missing number). | MISSING_FIELD: no invoice number. The field stays empty, nothing is invented. |
| `duplicates/1-original.pdf` | The invoice everything else here is a copy of. Upload this first. | Processed normally. |
| `duplicates/2-identical-file.pdf` | Byte for byte the same file. | Refused at upload as an identical file, nothing is queued. |
| `duplicates/3-same-invoice-other-layout.pdf` | The same invoice (same supplier, same number) in a different layout. | DUPLICATE_INVOICE on the later record. |
| `duplicates/4-same-supplier-date-total-new-number.pdf` | Same supplier, date and total, but a different invoice number. | POSSIBLE_DUPLICATE (a warning, a person decides). |
| `cross-check/register.xlsx` | A register of invoices to compare the six PDFs in invoices/ against. | Compare finds: 4 matches, 1 mismatch (invoice BD202508018, total off by 5.00), 1 invoice missing from the register (INV/25/7550), 1 register row with no invoice (REG-9999). The register itself shows TOTAL_MISMATCH on three rows: the changed one, whose subtotal plus tax no longer gives its total, and the rows for the two invoices that include shipping (SI-25-365 and A-70962), because the register has no shipping column. |
| `failures/truncated.pdf` | A PDF cut off in the middle. | Accepted, then fails with CORRUPT_FILE and a plain message. No retry, because retrying cannot help. |
| `failures/password-protected.pdf` | A PDF that needs a password. | Fails with PASSWORD_PROTECTED. |
| `failures/not-really-a-pdf.pdf` | Random bytes with a .pdf name. | Refused at upload (UNSUPPORTED_TYPE). The content decides, not the name. |
| `failures/empty-file.pdf` | A file with nothing in it. | Refused at upload (EMPTY_FILE). |
| `failures/letter-without-invoice.pdf` | A normal letter, no invoice in it. | Completes with NO_INVOICE_DETECTED so a person sees it was not skipped. |
| `failures/blank-page.jpg` | A white page. | Completes with NO_INVOICE_DETECTED. |
| `failures/truncated-workbook.xlsx` | An Excel file cut in half. | Fails with CORRUPT_FILE. |
