"""Build small synthetic papers with body text, figures, and tables."""

from itertools import cycle
from pathlib import Path

import pymupdf
import pytest

TOP, BOTTOM = 72.0, 720.0
LEFT, RIGHT = 54.0, 558.0
TWO_COLUMNS = ((54.0, 297.0), (315.0, 558.0))
ONE_COLUMN = ((54.0, 558.0),)
PITCH = 14.0  # Base-14 line boxes are about 13.8pt tall at 10pt.
WORDS = ("lorem", "ipsum", "dolor", "sit", "amet", "sed", "do", "ut")


def _width(text: str, size: float = 10, font: str = "tiro") -> float:
    return pymupdf.get_text_length(text, fontname=font, fontsize=size)


class Paper:
    """A paper under construction, with helpers placed by baseline."""

    TOP, BOTTOM, LEFT, RIGHT = TOP, BOTTOM, LEFT, RIGHT

    def __init__(self, path: Path, columns=TWO_COLUMNS):
        self.path = path
        self.columns = columns
        self.document = pymupdf.open()
        self.page = None
        self.lines = 0  # Varies the words, as no two lines in a paper match.

    def new_page(self) -> "Paper":
        """Start a new letter-size page."""
        self.page = self.document.new_page(width=612, height=792)
        return self

    def span(self, column: int | None) -> tuple[float, float]:
        """Return a column's edges, or the full width for None."""
        return (LEFT, RIGHT) if column is None else self.columns[column]

    def line(self, column, baseline: float, *, last: bool = False) -> None:
        """Write one justified line of 10pt body text."""
        x0, x1 = self.span(column)
        self.lines += 1
        words, text = cycle(WORDS[self.lines % len(WORDS) :] + WORDS), ""
        end = "end."
        room = (x1 - x0) * (0.5 if last else 1) - _width(f" {end}") - 2
        for word in words:
            if _width(f"{text} {word}".strip()) > room:
                break
            text = f"{text} {word}".strip()
        while not last and _width(f"{text} a") <= room:
            text += " a"
        self.page.insert_text(
            (x0, baseline), text, fontsize=10, fontname="tiro"
        )
        if last:
            return
        self.page.insert_text(
            (x1 - _width(end), baseline), end, fontsize=10, fontname="tiro"
        )

    def paragraph(self, column, first: float, last: float) -> float:
        """Fill baselines ``first``..``last``; return the next free y."""
        baseline = first
        while baseline + PITCH <= last:
            self.line(column, baseline)
            baseline += PITCH
        self.line(column, baseline, last=True)
        return baseline + 4

    def text(self, column, top: float, bottom: float) -> None:
        """Fill a column (or the full width) with text between two y's."""
        self.paragraph(column, top + 10.5, bottom - 3.5)

    def graphic(self, column, top: float, bottom: float) -> None:
        """Draw a filled box across most of a column."""
        x0, x1 = self.span(column)
        rect = pymupdf.Rect(x0 + 10, top, x1 - 10, bottom)
        self.page.draw_rect(rect, color=(0, 0, 0), fill=(0.7, 0.7, 0.7))

    def caption(self, column, top: float, label: str, lines: int = 2) -> float:
        """Write a 9pt caption; return its bottom."""
        x0, x1 = self.span(column)
        baseline = top + 9.5
        for i in range(lines):
            text = f"{label} A caption" if i == 0 else "More"
            while _width(f"{text} text", 9) < 0.9 * (x1 - x0):
                text += " text"
            self.page.insert_text(
                (x0, baseline), text, fontsize=9, fontname="tiro"
            )
            baseline += 12.5
        return baseline - 12.5 + 3

    def rule(self, column, y: float) -> None:
        """Draw a horizontal table rule."""
        x0, x1 = self.span(column)
        self.page.draw_line((x0 + 20, y), (x1 - 20, y), width=0.5)

    def heading(self, column, baseline: float, text: str) -> None:
        """Write a 12pt bold section heading."""
        x0, _ = self.span(column)
        self.page.insert_text(
            (x0, baseline), text, fontsize=12, fontname="tibo"
        )

    def save(self) -> Path:
        """Write the PDF and return its path."""
        self.document.save(self.path)
        self.document.close()
        return self.path

    def plain_pages(self, count: int) -> None:
        """Add pages of nothing but body text."""
        for _ in range(count):
            self.new_page()
            for i in range(len(self.columns)):
                self.text(i, TOP, BOTTOM)


@pytest.fixture
def paper(tmp_path):
    """Return a factory for papers with one or two columns.

    Text areas span x 54–558 and y 72–720; two columns split at 297–315.
    """

    def make(columns: int = 2) -> Paper:
        return Paper(
            tmp_path / "paper.pdf", TWO_COLUMNS if columns == 2 else ONE_COLUMN
        )

    return make
