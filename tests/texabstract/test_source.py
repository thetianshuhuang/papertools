"""Tests for reading a paper's files."""

import pytest

from papertools.texabstract.convert import extract
from papertools.texabstract.errors import TexAbstractError
from papertools.texabstract.source import find_mains, load

ABSTRACT = "\\begin{abstract}\nText.\n\\end{abstract}\n"


def test_nested_inputs(paper):
    r"""Inline \input and \include, with or without .tex, in subdirectories."""
    root = paper(
        **{
            "main.tex": "A \\input{sections/a} B \\input sections/b.tex C",
            "sections__a.tex": "[a \\include{sections/c}]",
            "sections__b.tex": "[b]",
            "sections__c.tex": "[c] % comment",
        }
    )
    doc = load(root / "main.tex")
    assert doc.text == "A [a [c] ] B [b] C"
    assert doc.warnings == []


def test_missing_and_cyclic_inputs(paper):
    """Warn about missing inputs and files that include themselves."""
    root = paper(
        **{
            "main.tex": "\\input{glyphtounicode}\\input{a}\\input{figs/#1}",
            "a.tex": "a\\input{a}",
        }
    )
    doc = load(root / "main.tex")
    assert doc.text == "a\\input{figs/#1}"
    assert doc.warnings == [
        "cannot find \\input{glyphtounicode}",
        "a includes itself; skipped",
    ]


def test_local_styles(paper):
    """Find style files next to the main file."""
    root = paper(
        **{
            "main.tex": "\\usepackage[x]{amsmath,macros}\\usepackage{cvpr}",
            "macros.sty": "",
            "cvpr.sty": "",
        }
    )
    styles = load(root / "main.tex").styles
    assert [s.name for s in styles] == ["macros.sty", "cvpr.sty"]


def test_style_macros_and_precedence(paper):
    """Use style-file macros, after the builtins and the paper's own."""
    root = paper(
        **{
            "main.tex": "\\usepackage{macros}\\newcommand{\\b}{paper}\n"
            "\\begin{abstract}\\a, \\b, \\eg\\end{abstract}",
            "macros.sty": "\\newcommand{\\a}{sty}\\newcommand{\\b}{sty}\n"
            "\\def\\eg{\\emph{e.g}\\onedot}\\def\\onedot{\\ifx\\fi}",
        }
    )
    assert extract(root / "main.tex").text == "sty, paper, e.g."


def test_find_mains(paper):
    r"""Find main files in a directory by their \documentclass."""
    root = paper(
        **{
            "main.tex": "\\documentclass{article}",
            "old.tex": "% \\documentclass{article}",
            "intro.tex": "",
        }
    )
    assert find_mains(root) == [root / "main.tex"]
    assert find_mains(root / "intro.tex") == [root / "intro.tex"]


def test_directory_picks_paper_with_abstract(paper):
    """Skip main files without an abstract, such as notes."""
    root = paper(
        **{
            "main.tex": "\\documentclass{article}" + ABSTRACT,
            "notes.tex": "\\documentclass{article}",
        }
    )
    result = extract(root)
    assert result.main.name == "main.tex"
    assert result.text == "Text."


@pytest.mark.parametrize(
    ("files", "message"),
    [
        ({}, "no .tex file with \\\\documentclass"),
        (
            {"a.tex": "\\documentclass{x}", "b.tex": "\\documentclass{x}"},
            r"none has an abstract in .* \(a.tex, b.tex\)",
        ),
        (
            {
                "a.tex": "\\documentclass{x}" + ABSTRACT,
                "b.tex": "\\documentclass{x}" + ABSTRACT,
            },
            r"several papers have an abstract in .* \(a.tex, b.tex\)",
        ),
    ],
)
def test_directory_errors(paper, files, message):
    """Explain why no main file could be chosen."""
    with pytest.raises(TexAbstractError, match=message):
        extract(paper(**files))


def test_abstract_command(paper):
    r"""Also accept \abstract{...}, as some classes use."""
    root = paper(**{"main.tex": "\\abstract{Short {\\em text}.}\n"})
    assert extract(root / "main.tex").text == "Short text."


def test_swallowed_space_warning(paper):
    r"""Warn when a macro swallows the space after it, outside math."""
    root = paper(
        **{
            "main.tex": "\\newcommand{\\sys}{S}\\newcommand{\\x}{x\\xspace}\n"
            "\\newcommand{\\ex}{E}\n\\begin{abstract}\\sys is, \\sys\\ is, "
            "\\sys{} is, \\x is, $\\ex x$, \\sys: \\sys\n is\\end{abstract}",
        }
    )
    result = extract(root / "main.tex")
    assert result.text == "Sis, S is, S is, x is, Ex, S: Sis"
    assert result.warnings == [
        r"\sys swallows the space before 'is'; write \sys\ or \sys{} to keep it"
    ]
