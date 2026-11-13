"""Text area, column, and end-of-body detection tests."""

import pymupdf
import pytest

from papertools.figspace.layout import read_document


def _read(path, page_limit=None):
    with pymupdf.open(path) as document:
        return read_document(document, page_limit)


def test_two_column_layout(paper):
    """Find the body size, text area, and both columns."""
    doc = paper()
    doc.plain_pages(2)
    layout = _read(doc.save()).layout
    assert layout.body_size == 10
    area = layout.text_area
    assert (area.x0, area.x1) == pytest.approx((doc.LEFT, doc.RIGHT), abs=1)
    # The last baseline that fits sits up to one line above the bottom.
    assert area.y0 == pytest.approx(doc.TOP, abs=1)
    assert doc.BOTTOM - 15 < area.y1 <= doc.BOTTOM
    assert layout.gutter == pytest.approx((297, 315), abs=1)


def test_one_column_layout(paper):
    """Report no gutter for single-column text."""
    doc = paper(columns=1)
    doc.plain_pages(2)
    layout = _read(doc.save()).layout
    assert layout.gutter is None
    assert layout.columns == [layout.text_area]


def test_running_header_and_page_number_ignored(paper):
    """Leave running headers and page numbers out of the text area."""
    doc = paper()
    for number in range(1, 5):
        doc.new_page()
        doc.page.insert_text(
            (doc.LEFT, 50), "Under review as a conference paper", fontsize=10
        )
        doc.page.insert_text((300, 750), str(number), fontsize=10)
        doc.text(0, doc.TOP, doc.BOTTOM)
        doc.text(1, doc.TOP, doc.BOTTOM)
    area = _read(doc.save()).layout.text_area
    # The last baseline that fits sits up to one line above the bottom.
    assert area.y0 == pytest.approx(doc.TOP, abs=1)
    assert doc.BOTTOM - 15 < area.y1 <= doc.BOTTOM


@pytest.mark.parametrize(
    "heading",
    ["References", "6. References", "LLM Usage Statement", "A LLM Usage"],
)
def test_stop_heading(paper, heading):
    """Stop at References or LLM Usage Statement headings, numbered or not."""
    doc = paper()
    doc.plain_pages(1)
    doc.new_page()
    doc.text(0, doc.TOP, doc.BOTTOM)
    doc.text(1, doc.TOP, 300)
    doc.heading(1, 320, heading)
    doc.text(1, 330, doc.BOTTOM)
    stop = _read(doc.save()).stop
    assert stop is not None
    assert (stop.page, stop.text) == (1, heading)
    assert stop.rect.y0 == pytest.approx(308, abs=4)


def test_stop_uses_reading_order(paper):
    """Pick the left column's heading over a higher one in the right."""
    doc = paper()
    doc.new_page()
    doc.text(0, doc.TOP, 400)
    doc.heading(0, 420, "References")
    doc.text(0, 430, doc.BOTTOM)
    doc.heading(1, 90, "LLM Usage Statement")
    doc.text(1, 100, doc.BOTTOM)
    assert _read(doc.save()).stop.text == "References"


def test_reference_in_sentence_is_not_a_heading(paper):
    """Ignore 'References' inside a line of body text."""
    doc = paper()
    doc.plain_pages(1)
    doc.page.insert_text((doc.LEFT, 400), "References are in the appendix.")
    assert _read(doc.save()).stop is None


def test_stop_beyond_page_limit_is_ignored(paper):
    """Do not look for the heading past --page-limit."""
    doc = paper()
    doc.plain_pages(2)
    doc.heading(0, 300, "References")
    result = _read(doc.save(), page_limit=1)
    assert result.stop is None
    assert sorted(result.rows) == [0]
