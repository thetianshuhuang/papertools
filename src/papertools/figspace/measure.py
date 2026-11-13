"""Find figure and table floats and measure the space they take up."""

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import pymupdf

from .errors import FigSpaceError
from .layout import Document, Layout, Row, Stop, read_document

Kind = Literal["figure", "table"]

# The first line of a caption: "Figure 3.", "Fig. 3:", "Table 2:", "TABLE II".
_CAPTION = re.compile(
    r"(Figure|Fig\.|Table|Tab\.|TABLE|FIGURE)\s*"
    r"([A-Z]?\d+(?:\.\d+)?|[IVXLC]+)\s*(?:[.:|]|$)"
)


@dataclass
class Float:
    """A figure or table with its caption."""

    kind: Kind
    label: str
    page: int
    caption: pymupdf.Rect
    # Footprint: the column (or full text width) between top and bottom.
    region: pymupdf.Rect
    full_width: bool
    # Whether any content besides the caption was found.
    found: bool
    # Area counted before the end of the main body, in pages.
    area: float = 0.0


@dataclass
class Report:
    """Space taken up by floats in a paper's main body."""

    page_count: int
    layout: Layout
    stop: Stop | None
    # Pages counted, mapped to the regions of each page that are counted.
    counted: dict[int, list[pymupdf.Rect]]
    floats: list[Float] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def page_area(self) -> float:
        """Area of one page's text area, in square points."""
        return self.layout.text_area.width * self.layout.text_area.height

    @property
    def body_pages(self) -> float:
        """Space available for the main body, in pages."""
        return (
            sum(_union_area(regions) for regions in self.counted.values())
            / self.page_area
        )

    def pages_used(self, kind: Kind | None = None) -> float:
        """Space taken up by floats of ``kind`` (or all floats), in pages."""
        total = 0.0
        for page, allowed in self.counted.items():
            regions = [
                f.region
                for f in self.floats
                if f.page == page and kind in (None, f.kind)
            ]
            total += _union_area(_clip(regions, allowed))
        return total / self.page_area


def _union_area(rects: list[pymupdf.Rect]) -> float:
    """Return the area covered by ``rects``, counting overlaps once."""
    rects = [r for r in rects if r.width > 0 and r.height > 0]
    xs = sorted({x for r in rects for x in (r.x0, r.x1)})
    area = 0.0
    for x0, x1 in zip(xs, xs[1:], strict=False):
        spans = sorted((r.y0, r.y1) for r in rects if r.x0 <= x0 and r.x1 >= x1)
        covered, end = 0.0, float("-inf")
        for y0, y1 in spans:
            if y1 > end:
                covered += y1 - max(y0, end)
                end = y1
        area += covered * (x1 - x0)
    return area


def _clip(
    rects: list[pymupdf.Rect], allowed: list[pymupdf.Rect]
) -> list[pymupdf.Rect]:
    return [r & a for r in rects for a in allowed if r.intersects(a)]


def _halves(layout: Layout) -> tuple[pymupdf.Rect, pymupdf.Rect]:
    """Split the text area of a two-column layout down the gutter."""
    assert layout.gutter is not None
    area, middle = layout.text_area, sum(layout.gutter) / 2
    return (
        pymupdf.Rect(area.x0, area.y0, middle, area.y1),
        pymupdf.Rect(middle, area.y0, area.x1, area.y1),
    )


def _counted_regions(
    document: Document, page_limit: int | None
) -> dict[int, list[pymupdf.Rect]]:
    """Return the parts of each page that precede the end of the main body."""
    layout = document.layout
    last = document.page_count
    if page_limit is not None:
        last = min(last, page_limit)
    counted = {i: [pymupdf.Rect(layout.text_area)] for i in range(last)}
    stop = document.stop
    if stop is None or stop.page >= last:
        return counted
    for i in range(stop.page + 1, last):
        del counted[i]
    area, cut = layout.text_area, stop.rect.y0
    side = layout.side(stop.rect)
    if side is None:
        regions = [pymupdf.Rect(area.x0, area.y0, area.x1, cut)]
    else:
        left, right = _halves(layout)
        if side == 0:
            regions = [pymupdf.Rect(left.x0, left.y0, left.x1, cut)]
        else:
            regions = [left, pymupdf.Rect(right.x0, right.y0, right.x1, cut)]
    counted[stop.page] = [r for r in regions if r.height > 0]
    return counted


@dataclass
class _Item:
    rect: pymupdf.Rect
    # "graphic", "text", "body" (body text or a heading), or "caption".
    kind: str


def _caption_rows(rows: list[Row], start: int) -> list[Row]:
    """Return the rows of the caption starting at ``rows[start]``."""
    caption = [rows[start]]
    bottom = rows[start].rect
    size = rows[start].size
    for row in rows[start + 1 :]:
        gap = row.rect.y0 - bottom.y1
        if gap > 0.6 * size:
            break
        overlap = min(row.rect.x1, bottom.x1) - max(row.rect.x0, bottom.x0)
        if (
            abs(row.size - size) > 0.3
            or overlap < 0.5 * min(row.rect.width, bottom.width)
            or _CAPTION.match(row.text)
        ):
            continue
        caption.append(row)
        bottom = row.rect
    return caption


def _grow(
    caption: pymupdf.Rect,
    items: list[_Item],
    *,
    up: bool,
    body_size: float,
) -> tuple[float, bool]:
    """Extend a float from its caption through adjacent content.

    Graphics and other text are added up to a gap of two lines, such as the
    space between a caption and subfigure labels. Body text and headings
    further than a line away, and other captions, end the float. Body-size
    lines are only kept if a graphic or other text follows them closely, as
    in a table with body-size text between rules.

    Returns the far edge of the float and whether any graphic was added.
    """
    sign = -1 if up else 1
    edge = caption.y0 if up else caption.y1

    def near(rect: pymupdf.Rect) -> float:
        return rect.y1 if up else rect.y0

    def far(rect: pymupdf.Rect) -> float:
        return rect.y0 if up else rect.y1

    ahead = [
        item
        for item in items
        if (
            item.rect.y0 < caption.y0 - 0.5
            if up
            else item.rect.y1 > caption.y1 + 0.5
        )
    ]
    ahead.sort(key=lambda item: sign * near(item.rect))
    body_gap = 0.8 * body_size
    content_gap = 2 * body_size
    found = False
    pending: float | None = None  # Edge including tentative body text.
    for item in ahead:
        current = edge if pending is None else pending
        gap = sign * (near(item.rect) - current)
        if item.kind == "caption":
            if gap >= 0:
                break
            continue
        limit = (
            body_gap
            if item.kind == "body" or pending is not None
            else content_gap
        )
        if gap > limit:
            break
        reach = far(item.rect)
        extended = min(current, reach) if up else max(current, reach)
        if item.kind == "body":
            pending = extended
        else:
            edge, pending = extended, None
            found |= item.kind == "graphic"
    return edge, found


def _page_floats(document: Document, index: int) -> list[Float]:
    """Find each caption on a page and grow its float from it."""
    layout = document.layout
    rows = document.rows[index]
    graphics = document.graphics[index]
    captions: list[tuple[re.Match[str], list[Row]]] = []
    in_caption: set[int] = set()
    for i, row in enumerate(rows):
        match = _CAPTION.match(row.text)
        if match and id(row) not in in_caption:
            members = _caption_rows(rows, i)
            in_caption.update(id(r) for r in members)
            captions.append((match, members))
    caption_ids = {id(r) for _, members in captions for r in members}

    floats = []
    for match, members in captions:
        rect = pymupdf.Rect(members[0].rect)
        for row in members[1:]:
            rect |= row.rect
        own = {id(r) for r in members}
        span = layout.span(rect)
        items = [_Item(g, "graphic") for g in graphics]
        for row in rows:
            if id(row) in own:
                continue
            kind = (
                "caption"
                if id(row) in caption_ids
                else "body"
                if row.body
                else "text"
            )
            items.append(_Item(row.rect, kind))
        items = [
            item
            for item in items
            if min(item.rect.x1, span.x1) - max(item.rect.x0, span.x0) > 0.5
        ]
        word = match[1]
        kind: Kind = "table" if word.lower().startswith("tab") else "figure"
        label = f"{kind.capitalize()} {match[2]}"
        # Table captions usually sit above the table, figure captions below.
        order = (False, True) if kind == "table" else (True, False)
        top, bottom, found = rect.y0, rect.y1, False
        for up in order:
            edge, found = _grow(rect, items, up=up, body_size=layout.body_size)
            extent = abs(edge - (rect.y0 if up else rect.y1))
            if found or extent >= 1.5 * layout.body_size:
                if up:
                    top = edge
                else:
                    bottom = edge
                found = True
                break
        area = layout.text_area
        region = pymupdf.Rect(
            span.x0, max(top, area.y0), span.x1, min(bottom, area.y1)
        )
        if region.y1 <= region.y0:
            continue  # In the margin.
        floats.append(
            Float(
                kind,
                label,
                index,
                rect,
                region,
                layout.side(rect) is None,
                found,
            )
        )
    return floats


def measure(source: Path, *, page_limit: int | None = None) -> Report:
    """Measure the space floats take up in the main body of ``source``."""
    try:
        document = pymupdf.open(source)
    except (pymupdf.FileDataError, RuntimeError) as exc:
        raise FigSpaceError(f"Cannot read PDF: {exc}") from exc
    with document:
        if document.needs_pass:
            raise FigSpaceError(
                "Encrypted PDFs are not supported; "
                "export an unencrypted PDF first."
            )
        if not document.page_count:
            raise FigSpaceError("The PDF has no pages.")
        content = read_document(document, page_limit)
    counted = _counted_regions(content, page_limit)
    report = Report(content.page_count, content.layout, content.stop, counted)
    if content.stop is None and page_limit is None:
        report.warnings.append(
            "no References or LLM Usage Statement heading found; "
            "counting every page (use --page-limit to stop earlier)."
        )
    for index, allowed in counted.items():
        floats = _page_floats(content, index)
        if content.stop is not None and index == content.stop.page:
            _extend_stop_page(content, floats, allowed)
        for item in floats:
            area = _union_area(_clip([item.region], allowed))
            if area <= 0:
                continue  # After the end of the main body.
            item.area = area / report.page_area
            report.floats.append(item)
            if not item.found:
                report.warnings.append(
                    f"page {index + 1}: found only the caption of "
                    f"{item.label}; counting the caption alone."
                )
    return report


def _extend_stop_page(
    document: Document, floats: list[Float], allowed: list[pymupdf.Rect]
) -> None:
    """Count all of a full-width float that precedes a left-column heading.

    Only the left column above the heading is counted otherwise, which would
    cut such a float in half.
    """
    layout, stop = document.layout, document.stop
    assert stop is not None
    if layout.gutter is None or layout.side(stop.rect) != 0:
        return
    _, right = _halves(layout)
    for item in floats:
        if item.full_width and item.region.y1 <= stop.rect.y0 + 1:
            allowed.append(
                pymupdf.Rect(right.x0, item.region.y0, right.x1, item.region.y1)
            )
