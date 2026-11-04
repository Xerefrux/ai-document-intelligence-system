# Known limitations

Written so that nobody has to find these out the hard way. Numbers are in `EVALUATION.md`.

## What the evaluation can and cannot tell you

- **The test documents are synthetic.** I generated them with code (`evaluation/synth`): invented companies, six development layouts and four holdout layouts, with scan damage added by an image degrader. The same working session wrote the generator and the extractor, so the extractor has implicitly learned what my generator likes. Real invoices are messier. Expect lower numbers on real ones, and measure again on a sample of your own documents before trusting any figure here.
- **The holdout is blind only once.** The four holdout layouts were written after the extractor was frozen, and the first run on them is saved (`evaluation/results/holdout_first_run.txt`). I then fixed what that run showed. Numbers measured after those fixes are not blind, and `EVALUATION.md` shows both.
- **The AI (LLM) extraction path has no measured accuracy.** The environment I built this in could not reach Gemini or OpenAI. The client, the grounding check, retries, the circuit breaker, chunking and the hybrid merge are tested against a mock server, but I have no idea how well a real model does on these documents, and I report nothing for it.
- **Hosted embeddings were never run against the real services either.** The default semantic search is a character n-gram hash. It finds typos and word endings. It does not know that "laptop" and "notebook" are related.
- **OCR numbers depend on the OCR engine.** The scan figures were measured with Tesseract 5.3.4. Without Tesseract the system falls back to RapidOCR, and `EVALUATION.md` has its own, separate scan figures for that.
- **Scan quality decides everything.** Light scans read well. Medium ones lose fields. Heavy ones (strong blur, noise and skew together) are mostly unreadable, and the system says so by flagging them instead of guessing. The exact figures are in the report.
- **Scanned pages are slow.** A hopeless page costs the base OCR passes plus up to 15 seconds of retries (`DOCINTEL_OCR_RETRY_BUDGET_SECONDS`). A scanned 1000 page file was **not** measured; `EVALUATION.md` gives the per page cost I did measure and what that implies. A text PDF of 1000 pages was measured.

## What the extractor does not do

- Invoices and receipts only. Purchase orders, bank statements, contracts and similar documents are reported as `NO_INVOICE_DETECTED`.
- Latin script and English labels. Labels in other languages, right to left text and handwriting are not supported.
- One currency per invoice. Mixed currency documents are not split.
- Line item tables that wrap descriptions over several lines, merge cells or nest sub totals can lose rows. Totals and counts are checked, so this is usually flagged, but not always.
- A date written 03/04/2026 can mean two things. The reader uses the other dates in the same document to decide and raises `DATE_AMBIGUOUS` when nothing settles it. A document that is wrong in a consistent way will not be caught.
- Wrong characters from OCR (a `1` read for an `I` inside an invoice number) cannot be noticed by any rule here, and such a value is not flagged. The evaluation counts these as silent errors.

## Architecture limits

- **SQLite is the only tested database.** It allows one writer at a time and one machine. The schema and the queue are written to move to PostgreSQL (`FOR UPDATE SKIP LOCKED` for claiming jobs), but I did not run that, so I do not claim it works.
- **Vector search reads every vector for each query.** Fine for tens of thousands of records. Not for millions.
- **The rate limiter is in memory** and counts per process.
- **There is no Docker image** in this submission. I could not build or run one where I worked, and I did not want to ship one I had not run.
- **Developed and tested on Linux** with Python 3.13. I did not run it on Windows or macOS, so I cannot promise it works there. What I could check without Windows: every dependency has a Windows build for Python 3.11 to 3.14, the tests pass under a strict ASCII default text encoding, and `tests/test_windows.py` forces the Windows only problems I know of. If something breaks, `docintel doctor` is the first place to look, and `python -m docintel` works when the `docintel` launcher does not.
- **No user accounts.** One shared API key; the audit trail records what changed and when but not who.
- Reprocessing a document replaces its records. Manual corrections would be lost, so the API refuses unless the request says `confirm: true`.
- The front end has only 18 small automated tests (formatting helpers, a few components, the search page and the message that replaces a screen that fails to draw). The pages themselves are type checked, and `scripts/ui_smoke.py` opens every screen in a real browser, but it needs Playwright and is not part of `pytest`. Hand checks at desktop and phone width missed one bug: the Search page went blank when it was opened with data loaded and nothing typed. It is fixed and covered by tests, and one broken screen now shows a message instead of blanking the whole app. The back end is covered by the pytest suite.

See `SECURITY.md` for the security side of this list.
