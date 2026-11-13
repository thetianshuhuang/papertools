"""Float detection and area accounting tests."""

import pytest

from papertools.figspace.measure import measure

COLUMN_WIDTH = 297 - 54


def _figure_page(doc, column=0, *, label="Figure 1.", top=320, bottom=420):
    """Add a page with one figure in ``column``; return the caption bottom."""
    doc.new_page()
    columns = [None] if column is None else range(len(doc.columns))
    for i in columns:
        if i == column:
            doc.text(i, doc.TOP, top - 20)
            doc.graphic(i, top, bottom)
            end = doc.caption(i, bottom + 10, label)
            doc.text(i, end + 20, doc.BOTTOM)
        else:
            doc.text(i, doc.TOP, doc.BOTTOM)
    return end


def test_column_figure(paper):
    """Measure a column figure from its graphic through its caption."""
    doc = paper()
    end = _figure_page(doc, 0)
    doc.plain_pages(1)
    report = measure(doc.save())
    (figure,) = report.floats
    assert (figure.kind, figure.label, figure.page) == ("figure", "Figure 1", 0)
    assert not figure.full_width and figure.found
    region = figure.region
    assert (region.x0, region.x1) == pytest.approx((54, 297), abs=1)
    assert (region.y0, region.y1) == pytest.approx((320, end), abs=1.5)
    expected = COLUMN_WIDTH * (end - 320) / report.page_area
    assert figure.area == pytest.approx(expected, rel=0.01)
    assert report.pages_used("figure") == pytest.approx(expected, rel=0.01)
    assert report.pages_used("table") == 0
    assert report.body_pages == pytest.approx(2)


def test_full_width_figure_at_top(paper):
    """Snap a figure spanning both columns to the full text width."""
    doc = paper()
    doc.new_page()
    doc.graphic(None, doc.TOP, 250)
    end = doc.caption(None, 260, "Figure 1:")
    doc.text(0, end + 20, doc.BOTTOM)
    doc.text(1, end + 20, doc.BOTTOM)
    doc.plain_pages(1)
    (figure,) = measure(doc.save()).floats
    assert figure.full_width
    assert (figure.region.x0, figure.region.x1) == pytest.approx(
        (doc.LEFT, doc.RIGHT), abs=1
    )
    assert figure.region.y0 == pytest.approx(doc.TOP, abs=1.5)


def test_table_caption_above(paper):
    """Measure a table from its caption above through its bottom rule."""
    doc = paper()
    doc.new_page()
    end = doc.caption(1, doc.TOP, "Table 2:", lines=1)
    doc.rule(1, end + 6)
    for i in range(4):
        y = end + 18 + 12 * i
        doc.page.insert_text((340, y), "Model", fontsize=8)
        doc.page.insert_text((450, y), "0.42", fontsize=8)
    bottom = end + 18 + 12 * 3 + 6
    doc.rule(1, bottom)
    doc.text(1, bottom + 20, doc.BOTTOM)
    doc.text(0, doc.TOP, doc.BOTTOM)
    doc.plain_pages(1)
    report = measure(doc.save())
    (table,) = report.floats
    assert (table.kind, table.label) == ("table", "Table 2")
    assert table.region.x0 == pytest.approx(315, abs=1)
    assert table.region.y0 == pytest.approx(doc.TOP, abs=1.5)
    assert table.region.y1 == pytest.approx(bottom, abs=1)
    assert report.pages_used("figure") == 0


def test_paragraph_above_figure_not_included(paper):
    """Stop the figure at the paragraph above, including its short last line."""
    doc = paper()
    _figure_page(doc, 1, top=300)
    doc.plain_pages(1)
    (figure,) = measure(doc.save()).floats
    assert figure.region.y0 == pytest.approx(300, abs=1)


def test_stop_page_counts_text_before_heading(paper):
    """Count floats before the heading, but not after it."""
    doc = paper()
    doc.plain_pages(1)
    doc.new_page()
    doc.graphic(0, doc.TOP, 200)
    end = doc.caption(0, 210, "Figure 1.")
    doc.text(0, end + 20, doc.BOTTOM)
    doc.text(1, doc.TOP, 300)
    doc.heading(1, 320, "References")
    doc.graphic(1, 340, 450)
    doc.caption(1, 460, "Figure 2.")
    report = measure(doc.save())
    assert [f.label for f in report.floats] == ["Figure 1"]
    area = report.layout.text_area
    half = area.width / 2
    left = half * area.height
    right = half * (report.stop.rect.y0 - area.y0)
    assert report.body_pages == pytest.approx(
        1 + (left + right) / report.page_area
    )
    assert report.stop.text == "References"
    assert not report.warnings


def test_full_width_float_above_left_column_heading(paper):
    """Count all of a full-width float above a heading in the left column."""
    doc = paper()
    doc.plain_pages(1)
    doc.new_page()
    doc.graphic(None, doc.TOP, 250)
    end = doc.caption(None, 260, "Figure 1.")
    doc.text(0, end + 20, 400)
    doc.heading(0, 420, "References")
    doc.text(0, 430, doc.BOTTOM)
    doc.text(1, end + 20, doc.BOTTOM)
    report = measure(doc.save())
    (figure,) = report.floats
    assert figure.area == pytest.approx(
        (doc.RIGHT - doc.LEFT) * (end - doc.TOP) / report.page_area, rel=0.01
    )


def test_page_limit(paper):
    """Count only the pages up to --page-limit."""
    doc = paper()
    _figure_page(doc, 0, label="Figure 1.")
    _figure_page(doc, 1, label="Figure 2.")
    doc.heading(1, 700, "References")
    path = doc.save()
    report = measure(path, page_limit=1)
    assert [f.label for f in report.floats] == ["Figure 1"]
    assert report.body_pages == pytest.approx(1)
    assert not report.warnings
    report = measure(path, page_limit=10)
    assert [f.label for f in report.floats] == ["Figure 1", "Figure 2"]


def test_no_heading_warns(paper):
    """Warn when counting runs to the end of the PDF."""
    doc = paper()
    doc.plain_pages(2)
    report = measure(doc.save())
    assert report.body_pages == pytest.approx(2)
    assert "no References" in report.warnings[0]


def test_side_by_side_floats_counted_once(paper):
    """Count the shared band of side-by-side floats once in the totals."""
    doc = paper(columns=1)
    doc.new_page()
    doc.page.draw_rect((70, doc.TOP, 290, 250), fill=(0.5, 0.5, 0.5))
    doc.page.draw_rect((320, doc.TOP, 540, 250), fill=(0.5, 0.5, 0.5))
    for x, label in ((70, "Figure 1."), (320, "Figure 2.")):
        doc.page.insert_text((x, 270), f"{label} Left.", fontsize=9)
    doc.text(0, 300, doc.BOTTOM)
    doc.plain_pages(1)
    report = measure(doc.save())
    assert [f.label for f in report.floats] == ["Figure 1", "Figure 2"]
    band = report.floats[0].area
    assert report.floats[1].area == pytest.approx(band)
    assert report.pages_used() == pytest.approx(band)


def test_caption_without_content_warns(paper):
    """Count a lone caption, with a warning."""
    doc = paper()
    doc.new_page()
    doc.text(0, doc.TOP, 300)
    end = doc.caption(0, 330, "Figure 1.")
    doc.text(0, end + 30, doc.BOTTOM)
    doc.text(1, doc.TOP, doc.BOTTOM)
    doc.plain_pages(1)
    report = measure(doc.save())
    (figure,) = report.floats
    assert not figure.found
    assert "only the caption of Figure 1" in " ".join(report.warnings)


def test_figure_reference_in_text_is_not_a_caption(paper):
    """Ignore 'Figure 3 shows' at the start of a line of body text."""
    doc = paper()
    doc.plain_pages(1)
    doc.page.insert_text((doc.LEFT, 300), "Figure 3 shows the results.")
    assert measure(doc.save()).floats == []


def test_table_caption_below_above_figure(paper):
    """Grow a table up from its caption when below it is another float.

    Some templates put table captions below the table; growing down would
    take the next figure, which ends at its own caption.
    """
    doc = paper()
    doc.new_page()
    doc.rule(None, doc.TOP + 2)
    for i in range(5):
        y = doc.TOP + 14 + 12 * i
        doc.page.insert_text((100, y), "Model", fontsize=8)
        doc.page.insert_text((400, y), "0.42", fontsize=8)
    doc.rule(None, doc.TOP + 66)
    table_end = doc.caption(None, doc.TOP + 72, "Table 1:")
    graphic_top = table_end + 12
    doc.graphic(None, graphic_top, 400)
    figure_end = doc.caption(None, 410, "Figure 1:")
    doc.text(0, figure_end + 20, doc.BOTTOM)
    doc.text(1, figure_end + 20, doc.BOTTOM)
    doc.plain_pages(1)
    table, figure = measure(doc.save()).floats
    assert table.label == "Table 1" and table.found
    assert (table.region.y0, table.region.y1) == pytest.approx(
        (doc.TOP + 2, table_end), abs=1.5
    )
    assert figure.region.y0 == pytest.approx(graphic_top, abs=1.5)
