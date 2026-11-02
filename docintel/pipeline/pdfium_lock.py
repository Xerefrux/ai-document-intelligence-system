"""One lock for every call into PDFium.

PDFium is not thread safe: two threads must never be inside it at the same time, not even on different
documents, or the whole process can crash ("Segmentation fault", which no exception handler can catch).
The workers are threads and the web app renders page previews in request threads, so every use of
pypdfium2 in this program goes through this lock. Objects are closed inside the lock too, because closing
a page or a document calls into PDFium as well.
"""

from __future__ import annotations

import threading

PDFIUM_LOCK = threading.RLock()
