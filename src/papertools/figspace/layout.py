"""Find a paper's text lines, graphics, text area, columns, and body end."""

import re
from collections import Counter
from dataclasses import dataclass, field

import pymupdf
from PIL import Image, ImageDraw

from .errors import FigSpaceError

_NO_TEXT = "The PDF has no extractable text; scanned papers are not supported."

_BOLD_FONT = re.compile(r"bold|medi|semi|demi|black|heavy", re.IGNORECASE)
# Section headings that end the main body, optionally numbered ("6.", "A").
_END_HEADING = re.compile(
    r"(?:(?:\d+(?:\.\d+)*|[A-Z])\.?\s+)?"
    r"(?:references|bibliography|llm usage(?: statement)?"
    r"|(?:the )?use of (?:large language models|llms)(?: \(llms\))?)",
    re.IGNORECASE,
)
_PAGE_NUMBER = re.compile(r"[\divxlc]+", re.IGNORECASE)
# Drawing kinds from Page.get_bboxlog() that are visible graphics.
_GRAPHIC_KINDS = ("path", "image", "imgmask", "shade")


@dataclass
class Fragment:
    """One extracted text line, as PyMuPDF reports it."""

    rect: pymupdf.Rect
    text: str
    size: float
    bold: bool


@dataclass
class Row:
    """Fragments sharing a baseline in one column, i.e. one visual line."""

    rect: pymupdf.Rect
    text: str
    size: float
    bold: bool
    # Body text or a heading, which a float cannot extend through.
    body: bool = False


@dataclass
class Layout:
    """Document-wide geometry of the main text."""

    body_size: float
    text_area: pymupdf.Rect
    # Inner edges of a two-column layout's columns, or None for one column.
    gutter: tuple[float, float] | None = None

    @property
    def columns(self) -> list[pymupdf.Rect]:
        """Return the column rectangles, left to right."""
        area = self.text_area
        if self.gutter is None:
            return [pymupdf.Rect(area)]
        return [
            pymupdf.Rect(area.x0, area.y0, self.gutter[0], area.y1),
            pymupdf.Rect(self.gutter[1], area.y0, area.x1, area.y1),
        ]

    def side(self, rect: pymupdf.Rect) -> int | None:
        """Return the column index ``rect`` lies in, or None if it spans."""
        if self.gutter is None:
            return None
        middle = sum(self.gutter) / 2
        if rect.x1 <= middle:
            return 0
        if rect.x0 >= middle:
            return 1
        return None

    def span(self, rect: pymupdf.Rect) -> pymupdf.Rect:
        """Return the column (or full text width) that ``rect`` occupies."""
        side = self.side(rect)
        if side is None:
            return pymupdf.Rect(self.text_area)
        return self.columns[side]


@dataclass
class Stop:
    """The heading where the main body ends."""

    page: int
    rect: pymupdf.Rect
    text: str


@dataclass
class Document:
    """Text rows and graphics for each page, with the document layout."""

    page_count: int
    layout: Layout
    rows: dict[int, list[Row]] = field(default_factory=dict)
    graphics: dict[int, list[pymupdf.Rect]] = field(default_factory=dict)
    stop: Stop | None = None


def _fragments(page: pymupdf.Page) -> list[Fragment]:
    fragments = []
    data = page.get_text("dict", flags=pymupdf.TEXTFLAGS_TEXT)
    for block in data["blocks"]:
        for line in block.get("lines", ()):
            dx, dy = line["dir"]
            if dx < 0.99 or abs(dy) > 0.01:
                continue  # Rotated text, e.g. an arXiv stamp.
            spans = [s for s in line["spans"] if s["text"].strip()]
            text = "".join(s["text"] for s in line["spans"]).strip()
            if not spans or not text:
                continue
            main = max(spans, key=lambda s: len(s["text"].strip()))
            bold = bool(main["flags"] & pymupdf.TEXT_FONT_BOLD) or bool(
                _BOLD_FONT.search(main["font"])
            )
            fragments.append(
                Fragment(
                    pymupdf.Rect(line["bbox"]),
                    text,
                    round(main["size"], 1),
                    bold,
                )
            )
    return fragments


def _body_size(pages: list[list[Fragment]]) -> float:
    sizes: Counter[float] = Counter()
    for fragments in pages:
        for fragment in fragments:
            sizes[fragment.size] += len(fragment.text)
    if not sizes:
        raise FigSpaceError(_NO_TEXT)
    return sizes.most_common(1)[0][0]


def _body_fragments(
    pages: list[list[Fragment]], body_size: float
) -> list[list[Fragment]]:
    """Keep body-size text, without page numbers and running headers."""
    candidates = [
        [
            f
            for f in fragments
            if abs(f.size - body_size) <= 0.3
            and not _PAGE_NUMBER.fullmatch(f.text)
        ]
        for fragments in pages
    ]

    def key(fragment: Fragment) -> tuple[int, str]:
        return round(fragment.rect.y0), re.sub(r"\d+", "#", fragment.text)

    def edges(fragments: list[Fragment]) -> list[Fragment]:
        """Return the topmost and bottommost lines of a page."""
        if not fragments:
            return []
        top = min(f.rect.y0 for f in fragments)
        bottom = max(f.rect.y1 for f in fragments)
        return [
            f
            for f in fragments
            if f.rect.y0 <= top + 1 or f.rect.y1 >= bottom - 1
        ]

    # Running headers and footers repeat at the edge of most pages.
    repeats: Counter[tuple[int, str]] = Counter()
    for fragments in candidates:
        repeats.update({key(f) for f in edges(fragments)})
    threshold = max(3, len(pages) / 2)
    return [
        [
            f
            for f in fragments
            if not (repeats[key(f)] >= threshold and f in edges(fragments))
        ]
        for fragments in candidates
    ]


def _gutter(pages: list[list[Fragment]]) -> float | None:
    """Return the middle of a two-column layout's gutter, if there is one."""
    lines = [
        f.rect for fragments in pages for f in fragments if len(f.text) >= 20
    ]
    if len(lines) < 10:
        return None
    left = min(int(r.x0) for r in lines)
    right = max(int(r.x1) + 1 for r in lines)
    coverage = [0] * (right - left + 2)
    for rect in lines:
        coverage[int(rect.x0) - left] += 1
        coverage[int(rect.x1) + 1 - left] -= 1
    total = 0
    for x in range(len(coverage)):
        total += coverage[x]
        coverage[x] = total
    width = right - left
    threshold = max(1, len(lines) // 50)
    best: tuple[int, int] | None = None
    start = None
    for x in range(int(width * 0.3), int(width * 0.7) + 1):
        if coverage[x] <= threshold:
            start = x if start is None else start
            if best is None or x - start > best[1] - best[0]:
                best = (start, x)
        else:
            start = None
    if best is None or best[1] - best[0] < 4:
        return None
    return left + (best[0] + best[1] + 1) / 2


def _edge(values: list[float], *, low: bool) -> float:
    """Return the outermost value that at least two pages agree on.

    A text edge is the same on every page that has text there, while floats
    and stray marks move it on single pages.
    """
    ordered = sorted(values, reverse=not low)
    needed = min(2, len(ordered))
    for value in ordered:
        if sum(abs(v - value) <= 2 for v in ordered) >= needed:
            return value
    return ordered[0]


def _layout(
    pages: list[list[Fragment]], body_size: float, gutter: float | None
) -> Layout:
    """Measure the text area from the body text of ``pages``."""
    groups: list[list[list[Fragment]]] = [[], []]
    for fragments in pages:
        if gutter is None:
            groups[0].append(fragments)
            groups[1].append(fragments)
        else:
            groups[0].append([f for f in fragments if f.rect.x1 <= gutter])
            groups[1].append([f for f in fragments if f.rect.x0 >= gutter])
    used = [fragments for fragments in pages if fragments]
    if not used:
        raise FigSpaceError(_NO_TEXT)
    left_pages = [fs for fs in groups[0] if fs]
    right_pages = [fs for fs in groups[1] if fs]
    if gutter is not None and (not left_pages or not right_pages):
        gutter = None
        left_pages = right_pages = used
    area = pymupdf.Rect(
        _edge([min(f.rect.x0 for f in fs) for fs in left_pages], low=True),
        _edge([min(f.rect.y0 for f in fs) for fs in used], low=True),
        _edge([max(f.rect.x1 for f in fs) for fs in right_pages], low=False),
        _edge([max(f.rect.y1 for f in fs) for fs in used], low=False),
    )
    if gutter is None:
        return Layout(body_size, area)
    inner = (
        _edge([max(f.rect.x1 for f in fs) for fs in left_pages], low=False),
        _edge([min(f.rect.x0 for f in fs) for fs in right_pages], low=True),
    )
    if not area.x0 < inner[0] < inner[1] < area.x1:
        return Layout(body_size, area)
    return Layout(body_size, area, inner)


def _rows(fragments: list[Fragment], layout: Layout) -> list[Row]:
    """Join fragments on one baseline, such as a run-in heading and its text."""
    rows: list[Row] = []
    sides: list[int | None] = []
    for fragment in sorted(fragments, key=lambda f: f.rect.x0):
        rect = fragment.rect
        side = layout.side(rect)
        for i, row in enumerate(rows):
            tolerance = 0.4 * min(rect.height, row.rect.height)
            if (
                sides[i] == side
                and abs(fragment.size - row.size) <= 0.5
                and abs((rect.y0 + rect.y1) - (row.rect.y0 + row.rect.y1)) / 2
                <= tolerance
                and -1 <= rect.x0 - row.rect.x1 <= 3 * fragment.size
            ):
                row.rect |= rect
                row.text = f"{row.text} {fragment.text}"
                if len(fragment.text) > len(row.text) / 2:
                    row.size, row.bold = fragment.size, fragment.bold
                break
        else:
            rows.append(
                Row(
                    pymupdf.Rect(rect),
                    fragment.text,
                    fragment.size,
                    fragment.bold,
                )
            )
            sides.append(side)
    rows.sort(key=lambda r: (r.rect.y0, r.rect.x0))
    return rows


def _graphics(page: pymupdf.Page, area: pymupdf.Rect) -> list[pymupdf.Rect]:
    """Return visible drawings and images inside the text area."""
    page_area = page.rect.width * page.rect.height
    bounds = pymupdf.Rect(area.x0 - 4, area.y0 - 4, area.x1 + 4, area.y1 + 4)
    graphics = []
    seen = set()
    for kind, box in page.get_bboxlog():
        if not kind.endswith(_GRAPHIC_KINDS) or kind.startswith("clip"):
            continue
        x0, y0, x1, y1 = box
        if (x1 - x0) * (y1 - y0) > 0.8 * page_area:
            continue  # A page background.
        # Clip by hand: Rect intersection drops zero-height rules.
        x0, y0 = max(x0, bounds.x0), max(y0, bounds.y0)
        x1, y1 = min(x1, bounds.x1), min(y1, bounds.y1)
        key = (round(x0, 1), round(y0, 1), round(x1, 1), round(y1, 1))
        if x1 < x0 or y1 < y0 or key in seen:
            continue
        seen.add(key)
        graphics.append(pymupdf.Rect(x0, y0, x1, y1))
    return graphics


def _mark_body(
    rows: list[Row],
    graphics: list[pymupdf.Rect],
    layout: Layout,
    page_rect: pymupdf.Rect,
) -> None:
    """Flag the rows of body text and headings, which floats end at.

    These are rows outside any graphic that are either larger than body
    text (headings and titles), justified body text, or the last line of a
    paragraph of justified body text.
    """
    mask = Image.new("1", (int(page_rect.width) + 1, int(page_rect.height) + 1))
    draw = ImageDraw.Draw(mask)
    for rect in graphics:
        draw.rectangle(
            (
                rect.x0,
                rect.y0,
                max(rect.x0, rect.x1 - 1),
                max(rect.y0, rect.y1 - 1),
            ),
            fill=1,
        )

    def in_graphic(rect: pymupdf.Rect) -> bool:
        box = tuple(int(v) for v in rect)
        if box[2] <= box[0] or box[3] <= box[1]:
            return False
        crop = mask.crop(box)
        return crop.histogram()[-1] > 0.3 * crop.width * crop.height

    size = layout.body_size
    previous: dict[int | None, Row] = {}  # Last body row in each column.
    for row in rows:
        side = layout.side(row.rect)
        if row.size >= 1.15 * size:
            row.body = not in_graphic(row.rect)
            continue
        if abs(row.size - size) > 0.3 or len(row.text) < 3:
            continue
        column = layout.span(row.rect)
        indent = row.rect.x0 - column.x0
        justified = -2 <= indent <= 2.5 * size and (
            -6 <= column.x1 - row.rect.x1 <= 2
        )
        above = previous.get(side)
        last_line = (
            abs(indent) <= 2
            and above is not None
            and -0.4 * size <= row.rect.y0 - above.rect.y1 <= 0.6 * size
        )
        if (justified or last_line) and not in_graphic(row.rect):
            row.body = True
            previous[side] = row


def _find_stop(rows: dict[int, list[Row]], layout: Layout) -> Stop | None:
    for index in sorted(rows):
        ordered = sorted(
            rows[index], key=lambda r: (layout.side(r.rect) == 1, r.rect.y0)
        )
        for row in ordered:
            if _END_HEADING.fullmatch(row.text.strip()):
                return Stop(index, pymupdf.Rect(row.rect), row.text.strip())
    return None


def read_document(
    document: pymupdf.Document, page_limit: int | None = None
) -> Document:
    """Analyze the layout of ``document`` up to where its main body ends."""
    pages = [_fragments(page) for page in document]
    body_size = _body_size(pages)
    body = _body_fragments(pages, body_size)
    gutter = _gutter(body)
    # Rows only need the gutter, which is known before the text area.
    columns = Layout(
        body_size, pymupdf.Rect(), None if gutter is None else (gutter, gutter)
    )
    last = len(pages) if page_limit is None else min(page_limit, len(pages))
    stop = _find_stop(
        {i: _rows(pages[i], columns) for i in range(last)}, columns
    )
    counted = range(last if stop is None else stop.page + 1)
    layout = _layout([body[i] for i in counted], body_size, gutter)
    result = Document(len(pages), layout, stop=stop)
    area = layout.text_area
    for index in counted:
        page = document[index]
        # Drop margin text, such as line numbers in review copies.
        inside = [
            f
            for f in pages[index]
            if f.rect.x1 > area.x0 + 1 and f.rect.x0 < area.x1 - 1
        ]
        rows = _rows(inside, layout)
        graphics = _graphics(page, layout.text_area)
        _mark_body(rows, graphics, layout, page.rect)
        result.rows[index] = rows
        result.graphics[index] = graphics
    if stop is not None:
        for row in result.rows[stop.page]:
            if row.text.strip() == stop.text and row.rect.intersects(stop.rect):
                stop.rect = pymupdf.Rect(row.rect)
                break
    return result
