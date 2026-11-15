"""Build small papers with a supplement, outline, links, and page labels."""

from pathlib import Path

import pikepdf
import pymupdf
import pytest

BODY = "lorem ipsum dolor sit amet sed do eiusmod tempor incididunt ut labore"


def marker(page: int) -> str:
    """Return the text that identifies a page (one-based) of a paper."""
    return f"Marker of page {page}."


def make_paper(
    path: Path,
    *,
    pages: int = 6,
    heading: tuple[int, str] | None = (4, "Supplementary Material"),
    heading_style: str = "heading",
    anchors: dict[str, int] | None = None,
    toc: list[list] | None = None,
    links: list[tuple[int, int]] = (),
    labels: list[dict] | None = None,
) -> Path:
    """Write a paper and return its path.

    Pages are zero-based in the arguments. ``heading`` puts a line of text at
    the top of a page, styled as a heading or as body text; ``anchors`` adds
    named destinations, as hyperref does; ``links`` adds links from one page
    to another.
    """
    document = pymupdf.open()
    for index in range(pages):
        page = document.new_page(width=612, height=792)
        y = 100.0
        if heading is not None and heading[0] == index:
            if heading_style == "heading":
                page.insert_text(
                    (72, y), heading[1], fontsize=14, fontname="tibo"
                )
            else:
                page.insert_text(
                    (72, y), heading[1], fontsize=10, fontname="tiro"
                )
            y += 30
        page.insert_text((72, y), marker(index + 1), fontname="tiro")
        for _ in range(20):
            y += 14
            page.insert_text((72, y), BODY, fontsize=10, fontname="tiro")
    for origin, target in links:
        document[origin].insert_link(
            {
                "kind": pymupdf.LINK_GOTO,
                "from": pymupdf.Rect(72, 700, 200, 712),
                "page": target,
                "to": pymupdf.Point(72, 100),
            }
        )
    if toc:
        document.set_toc(toc)
    if labels:
        document.set_page_labels(labels)
    document.save(path)
    document.close()
    if anchors:
        with pikepdf.open(path, allow_overwriting_input=True) as pdf:
            names = pikepdf.Array()
            for name, index in sorted(anchors.items()):
                names.append(pikepdf.String(name))
                names.append(
                    pikepdf.Array(
                        [pdf.pages[index].obj, pikepdf.Name.XYZ, 72, 700, 0]
                    )
                )
            pdf.Root.Names = pikepdf.Dictionary(
                Dests=pikepdf.Dictionary(Names=names)
            )
            pdf.save(path)
    return path


@pytest.fixture
def paper(tmp_path):
    """Return a factory for papers; see ``make_paper`` for its arguments."""

    def make(**kwargs) -> Path:
        return make_paper(tmp_path / "paper.pdf", **kwargs)

    return make


@pytest.fixture
def outputs(tmp_path):
    """Return the paths for the main and supplement PDFs."""
    return tmp_path / "out-main.pdf", tmp_path / "out-supp.pdf"
