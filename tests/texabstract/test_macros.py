"""Macro definition and expansion tests."""

import pytest

from papertools.texabstract.errors import TexAbstractError


@pytest.mark.parametrize(
    "preamble",
    [
        r"\newcommand{\sysname}{Name}",
        r"\newcommand\sysname{Name}",
        r"\newcommand*{\sysname}{Name}",
        r"\def\sysname{Name}",
        r"\DeclareRobustCommand{\sysname}{Name}",
    ],
)
def test_definition_forms(convert, preamble):
    """Read each way of defining a macro without arguments."""
    text, _ = convert(r"\sysname: a dataset. \sysname\ is big.", preamble)
    assert text == "Name: a dataset. Name is big."


def test_space_after_macro_is_swallowed(convert):
    """Keep TeX's behavior, so the text matches the PDF."""
    text, _ = convert(r"\sysname is", r"\newcommand{\sysname}{X}")
    assert text == "Xis"


def test_arguments_and_defaults(convert):
    """Substitute arguments, including an optional one."""
    preamble = (
        r"\newcommand{\pair}[2]{(#1, #2)}"
        r"\newcommand{\opt}[2][def]{#1/#2}"
        r"\def\twice#1{#1#1}"
    )
    text, _ = convert(
        r"\pair{a}{b} \pair xy \opt{c} \opt[o]{c} \twice{ab}", preamble
    )
    assert text == "(a, b) (x, y) def/c o/c abab"


def test_nested_macros(convert):
    """Expand macros whose bodies use other macros and formatting."""
    preamble = r"\newcommand{\name}{Foo}\newcommand{\full}{\textbf{\name}Net}"
    assert convert(r"\full", preamble)[0] == "FooNet"


def test_redefinitions_apply_in_order(convert):
    r"""A later \renewcommand wins, but \providecommand doesn't override."""
    preamble = (
        r"\newcommand{\x}{one}\renewcommand{\x}{two}\providecommand{\x}{three}"
        r"\providecommand{\y}{four}"
    )
    assert convert(r"\x \y", preamble)[0] == "twofour"


def test_commented_and_local_definitions_ignored(convert):
    """Skip definitions in comments and inside groups."""
    preamble = (
        "\\newcommand{\\x}{kept}\n"
        "% \\renewcommand{\\x}{commented}\n"
        "{\\renewcommand{\\x}{local}}\n"
        "\\twocolumn[{\\renewcommand\\x[1][]{#1}}]"
    )
    assert convert(r"\x", preamble)[0] == "kept"


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        (r"\sys is new.", "Name is new."),
        (r"\sys, and", "Name, and"),
        (r"\sys. And", "Name. And"),
        (r"(\sys)", "(Name)"),
        (r"\sys\ is", "Name is"),
        (r"\textbf{\sys} is", "Name is"),
    ],
)
def test_xspace(convert, body, expected):
    r"""Add a space after \xspace only before words."""
    preamble = r"\newcommand{\sys}{Name\xspace}"
    assert convert(body, preamble)[0] == expected


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        (r"models, \eg ViT", "models, e.g. ViT"),
        (r"models (\eg, ViT)", "models (e.g., ViT)"),
        (r"and so on \etc.", "and so on etc."),
        (r"Smith \etal show", "Smith et al. show"),
    ],
)
def test_builtin_abbreviations(convert, body, expected):
    """Expand cvpr.sty-style abbreviations without doubling periods."""
    assert convert(body)[0] == expected


def test_paper_definition_overrides_builtin(convert):
    """A definition in the paper beats the builtin abbreviation."""
    assert convert(r"\eg", r"\newcommand{\eg}{for example}")[0] == (
        "for example"
    )


def test_runaway_recursion(convert):
    """Stop expanding a macro that refers to itself."""
    with pytest.raises(TexAbstractError, match="does not terminate"):
        convert(r"\a", r"\def\a{x\a}")


def test_internal_definitions_ignored(convert):
    r"""Skip layout code with \@ internals or conditionals."""
    preamble = (
        r"\newcommand{\name}{X}"
        r"\renewcommand\paragraph{\@startsection{paragraph}{4}{\z@}{8pt}}"
        r"\def\name{\ifx\a\b Y\else Z\fi}"
        r"\newcommand{\etal}{et al.\@\xspace}"
    )
    text, warnings = convert(r"\name \paragraph{Title} \etal", preamble)
    assert text == "XTitle et al."
    assert warnings == [r"unknown macro \paragraph; dropped"]
