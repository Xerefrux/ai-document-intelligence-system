"""PNG previews of a document page, so the review screen can show the source next to the extracted values."""

from __future__ import annotations

import io
from pathlib import Path

import numpy as np
from PIL import Image

from ..config import Settings
from ..pipeline.image import load_image_frame
from ..pipeline.ocr import render_pdf_page
from ..pipeline.types import DocIntelError

MAX_SIDE = 1500


def preview_png(settings: Settings, doc_id: str, stored: Path, file_type: str, page_no: int) -> bytes:
    """Return the PNG bytes of page `page_no` (1 based). Rendered once, then cached on disk."""
    cache = settings.previews_dir / f"{doc_id}-{page_no}.png"
    if cache.exists():
        return cache.read_bytes()
    if file_type == "pdf":
        rgb = render_pdf_page(str(stored), page_no - 1, 110, MAX_SIDE)  # takes the PDFium lock itself
    elif file_type == "image":
        rgb = load_image_frame(str(stored), page_no - 1, settings)
    else:
        raise DocIntelError("Spreadsheets have no page image.", code="NO_PREVIEW")
    img = Image.fromarray(np.asarray(rgb))
    if max(img.size) > MAX_SIDE:
        img.thumbnail((MAX_SIDE, MAX_SIDE))
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=False)
    data = buf.getvalue()
    try:
        settings.previews_dir.mkdir(parents=True, exist_ok=True)
        cache.write_bytes(data)
    except OSError:
        pass
    return data
