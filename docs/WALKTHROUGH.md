# Demo walkthrough

A click by click tour for the recorded video. It takes about ten minutes at a calm pace, and it only uses files in `samples/`. Every file there is synthetic, and `samples/README.md` says what each one is for and what you should see.

## Before you start

Use a fresh data folder, so the first screen is empty and nothing from earlier runs shows up:

```bash
source .venv/bin/activate              # Windows PowerShell: .venv\Scripts\Activate.ps1
docintel --data-dir ./demo-data doctor
docintel --data-dir ./demo-data serve  # then open http://127.0.0.1:8000
```

On Windows use the commands in the Windows paragraph of the README (`py -m venv .venv`, then `python -m docintel ...` in place of `docintel ...`). Open the app in Edge or Chrome, not in the browser built into VS Code.

The `doctor` line is worth showing on screen once: it prints the Python version, which OCR engine was found, and whether the database and the data folder are writable.

## 1. What it is (1 minute)

Show the top of `README.md` and the diagram in `docs/img/architecture.png`. Three sentences are enough: one service holds the REST API, the web app and the background workers; the queue, the full text index and the vectors all live in one SQLite database; a worker takes a file, reads it, extracts, validates, saves and indexes it.

## 2. Upload many files at once (1.5 minutes)

Open **Upload**. Drag in the folder `samples/invoices` (six PDFs in six layouts) together with `samples/cross-check/register.xlsx`. Do not add the other spreadsheets yet, because step 4 depends on that.

Point out that each file gets its own row and moves through its own stages (waiting, reading pages, finding invoice fields, checking, done), and that one file failing never stops the rest. Open **Documents** to see the list with status, page count and review state.

## 3. Look inside a record (1.5 minutes)

Open one of the invoices. The page image is on the left and the extracted values are on the right: invoice number, dates, supplier, customer, subtotal, tax, total, currency, and the line items. Each value has a confidence mark, and under it the line of text it was read from (the evidence), with the page image showing where that line sits. Say that a value that is not on the page stays empty, because the system is not allowed to guess.

## 4. Compare an invoice batch with a spreadsheet (1 minute)

Open **Compare** and press **Compare now**. The six invoices are checked against the rows of the register. You should see 4 matches, 1 mismatch (one total is off by 5.00), 1 invoice that the register does not have, and 1 register row without an invoice. This is the "PDF against Excel" check from the brief.

Do this before loading the other spreadsheets, because Compare looks at every invoice against every spreadsheet row in the system.

## 5. Scans, spreadsheets and several invoices in one PDF (1.5 minutes)

Now drag in `samples/scans`, `samples/spreadsheets` and `samples/multi-invoice`. While they process, say what is happening: pages with a text layer are read directly, scans go to OCR (Tesseract if it is installed, otherwise RapidOCR), spreadsheets are read cell by cell, and a PDF with several invoices is cut into separate records.

Open the poor quality scan (`scan-poor-quality-...png`). The confidence marks are low and the record is waiting for a person. This is the case the review queue exists for.

## 6. The review queue and corrections (2 minutes)

Drag in `samples/review-demo`, the four invoices with a deliberate mistake. Open **To review**. Each one carries a code and a plain sentence: `TOTAL_MISMATCH` (the total is not subtotal plus tax), `LINE_ITEM_MISMATCH`, `SUBTOTAL_MISMATCH`, `MISSING_FIELD` (no invoice number).

Open one, fix the value, and press **Save changes**, then **Check again** to see the rules run on the corrected values, then **Approve**. Mention three things: every edit is validated again, every edit is written to the audit trail, and if somebody else saved the same record in the meantime, the app says so instead of overwriting their change.

## 7. Duplicates (1 minute)

Drag in the four files of `samples/duplicates`. The identical file is turned away at upload ("already stored"). The same invoice in another layout is flagged `DUPLICATE_INVOICE`. The same supplier, date and total under a new invoice number is flagged `POSSIBLE_DUPLICATE`, which is a warning for a person to decide, not an error.

## 8. Files that fail (1 minute)

Drag in `samples/failures`. Two files are refused straight away (an empty file, and random bytes with a `.pdf` name), three fail with a code and a plain message (a truncated PDF, a password protected PDF, a truncated workbook), and a letter and a white page complete with a `NO_INVOICE_DETECTED` warning, so nobody believes they were read and found clean. On the Upload page, **Retry** puts failed files back in the queue.

## 9. Search (1 minute)

Open **Search**. Try a supplier name, then an invoice number, then a word that appears in a line item (such as "thermometer"), and then the same word with a typo. Show the filters (supplier, dates, amount range, review status, file type) and the highlighted snippets, and the switch between keyword, meaning and combined search. Say honestly what the semantic mode is: by default it matches word shapes and typos, and a real embedding model can be switched on with a key.

## 10. Tests and numbers (1.5 minutes)

In a terminal:

```bash
pytest -p no:warnings
```

It ends with a line like `159 passed in 25s`. Do not add `-q` here: the project already sets it, and a second one hides that last line.

Then open `EVALUATION.md` and walk through three things: the table after fixes, the first blind run (97% of fields right but only half of the documents completely right), and the scan table by damage level. Close on the limits: synthetic documents, same author, the AI path not measured, SQLite only.

## 11. The AI disclosure (30 seconds)

Say it plainly: the code was written with Claude as a coding assistant, working from the assessment brief and my instructions, and `AI_Prompts.docx` has the disclosure, the exact prompts the product sends to a model, and what I asked Claude to do.

## If something goes wrong while recording

- **Compare shows many more rows than expected.** The other spreadsheets were loaded before Compare ran. Stop the server, delete `demo-data` and start again from step 1.
- **A scan takes a long time.** A very poor page can use up to 15 seconds of retries. That is the retry budget working, not a hang.
- **Port 8000 is taken.** Use `docintel serve --port 8010`.
