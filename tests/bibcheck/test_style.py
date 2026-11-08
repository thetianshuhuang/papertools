"""Bibliography style detection tests."""

from papertools.bibcheck.style import detect

SENTENCE_BST = """FUNCTION {format.title}
{ title empty$
    { "" }
    { title "t" change.case$ }
  if$
}
"""
KEEP_BST = """FUNCTION {format.title}
{ title empty$ { "" } { title } if$ }
% { title "t" change.case$ }   commented out, as in ACM-Reference-Format
FUNCTION {format.edition} { edition "t" change.case$ }
"""


def paper(tmp_path, tex, bst=None):
    """A paper directory with main.tex, main.bib, and an optional style."""
    (tmp_path / "main.tex").write_text(tex)
    (tmp_path / "main.bib").write_text("")
    if bst:
        (tmp_path / "mystyle.bst").write_text(bst)
    return tmp_path / "main.bib"


def test_sentence_case_style(tmp_path):
    """A local .bst that lowercases titles."""
    bib = paper(tmp_path, r"\bibliographystyle{mystyle}", SENTENCE_BST)
    assert detect(bib) == (True, "mystyle.bst (sentence-cases titles)")


def test_keep_case_style_ignores_comments(tmp_path):
    """Commented-out code and other fields don't count."""
    bib = paper(tmp_path, r"\bibliographystyle{mystyle}", KEEP_BST)
    assert detect(bib)[0] is False


def test_style_in_subdirectory_and_commented(tmp_path):
    """Find the style in sections/, skipping commented-out lines."""
    bib = paper(tmp_path, r"% \bibliographystyle{nope}", SENTENCE_BST)
    (tmp_path / "sections").mkdir()
    (tmp_path / "sections/refs.tex").write_text(r"\bibliographystyle{mystyle}")
    assert detect(bib)[0] is True


def test_biblatex_and_missing(tmp_path):
    """Biblatex keeps case; no style means keep case."""
    bib = paper(tmp_path, r"\usepackage[style=ieee]{biblatex}")
    assert detect(bib) == (False, "biblatex (keeps title case)")
    (tmp_path / "main.tex").write_text("no style here")
    assert detect(bib)[0] is False
    (tmp_path / "main.tex").write_text(r"\bibliographystyle{doesnotexist123}")
    assert detect(bib) == (False, "doesnotexist123.bst not found")
