"""BibTeX reader tests."""

import pytest

from papertools.bibcheck.bibtex import format_entry, parse_bib, parse_entry
from papertools.bibcheck.errors import BibCheckError

BIB = r"""@String(CVPR = {IEEE Conf. Comput. Vis. Pattern Recog.})
@string{proc = "Proc. "}
@comment{ignored @article{nope, title={x}} }
@preamble{"\newcommand{\x}{y}"}

@inproceedings{a2022,
  title = {Nested {Braces} and "quotes"},
  author = "Doe, Jane and {\"O}zt{\"u}rk, Ali",
  booktitle = proc # CVPR,
  year = 2022,
  month = jun,
}
@article ( b2023 , title={B}, journal={J} )
"""


def test_parse_macros_values_and_spans():
    """Resolve macros and concatenation; keep exact spans."""
    entries = parse_bib(BIB)
    assert [e.key for e in entries] == ["a2022", "b2023"]
    a = entries[0]
    assert a.type == "inproceedings"
    assert a.fields["title"] == 'Nested {Braces} and "quotes"'
    assert a.fields["author"] == r"Doe, Jane and {\"O}zt{\"u}rk, Ali"
    assert (
        a.fields["booktitle"] == "Proc. IEEE Conf. Comput. Vis. Pattern Recog."
    )
    assert a.fields["year"] == "2022"
    assert a.fields["month"] == "Jun"
    for e in entries:
        assert BIB[e.start : e.end] == e.text
    assert entries[1].text.endswith(")")


def test_undefined_macro_is_kept_verbatim():
    """BibTeX only warns about undefined macros, and so do we."""
    e = parse_entry("@misc{k, month=June, title={T}}")
    assert e.fields["month"] == "June"


@pytest.mark.parametrize(
    "text", ["@misc{k, title={unbalanced}", '@misc{k, title="open}', "@misc{k}"]
)
def test_malformed_input(text):
    """Report malformed entries with a line number."""
    with pytest.raises(BibCheckError, match="line 1"):
        parse_bib(text)


def test_format_round_trip():
    """Formatted entries parse back to the same fields."""
    fields = {"title": "{ABC}v2", "year": "2024"}
    e = parse_entry(format_entry("article", "k", fields))
    assert (e.type, e.key, e.fields) == ("article", "k", fields)
