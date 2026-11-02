"""Page text as lines of cells.

A cell is a run of words separated by single spaces. Two or more spaces start a new cell, which is
how the layout text from docintel.pipeline.layout marks the gap between columns.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_CELL_RE = re.compile(r"\S+(?: (?![·•|] )\S+)*")


@dataclass
class Cell:
    text: str
    col: int
    end: int

    @property
    def center(self) -> float:
        return (self.col + self.end) / 2


@dataclass
class DocLine:
    page: int
    text: str
    cells: list[Cell] = field(default_factory=list)
    ocr_conf: float | None = None
    source: str = "text"  # text | ocr | sheet

    @property
    def blank(self) -> bool:
        return not self.cells

    @property
    def plain(self) -> str:
        return " ".join(c.text for c in self.cells)

    @property
    def indent(self) -> int:
        return self.cells[0].col if self.cells else 0


def split_cells(line: str) -> list[Cell]:
    line = line.replace("\t", "  ")
    cells = [Cell(m.group(), m.start(), m.end()) for m in _CELL_RE.finditer(line)]
    return [c for c in cells if c.text not in ("·", "•", "|")]


def build_lines(pages: list[tuple[int, str, float | None, str]]) -> list[DocLine]:
    """pages: (page_no, text, ocr_confidence, source). Returns every line of every page, blank lines included."""
    out: list[DocLine] = []
    for page_no, text, conf, source in pages:
        for raw in (text or "").split("\n"):
            raw = raw.rstrip()
            out.append(DocLine(page_no, raw, split_cells(raw), conf, source))
    return out


def clip(s: str, n: int = 200) -> str:
    s = " ".join(s.split())
    return s if len(s) <= n else s[: n - 1] + "…"
