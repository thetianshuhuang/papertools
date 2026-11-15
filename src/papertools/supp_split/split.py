"""Find where the supplementary material starts and write the two parts."""

import io
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import pikepdf
import pymupdf

from .errors import SuppSplitError, UsageError

# A heading that starts the supplement, optionally lettered ("A Appendix").
_HEADING = re.compile(
    r"(?:[A-Z]\.?\s+)?"
    r"(?:(?:supplementary|supplemental)(?:\s+materials?)?"
    r"|appendix(?:\s+[A-Z])?|appendices)"
    r"(?:\s*[:.\-–—]\s*\S.*|\s+for\s+\S.*)?",
    re.IGNORECASE,
)
_BOLD_FONT = re.compile(r"bold|black|heavy|semibold|demi", re.IGNORECASE)
# Span flag that PyMuPDF sets for bold text.
_BOLD_FLAG = 16


@dataclass
class Start:
    """Where the supplementary material starts."""

    page: int  # Zero-based.
    reason: str


@dataclass
class SplitResult:
    """The pages written to each part."""

    start: Start
    page_count: int
    warnings: list[str] = field(default_factory=list)


def _anchor_start(names: dict[str, dict]) -> Start | None:
    r"""Find the first section after ``\appendix``, from hyperref's anchors."""
    anchors = [
        (target["page"], name)
        for name, target in names.items()
        if name.startswith("appendix.") and target.get("page", -1) >= 0
    ]
    if not anchors:
        return None
    page, name = min(anchors)
    return Start(page, f'hyperref anchor "{name}"')


def _lines(page: pymupdf.Page) -> list[tuple[str, list[dict]]]:
    data = page.get_text("dict", flags=pymupdf.TEXTFLAGS_TEXT)
    return [
        ("".join(span["text"] for span in line["spans"]).strip(), line["spans"])
        for block in data["blocks"]
        for line in block.get("lines", [])
    ]


def _heading_start(document: pymupdf.Document) -> Start | None:
    """Find a "Supplementary Material" or "Appendix" heading.

    Only lines set larger or bolder than the body text count, so that a line
    of prose such as "Appendix A of the paper" is not taken for a heading.
    """
    pages = [_lines(page) for page in document]
    sizes: Counter[float] = Counter()
    for lines in pages:
        for _, spans in lines:
            for span in spans:
                sizes[round(span["size"], 1)] += len(span["text"])
    if not sizes:
        return None
    body = sizes.most_common(1)[0][0]
    for index, lines in enumerate(pages[1:], start=1):
        for text, spans in lines:
            if not _HEADING.fullmatch(text):
                continue
            styled = [s for s in spans if s["text"].strip()]
            if any(
                s["size"] > body + 0.5
                or s["flags"] & _BOLD_FLAG
                or _BOLD_FONT.search(s["font"])
                for s in styled
            ):
                return Start(index, f'heading "{text}"')
    return None


def find_start(document: pymupdf.Document) -> Start:
    """Return the page where the supplementary material starts."""
    start = _anchor_start(document.resolve_names())
    if start is None or start.page == 0:
        start = _heading_start(document)
    if start is None or start.page == 0:
        raise SuppSplitError(
            "Could not find where the supplementary material starts; "
            "pass --page N."
        )
    return start


def _label_rules(source: Path) -> list[tuple[int, dict]]:
    """Return the page label rules of ``source`` as (first page, rule)."""
    with pikepdf.open(source) as pdf:
        labels = pdf.Root.get("/PageLabels")
        if labels is None:
            return []
        return sorted(
            (int(page), {str(k): v for k, v in rule.items()})
            for page, rule in pikepdf.NumberTree(labels).items()
        )


def _part_rules(
    rules: list[tuple[int, dict]], first: int, end: int
) -> list[tuple[int, dict]]:
    """Return the page label rules for pages ``first`` to ``end`` - 1."""
    inside = [
        (page - first, rule) for page, rule in rules if first <= page < end
    ]
    if inside and inside[0][0] == 0:
        return inside
    before = [(page, rule) for page, rule in rules if page < first]
    if before:
        page, rule = before[-1]
        number = int(rule.get("/St", 1)) + first - page
        rule = {**rule, "/St": number}
    else:
        # Unlabeled pages are shown by number.
        rule = {"/S": pikepdf.Name.D, "/St": first + 1}
    return [(0, rule), *inside]


def _part(
    source: Path,
    pages: range,
    rules: list[tuple[int, dict]],
) -> bytes:
    """Return ``pages`` of ``source`` as a PDF.

    PyMuPDF trims the outline, links, and named destinations to the pages
    kept, but drops the page labels, so those are copied with pikepdf.
    """
    with pymupdf.open(source) as document:
        document.select(list(pages))
        data = document.tobytes(garbage=3, deflate=True)
    if not rules:
        return data
    with pikepdf.open(io.BytesIO(data)) as pdf:
        numbers: list = []
        for page, rule in _part_rules(rules, pages.start, pages.stop):
            numbers += [page, pikepdf.Dictionary(rule)]
        pdf.Root.PageLabels = pdf.make_indirect(
            pikepdf.Dictionary(Nums=pikepdf.Array(numbers))
        )
        output = io.BytesIO()
        pdf.save(output)
        return output.getvalue()


def split(
    source: Path, main: Path, supp: Path, *, page: int | None = None
) -> SplitResult:
    """Write the main text of ``source`` to ``main`` and the rest to ``supp``.

    ``page`` is the one-based first page of the supplement; it is found
    automatically if omitted.
    """
    try:
        document = pymupdf.open(source)
    except (pymupdf.FileDataError, RuntimeError) as exc:
        raise SuppSplitError(f"Cannot read PDF: {exc}") from exc
    with document:
        if document.needs_pass:
            raise SuppSplitError(
                "Encrypted PDFs are not supported; "
                "export an unencrypted PDF first."
            )
        count = document.page_count
        if count < 2:
            raise SuppSplitError("The PDF has fewer than two pages.")
        if page is None:
            start = find_start(document)
        elif 2 <= page <= count:
            start = Start(page - 1, "--page")
        else:
            raise UsageError(f"--page must be between 2 and {count}.")
        result = SplitResult(start, count)
        names = document.resolve_names()
    if any(
        name.startswith("cite.") and target.get("page") == start.page
        for name, target in names.items()
    ):
        result.warnings.append(
            f"page {start.page + 1} also holds the end of the bibliography, "
            "so the supplementary material starts partway down it and the "
            "whole page goes to the supplement; add \\clearpage before it, "
            "or pass --page."
        )
    rules = _label_rules(source)
    main_data = _part(source, range(0, start.page), rules)
    supp_data = _part(source, range(start.page, count), rules)
    main.write_bytes(main_data)
    supp.write_bytes(supp_data)
    return result
