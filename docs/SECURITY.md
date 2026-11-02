# Security

This service reads files from strangers and sends parts of them to a third party if an AI key is set, so the threat model is mostly about hostile files and about not leaking data. This page lists what is done, how each item is tested, and what is not done. There was no external security review; the list is my own assessment.

## What is protected, and how

| Risk | What the code does | Tested in |
|---|---|---|
| Anyone on the network uses the API | With `DOCINTEL_API_KEY` set, every `/api` call needs `X-API-Key` or `Authorization: Bearer`. The comparison is constant time. Only `/api/health` is public. The server binds to `127.0.0.1` unless told otherwise. | `test_api_key_protects_everything_except_health` |
| The API key leaks | Keys come from the environment, are never returned by `/api/config`, and are kept in `sessionStorage` by the web app, so closing the tab forgets them | `test_api_key_protects_everything_except_health` |
| Flooding | Per client rate limit (`429` with `Retry-After`), a cap on request size, files per request and size per file. Uploads stream to disk instead of memory | `test_too_many_requests_get_429_with_retry_after`, `test_an_upload_that_is_too_large_is_refused` |
| A file that lies about what it is | The type comes from the first bytes, never from the name or the client's content type. An executable called `invoice.pdf` is rejected, a PNG called `invoice.pdf` is read as a PNG | `test_many_files_are_accepted_and_each_gets_its_own_status`, `tests/test_readers.py` |
| Path traversal through file names | Files are stored under a random id with a fixed extension. The original name is only displayed, and is cleaned first | `test_a_hostile_file_name_cannot_escape_the_upload_folder` |
| Decompression bombs and huge inputs | Limits on image pixels, PDF pages, TIFF frames, sheet rows, columns and sheets, and on the uncompressed size inside an XLSX archive. `defusedxml` is a dependency, and openpyxl uses it for the XML inside XLSX files when it is installed. A time limit applies to a document and to an OCR page | `tests/test_readers.py` |
| A crash in a parser takes the API down | OCR runs in a separate process pool. A worker error ends the job, not the server | `tests/test_system.py` (failure scenarios) |
| SQL injection | All queries are parameterised through SQLAlchemy. Search text is turned into tokens and quoted before it reaches the FTS5 `MATCH` parameter | `tests/test_system.py` search tests |
| Cross site scripting | React escapes output. Search highlights are built as elements from control character markers, not with `innerHTML`. The UI is served with a Content Security Policy. Original files and page images are served with `Content-Security-Policy: sandbox; default-src 'none'` and `nosniff`, so a hostile PDF or image cannot run script in the app's origin | `test_the_original_file_is_served_in_a_sandbox` |
| Clickjacking and sniffing | `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`, a restrictive `Permissions-Policy`, `Cache-Control: no-store` on API responses | `test_errors_have_one_shape_and_security_headers_are_set` |
| Spreadsheet formula injection | A CSV export prefixes any cell that starts with `=`, `+`, `-` or `@` with a quote, so opening it in Excel does not run a formula that came from a document | `test_export_neutralises_spreadsheet_formulas` |
| Information in error messages | One error shape for the whole API. No paths, stack traces or SQL in a response; they go to the log with a request id | `test_errors_have_one_shape_and_security_headers_are_set` |
| Lost updates | Records carry a version. An edit based on an old version is refused with `409`, and every change is written to an audit trail with the old and new value | `test_two_people_cannot_silently_overwrite_each_other` |
| Cross origin calls | No CORS headers unless `DOCINTEL_CORS_ORIGINS` is set | `docintel/api/app.py` |

## Sending text to an AI service

This only happens when you set a Gemini or OpenAI key. Without a key nothing leaves the machine.

- Before sending, emails, phone numbers, IBANs, bank account numbers and card numbers that pass the card checksum are masked (`DOCINTEL_LLM_REDACT_PII`, on by default). This is best effort. Names, addresses and anything unusual are not masked, so do not point the service at a document set you may not share with the provider.
- The text of a document is untrusted input. The prompt tells the model to treat it as data, but that is a request, not a guarantee. The real defence is on the way back: the model must quote the text each value came from, the quote must occur in the document, and the value must be readable from the quote. Anything else is dropped and reported. A document that says "ignore your instructions" can at worst make the model return values that really are printed in that document.
- The model has no tools and no way to write anywhere. Its output is parsed against a fixed schema and validated like any other extraction result.

## Not done

I would not call this production ready without these:

- **No user accounts or roles.** There is one shared API key. The audit trail records what changed and when, not who did it.
- **No TLS.** Run it behind a reverse proxy that terminates HTTPS, and set `DOCINTEL_TRUST_PROXY_HEADERS=true` only when such a proxy really sets the client address header.
- **No encryption at rest.** The database and the uploads folder are plain files. Protect them with disk encryption and file permissions.
- **No malware scanning** of uploads. Files are never executed and are served in a sandbox, but they are stored as received.
- **The rate limiter is in memory** and per process. Several API processes each count on their own.
- **No data retention rules.** Documents stay until someone deletes them (`DELETE /api/documents/{id}` removes the file, the records, the text and the vectors).
- **Dependencies are not pinned to exact versions** and I did not run a vulnerability scan on them.
