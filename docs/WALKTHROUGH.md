# Demo walkthrough

A click by click tour for the recorded video. It takes about eleven minutes at a calm pace, and it only uses files in `samples/`. Every file there is synthetic, and `samples/README.md` says what each one is for and what you should see. The words to say, with timings, are in the video script that goes with this tour.

## Before you start

Use a fresh data folder, so the first screen is empty and nothing from earlier runs shows up:

```bash
source .venv/bin/activate              # Windows PowerShell: .venv\Scripts\Activate.ps1
docintel --data-dir ./demo-data doctor
docintel --data-dir ./demo-data serve  # then open http://127.0.0.1:8000
```

On Windows use the commands in the Windows paragraph of the README (`py -m venv .venv`, then `python -m docintel ...` in place of `docintel ...`). Open the app in Edge or Chrome, not in the browser built into VS Code.

The `doctor` output is worth showing on screen once. It prints the Python version, which OCR engine was found, whether the database and the data folder are usable, and an "ai extraction" line that says the AI path is off unless you set a key. Everything in this tour runs on the offline rules reader, so say that early.

## 1. What it is

Show the top of `README.md` and the diagram in `docs/img/architecture.png`. Three sentences are enough: one service holds the REST API, the web app and the background workers; the queue, the full text index and the vectors all live in one SQLite database; a worker takes a file, reads it, extracts, validates, saves and indexes it.

## 2. Upload many files at once

Open **Upload**. Drag in the folder `samples/invoices` (six PDFs in six layouts) together with `samples/cross-check/register.xlsx`. Do not add the other spreadsheets yet, because step 4 depends on that.

Point out that each file gets its own row and moves through its own stages (waiting its turn, reading pages, finding invoice fields, checking the numbers, done), and that one file failing never stops the rest. Open **Documents** to see the list with status, page count and review state.

## 3. Look inside a record

Open `layout-4-SI-25-287.pdf`, which prints no tax line. The page picture is on the left and the extracted values are on the right: invoice number, dates, supplier, customer, subtotal, tax, total, currency, and the line items. Each value has three small confidence bars, and under it the line of text it was read from (the evidence). Clicking into a value jumps the page picture to the page it came from, and **Show the text read from this page** marks the evidence line in that page's text.

The empty tax field says "Nothing was found for this field. It was left empty rather than guessed." Say that this is the rule: a value that is not on the page stays empty, because the system is not allowed to guess. This is also the place to explain the optional AI reader: every value it returns must come with a quote that really occurs in the document, or it is thrown away.

## 4. Compare an invoice batch with a spreadsheet

Open **Compare** and press **Compare now**. The six invoices are checked against the rows of the register. You should see 4 matches, 1 that differs (invoice BD202508018, the total is off by 5.00), 1 that is only in the invoices (INV/25/7550), and 1 that is only in the spreadsheet (REG-9999). This is the "PDF against Excel" check from the brief. Differences are also added to the two records as a warning. Through the API (`mode: manual`) the same check compares any two sets of documents, so PDF against PDF works too.

Do this before loading the other spreadsheets, because Compare looks at every invoice against every spreadsheet row in the system.

## 5. Scans, spreadsheets and several invoices in one PDF

Now drag in `samples/scans`, `samples/spreadsheets` and `samples/multi-invoice`. While they process, say what is happening: pages with a text layer are read directly, scans go to OCR (Tesseract if it is installed, otherwise RapidOCR), spreadsheets are read cell by cell, and a PDF with several invoices is cut into separate records (four, here).

Open the poor quality scan (`scan-poor-quality-INV-2026-84450.png`). Some confidence bars are low and the record is waiting for a person. This is the case the review queue exists for.

## 6. The review queue and corrections

Drag in `samples/review-demo`, the four invoices with a deliberate mistake. Open **To review**. The queue also holds records from the earlier steps (the poor scan, the spreadsheet with no total column, and a few register rows whose totals include a shipping charge the register has no column for), so go to the four by name or use the problem filters. Each problem is shown as a plain sentence: "Subtotal, tax and total do not add up", "A line does not multiply out", "Lines do not add up to the subtotal" and "A field is missing". The API calls them `TOTAL_MISMATCH`, `LINE_ITEM_MISMATCH`, `SUBTOTAL_MISMATCH` and `MISSING_FIELD`.

Open `total-does-not-add-up.pdf`. Subtotal plus tax is 6,243.61 but the page says 6,530.66, and the lines add up to the subtotal, so the printed total is the wrong number. Type 6243.61 over it. The calculation under the amounts turns green as you type. Press **Save changes**: the rules run again on the saved values and the status moves to Corrected. Then press **Approve**. (**Check again** re-runs every rule on demand; after a save it is not needed.)

Mention three things: every edit is validated again, every edit is written to the audit trail (open "1 change made by people" at the bottom of the form to show the old value, the new value and the time), and if somebody else saved the same record in the meantime, the app says "This record changed while you were editing" instead of overwriting their change.

## 7. Duplicates

Drag in the four files of `samples/duplicates`. The identical file is turned away at upload ("already stored"). The same invoice in another layout is flagged `DUPLICATE_INVOICE`, and the message names the file it duplicates. The same supplier, date and total under a new invoice number is flagged `POSSIBLE_DUPLICATE`, which is a warning for a person to decide, not an error.

## 8. Files that fail

Drag in `samples/failures`. Two files are refused straight away (an empty file, and random bytes with a `.pdf` name), three fail with a code and a plain message (a truncated PDF, a password protected PDF, a truncated workbook), and a letter and a white page complete with a "No invoice found" warning (`NO_INVOICE_DETECTED`), so nobody believes they were read and found clean.

On the Upload page press **Retry**. It queues nothing: all three failures are the file's own fault, so the app says they were skipped because the file itself is damaged or locked and a fixed copy has to be uploaded. Failures that a second try can fix (a worker that died, an OCR page that failed) do go back in the queue and carry on after the last stored page.

## 9. Search

Open **Search**. With nothing typed it lists the invoices, newest first. Try a supplier name (`Summit`), then an invoice number (`BD202508018`), then a word that appears in a line item (`thermometer`), and then the same word with a typo (`thermomter`). Exact words finds nothing for the typo, and the default **Both** still puts the right invoice first, with a few looser matches after it. Show the filters (supplier, dates, amount range, review status, file type), the marked snippets, and the switch between **Exact words**, **Similar meaning** and **Both**. Say honestly what the semantic mode is: by default it matches word fragments and typos, not meaning, the screen says so, and a real embedding model can be switched on with a key.

## 10. Tests and numbers

Open **Overview** and read the first figure: documents read without failing. With the whole demo loaded it is a bit over 90 percent, and the rest are the three damaged files from step 8.

Then, in a terminal:

```bash
pytest -p no:warnings
```

It ends with a line like `160 passed in 27s` (the time depends on the machine). Do not add `-q` here: the project already sets it, and a second one hides that last line.

Then open `EVALUATION.md` and walk through three things: the table after fixes, the first blind run (97% of fields right on digital PDFs but only half of the documents completely right), and the scan table by damage level. Close on the limits: synthetic documents, same author, the AI path not measured, SQLite only, built and tested on Linux.

## 11. The AI disclosure

Say it plainly: most of the code was written with Claude as a coding assistant, working from the assessment brief and my rules, and `AI_Prompts.docx` has the disclosure, the exact prompts the product sends to a model, and the instructions I gave while building.

## If something goes wrong while recording

- **Compare shows many more rows than expected.** The other spreadsheets were loaded before Compare ran. Stop the server, delete `demo-data` and start again from step 1.
- **To review looks crowded.** That is expected after steps 2 to 6: it also holds the earlier records that need a person. Open the four review-demo files by name.
- **A scan takes a long time.** A very poor page can use up to 15 seconds of retries. That is the retry budget working, not a hang.
- **Port 8000 is taken.** Use `docintel serve --port 8010`.
