"""Name, title, and venue normalization tests."""

import pytest

from papertools.bibcheck.text import (
    count_accents,
    delatex,
    first_last,
    norm_pages,
    norm_title,
    protect,
    split_authors,
    venue_family,
)


def test_delatex_accents_and_symbols():
    """Decode the LaTeX accent forms Scholar and publishers emit."""
    assert delatex(r"J{\'e}gou, Herv\'{e}") == "Jégou, Hervé"
    assert delatex(r"G\"{o}del and Bj\"orn") == "Gödel and Björn"
    assert delatex(r"\L{}ukasz, Fran\c cois, Ga\v{s}per") == (
        "Łukasz, François, Gašper"
    )
    assert delatex(r"{\o}ystein and \'{\i}") == "øystein and í"


def test_norm_title_ignores_case_braces_and_accents():
    """Titles compare equal across case, braces, and accents."""
    assert norm_title("{DINO}v2: Learning") == norm_title("Dinov2: learning")
    assert norm_title(r"Caf\'e") == norm_title("Café") == "cafe"


@pytest.mark.parametrize(
    "name,expected",
    [
        ("Dollár, Piotr", "Piotr Dollár"),
        ("Yi Wang 0001", "Yi Wang"),
        ("von Neumann, Jr, John", "John von Neumann Jr"),
        ("Kaiming He", "Kaiming He"),
    ],
)
def test_first_last(name, expected):
    """Normalize BibTeX and DBLP name forms."""
    assert first_last(name) == expected


def test_split_authors():
    """Split on 'and' and decode accents."""
    assert split_authors(r"Dollar, Piotr and J{\'e}gou, Herv{\'e}") == [
        "Piotr Dollar",
        "Hervé Jégou",
    ]


def test_count_accents():
    """Count diacritics and special letters."""
    assert count_accents("Hervé Jégou") == 2
    assert count_accents("Herve Jegou") == 0
    assert count_accents("Łukasz") == 1


@pytest.mark.parametrize(
    "venue",
    [
        "{IEEE Conf. Comput. Vis. Pattern Recog.}",
        "CVPR",
        "2022 IEEE/CVF Conference on Computer Vision and Pattern Recognition "
        "(CVPR)",
        "Proceedings of the IEEE/CVF Conference on Computer Vision and "
        "Pattern Recognition",
    ],
)
def test_venue_family_cvpr(venue):
    """All spellings of CVPR map to one family."""
    assert venue_family(venue) == "CVPR"


def test_venue_family_others():
    """Distinguish workshops, arXiv, and unknown venues."""
    assert venue_family("CVPR Workshops") == "CVPRW"
    assert venue_family("arXiv preprint arXiv:2301.08243") == "arXiv"
    assert venue_family("Int. Conf. Learn. Represent.") == "ICLR"
    assert venue_family("Trans. Mach. Learn. Res. 2024") == "TMLR"
    assert venue_family("IEEE intelligent systems") is None


def test_protect_and_pages():
    """Brace mixed-case words; normalize page dashes."""
    assert protect("A ConvNet for the 2020s") == "A {ConvNet} for the {2020s}"
    assert protect("{DINO}v2: Learning") == "{DINO}v2: Learning"
    assert protect("K-Radar: 4D Radar") == "{K-Radar:} {4D} Radar"
    assert protect("Self-Supervised Learning") == "Self-Supervised Learning"
    assert norm_pages("15979–15988") == "15979--15988"
    assert norm_pages("1 - 5") == "1--5"
