"""Finding the supplement and writing the two parts."""

import re

import pikepdf
import pymupdf
import pytest

from papertools.supp_split.errors import SuppSplitError, UsageError
from papertools.supp_split.split import find_start, split


def _start(path):
    with pymupdf.open(path) as document:
        return find_start(document)


def _markers(path):
    """Return the original (one-based) number of each page of ``path``."""
    with pymupdf.open(path) as document:
        return [
            int(re.search(r"Marker of page (\d+)", page.get_text())[1])
            for page in document
        ]


def _labels(path):
    """Return the page label rules, read with pikepdf.

    PyMuPDF misreads prefixes stored as hex strings, as LaTeX writes them.
    """
    with pikepdf.open(path) as pdf:
        labels = pdf.Root.get("/PageLabels")
        if labels is None:
            return []
        return [
            (
                int(page),
                str(rule.get("/P", "")),
                str(rule.get("/S", "")),
                int(rule.get("/St", 1)),
            )
            for page, rule in pikepdf.NumberTree(labels).items()
        ]


def test_heading(paper):
    """Find a heading set larger than the body text."""
    start = _start(paper())
    assert start.page == 4
    assert start.reason == 'heading "Supplementary Material"'


@pytest.mark.parametrize(
    "text", ["Appendix", "A Appendix", "Appendix A: Proofs", "Appendices"]
)
def test_heading_variants(paper, text):
    """Accept the usual appendix headings."""
    assert _start(paper(heading=(3, text))).page == 3


def test_body_text_is_not_a_heading(paper):
    """Ignore a line of body text that happens to read like a heading."""
    path = paper(heading=(2, "Appendix"), heading_style="body")
    with pytest.raises(SuppSplitError, match="pass --page"):
        _start(path)


def test_anchor_beats_heading(paper):
    r"""Prefer hyperref's \appendix anchor to heading text."""
    start = _start(paper(anchors={"appendix.B": 5, "appendix.A": 3}))
    assert start.page == 3
    assert start.reason == 'hyperref anchor "appendix.A"'


def test_not_found(paper):
    """Ask for --page when nothing marks the supplement."""
    with pytest.raises(SuppSplitError, match="pass --page"):
        _start(paper(heading=None))


def test_split_pages(paper, outputs):
    """Write the pages before the supplement and the pages from it on."""
    main, supp = outputs
    result = split(paper(), main, supp)
    assert result.start.page == 4
    assert result.warnings == []
    assert _markers(main) == [1, 2, 3, 4]
    assert _markers(supp) == [5, 6]


def test_page_overrides(paper, outputs):
    """Split at --page instead of the detected start."""
    main, supp = outputs
    result = split(paper(anchors={"appendix.A": 4}), main, supp, page=3)
    assert result.start.reason == "--page"
    assert _markers(main) == [1, 2]
    assert _markers(supp) == [3, 4, 5, 6]


@pytest.mark.parametrize("page", [0, 1, 7])
def test_page_out_of_range(paper, outputs, page):
    """Reject a --page that would leave a part empty."""
    with pytest.raises(UsageError, match="between 2 and 6"):
        split(paper(), *outputs, page=page)


def test_toc(paper, outputs):
    """Keep each part's own outline entries, with renumbered pages."""
    main, supp = outputs
    toc = [
        [1, "Introduction", 1],
        [1, "Method", 3],
        [2, "Details", 4],
        [1, "Proofs", 5],
        [2, "Lemma", 6],
    ]
    split(paper(toc=toc), main, supp)
    with pymupdf.open(main) as document:
        assert [e[1:] for e in document.get_toc()] == [
            ["Introduction", 1],
            ["Method", 3],
            ["Details", 4],
        ]
    with pymupdf.open(supp) as document:
        assert [e[1:] for e in document.get_toc()] == [
            ["Proofs", 1],
            ["Lemma", 2],
        ]


def test_links(paper, outputs):
    """Keep links within a part and drop those into the other part."""
    main, supp = outputs
    split(paper(links=[(0, 2), (1, 5), (4, 0), (5, 4)]), main, supp)
    with pymupdf.open(main) as document:
        links = [
            (p.number, link["page"]) for p in document for link in p.links()
        ]
        assert links == [(0, 2)]
    with pymupdf.open(supp) as document:
        links = [
            (p.number, link["page"]) for p in document for link in p.links()
        ]
        assert links == [(1, 0)]


def test_named_destinations(paper, outputs):
    """Keep each part's named destinations only."""
    main, supp = outputs
    split(paper(anchors={"appendix.A": 4, "section.1": 0}), main, supp)
    with pymupdf.open(main) as document:
        assert set(document.resolve_names()) == {"section.1"}
    with pymupdf.open(supp) as document:
        assert document.resolve_names()["appendix.A"]["page"] == 0


def test_labels_reset(paper, outputs):
    """Keep a supplement's own numbering, such as S1, S2, ..."""
    main, supp = outputs
    labels = [
        {"startpage": 0, "prefix": "", "style": "D", "firstpagenum": 1},
        {"startpage": 4, "prefix": "S", "style": "D", "firstpagenum": 1},
    ]
    split(paper(labels=labels), main, supp)
    assert _labels(main) == [(0, "", "/D", 1)]
    assert _labels(supp) == [(0, "S", "/D", 1)]


def test_labels_continue(paper, outputs):
    """Continue the main text's numbering if the supplement doesn't reset."""
    main, supp = outputs
    labels = [
        {"startpage": 0, "prefix": "", "style": "r", "firstpagenum": 1},
        {"startpage": 1, "prefix": "", "style": "D", "firstpagenum": 1},
        {"startpage": 5, "prefix": "", "style": "A", "firstpagenum": 1},
    ]
    split(paper(labels=labels), main, supp)
    assert _labels(main) == [(0, "", "/r", 1), (1, "", "/D", 1)]
    assert _labels(supp) == [(0, "", "/D", 4), (1, "", "/A", 1)]
    with pymupdf.open(supp) as document:
        assert [page.get_label() for page in document] == ["4", "A"]


def test_no_labels(paper, outputs):
    """Add no page labels to a paper that has none."""
    main, supp = outputs
    split(paper(), main, supp)
    assert _labels(main) == _labels(supp) == []


def test_bibliography_on_start_page(paper, outputs):
    """Warn when the bibliography ends on the supplement's first page."""
    path = paper(anchors={"appendix.A": 4, "cite.smith": 4, "cite.doe": 3})
    result = split(path, *outputs)
    assert len(result.warnings) == 1
    assert (
        "page 5 also holds the end of the bibliography" in (result.warnings[0])
    )


def test_unreadable(tmp_path, outputs):
    """Report a file that is not a PDF."""
    path = tmp_path / "bad.pdf"
    path.write_text("not a pdf")
    with pytest.raises(SuppSplitError, match="Cannot read PDF"):
        split(path, *outputs)


def test_single_page(paper, outputs):
    """Refuse a PDF with nothing to split."""
    with pytest.raises(SuppSplitError, match="fewer than two pages"):
        split(paper(pages=1, heading=None), *outputs)
