"""Command-line behavior tests."""

import pytest

from papertools.texabstract.cli import main

MAIN = r"""\documentclass{article}
\input{preamble}
\newcommand{\name}{Name}
\begin{document}
\twocolumn[{\renewcommand\twocolumn[1][]{#1}\maketitle}]
\input{sections/abstract.tex}
\end{document}
"""
PREAMBLE = r"""
\newcommand{\TODO}[1]{\textbf{[TODO: #1]}}
% \renewcommand{\TODO}[1]{}
"""
ABSTRACT = r"""\begin{abstract}
We present \name: a method for ``examples''. \name\ is
simple (and short). It is
$2\times$ faster than the baseline.\footnote{
A footnote.
}
\end{abstract}
"""
EXPECTED = (
    "We present Name: a method for “examples”. Name is simple (and short). "
    "It is 2× faster than the baseline."
)


@pytest.fixture
def project(paper):
    """A paper with a preamble and sections in separate files."""
    return paper(
        **{
            "main.tex": MAIN,
            "preamble.tex": PREAMBLE,
            "sections__abstract.tex": ABSTRACT,
        }
    )


def test_end_to_end(project, capsys):
    """Print the abstract, and its length on stderr."""
    assert main([str(project)]) == 0
    out, err = capsys.readouterr()
    assert out == EXPECTED + "\n"
    assert err == f"19 words, {len(EXPECTED)} characters\n"


def test_math_tex(project, capsys):
    """Keep math as TeX with --math tex."""
    assert main([str(project / "main.tex"), "--math", "tex"]) == 0
    assert r"It is $2\times$ faster" in capsys.readouterr().out


def test_warnings_go_to_stderr(paper, capsys):
    """Report warnings on stderr and still succeed."""
    root = paper(**{"main.tex": "\\begin{abstract}A~\\cite{x}.\\end{abstract}"})
    assert main([str(root / "main.tex")]) == 0
    out, err = capsys.readouterr()
    assert out == "A.\n"
    assert "texabstract: warning: dropped citation \\cite{x}" in err


def test_missing_file(tmp_path, capsys):
    """Report a missing file with a failure exit status."""
    assert main([str(tmp_path / "missing.tex")]) == 1
    assert "texabstract: cannot read" in capsys.readouterr().err


def test_no_abstract(paper, capsys):
    """Report a paper without an abstract, naming missing inputs."""
    root = paper(**{"main.tex": "\\input{abstract}"})
    assert main([str(root / "main.tex")]) == 1
    err = capsys.readouterr().err
    assert "no abstract found in" in err
    assert "(could not find abstract)" in err


def test_bad_math_mode():
    """Reject unknown --math values."""
    with pytest.raises(SystemExit) as exc:
        main(["main.tex", "--math", "latex"])
    assert exc.value.code == 2


def test_help_and_version(capsys):
    """Print help and package version without requiring an input."""
    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0
    assert "texabstract" in capsys.readouterr().out
    assert main(["--version"]) == 0
    assert capsys.readouterr().out.strip() == "pt texabstract 0.1.0"
