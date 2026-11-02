"""Turn positioned text runs into plain text that keeps the page layout.

PDF text runs and OCR boxes both become Segments. This module groups them into lines and places
each one at a column that matches its horizontal position, the way `pdftotext -layout` does.
Columns of a table stay aligned, a label above a value stays above it, and the extractors can
split a row into cells on runs of two or more spaces.
"""

from __future__ import annotations

from statistics import median
from typing import Sequence

from .types import Segment


def build_layout(segments: Sequence[Segment], *, max_cols: int = 200) -> str:
    segs = [s for s in segments if s.text and s.text.strip()]
    if not segs:
        return ""

    heights = [s.h for s in segs if s.h > 0] or [10.0]
    mh = median(heights)
    widths = [s.w / max(len(s.text), 1) for s in segs if len(s.text) >= 3 and s.w > 0]
    cw = median(widths) if widths else mh * 0.5
    cw = max(cw, 0.5)
    min_x = min(s.x0 for s in segs)
    max_x = max(s.x1 for s in segs)
    if (max_x - min_x) / cw > max_cols:
        cw = (max_x - min_x) / max_cols

    # Group into lines by vertical position.
    ordered = sorted(segs, key=lambda s: (s.yc, s.x0))
    lines: list[list[Segment]] = []
    centers: list[float] = []
    heights_line: list[float] = []
    for s in ordered:
        if lines:
            tol = 0.55 * max(mh, min(s.h or mh, heights_line[-1]))
            if abs(s.yc - centers[-1]) <= tol:
                n = len(lines[-1])
                lines[-1].append(s)
                centers[-1] = (centers[-1] * n + s.yc) / (n + 1)
                heights_line[-1] = max(heights_line[-1], s.h)
                continue
        lines.append([s])
        centers.append(s.yc)
        heights_line.append(s.h or mh)

    # Typical distance between lines, used to put a blank line between blocks.
    pitches = [b - a for a, b in zip(centers, centers[1:]) if b - a > 0]
    pitch = median(pitches) if pitches else mh * 1.3

    out: list[str] = []
    prev_center: float | None = None
    for ln, yc in zip(lines, centers):
        ln.sort(key=lambda s: s.x0)
        buf = ""
        prev_x1 = None
        for s in ln:
            txt = " ".join(s.text.split())
            col = int(round((s.x0 - min_x) / cw))
            if not buf:
                buf = " " * col + txt
            else:
                gap = s.x0 - (prev_x1 if prev_x1 is not None else s.x0)
                if gap <= 0.3 * cw:
                    buf += txt
                elif gap <= 1.6 * cw:
                    buf += " " + txt
                else:
                    pad = max(col - len(buf), 2)
                    buf += " " * pad + txt
            prev_x1 = max(prev_x1 or s.x1, s.x1)
        if prev_center is not None and yc - prev_center > 1.7 * pitch:
            out.append("")
        out.append(buf.rstrip())
        prev_center = yc
    return "\n".join(out)
