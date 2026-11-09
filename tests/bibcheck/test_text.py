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
    assert delatex(r"Ren{\'e}e, Jos\'{e}") == "Renée, José"
    assert delatex(r"M\"{u}ller and J\"org") == "Müller and Jörg"
    assert delatex(r"\L{}ukasz, Fran\c cois, Ga\v{s}per") == (
        "Łukasz, François, Gašper"
    )
    assert delatex(r"{\o}ystein and \'{\i}") == "øystein and í"


def test_norm_title_ignores_case_braces_and_accents():
    """Titles compare equal across case, braces, and accents."""
    assert norm_title("{ABC}v2: Learning") == norm_title("Abcv2: learning")
    assert norm_title(r"Caf\'e") == norm_title("Café") == "cafe"


@pytest.mark.parametrize(
    "name,expected",
    [
        ("Novák, Jan", "Jan Novák"),
        ("Yi Wang 0001", "Yi Wang"),
        ("von Neumann, Jr, John", "John von Neumann Jr"),
        ("Alice Smith", "Alice Smith"),
    ],
)
def test_first_last(name, expected):
    """Normalize BibTeX and DBLP name forms."""
    assert first_last(name) == expected


def test_split_authors():
    """Split on 'and' and decode accents."""
    assert split_authors(r"Novak, Jan and Ren{\'e}e, Jos{\'e}") == [
        "Jan Novak",
        "José Renée",
    ]


def test_count_accents():
    """Count diacritics and special letters."""
    assert count_accents("José Renée") == 2
    assert count_accents("Jose Renee") == 0
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
    assert venue_family("arXiv preprint arXiv:2301.00002") == "arXiv"
    assert venue_family("Int. Conf. Learn. Represent.") == "ICLR"
    assert venue_family("Trans. Mach. Learn. Res. 2024") == "TMLR"
    assert venue_family("IEEE intelligent systems") is None


def test_protect_and_pages():
    """Brace mixed-case words; normalize page dashes."""
    assert protect("A ConvNet for the 2020s") == "A {ConvNet} for the {2020s}"
    assert protect("{ABC}v2: Learning") == "{ABC}v2: Learning"
    assert protect("X-Net: 4D Example") == "{X-Net:} {4D} Example"
    assert protect("Self-Supervised Learning") == "Self-Supervised Learning"
    assert norm_pages("15979–15988") == "15979--15988"
    assert norm_pages("1 - 5") == "1--5"
