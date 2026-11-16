"""Syntax errors (located, explained, skipped) and LaTeX/BibTeX warnings."""

import json

import pytest

from papertools.bibcheck.bibtex import (
    BibSyntaxError,
    parse_bib,
    parse_bib_lenient,
    parse_entry,
)
from papertools.bibcheck.cli import main
from papertools.bibcheck.syntax import lint, unescaped

# The case that prompted this: {{CISA and others}, missing a brace.
RUNAWAY = """@techreport{ok1,
  author = {Doe, Jane}, title = {Fine}, institution = {NIST}, year = {2022}
}

@misc{cisa2023securebydesign,
  author       = {{CISA and others},
  title        = {Shifting the Balance of Cybersecurity Risk},
  year         = {2023},
  note         = {Accessed: 2026-11-11}
}


@article{ok2,
  author = {Boehm, Barry}, title = {Software Defect Reduction},
  journal = {Computer}, year = {2001}
}
"""


def _error(text: str) -> BibSyntaxError:
    with pytest.raises(BibSyntaxError) as exc:
        parse_bib(text)
    return exc.value


def test_runaway_brace_is_reported_where_it_starts():
    """Not at the end of the file: at the field with the missing brace."""
    e = _error(RUNAWAY)
    assert (e.line, e.column) == (6, 18)
    assert (e.key, e.field) == ("cisa2023securebydesign", "author")
    assert "runs on into the next field (title =) on line 7" in e.message
    assert "opens 2 '{' and closes 1 '}'" in e.hint
    assert e.excerpt.splitlines() == [
        "  author       = {{CISA and others},",
        "                 ^",
    ]


def test_broken_entries_are_skipped_and_the_rest_parsed():
    """Lenient parsing resumes at the next entry."""
    entries, errors = parse_bib_lenient(RUNAWAY)
    assert [e.key for e in entries] == ["ok1", "ok2"]
    assert len(errors) == 1
    assert errors[0].as_dict()["key"] == "cisa2023securebydesign"


@pytest.mark.parametrize(
    ("text", "where", "message", "hint"),
    [
        (
            "@misc{a, title={X}\n  year={2020}}",
            (1, 19),
            "a comma is missing after the title field",
            "add ',' here, before year =",
        ),
        (
            "@misc{a, title={X},\n@misc{b, title={Y}}",
            (2, 1),
            "a new entry starts before the previous one is closed",
            "add '}' at the end of the previous entry",
        ),
        (
            "@misc{a, title {X}}",
            (1, 16),
            "'=' is missing after the field name 'title'",
            "write title = {...}",
        ),
        (
            "@misc{a title={X}}",
            (1, 7),
            "cannot read the key of this @misc",
            "the key can't contain spaces",
        ),
        (
            "@misc{a, title={X}",
            (1, 19),
            "the file ends inside this entry",
            "close the entry with '}'",
        ),
        (
            "@misc{a, title={X}, note={unclosed}",
            (1, 36),
            "the file ends inside this entry",
            "",
        ),
        (
            '@misc{a, title="open}',
            (1, 16),
            "this quoted value is never closed",
            "add the closing '\"'",
        ),
        (
            "@misc{a, title={open}, note={x\n  year = 2020",
            (1, 29),
            "this '{' is never closed",
            "add the missing '}'",
        ),
        (
            "@misc{a, title=, year={2020}}",
            (1, 16),
            "expected a value, found ','",
            "write the value in {braces}",
        ),
    ],
)
def test_common_mistakes(text, where, message, hint):
    """Each mistake is located and explained, with a fix."""
    e = _error(text)
    assert (e.line, e.column) == where
    assert message in e.message
    assert hint in e.hint


def test_error_text_shows_line_and_caret():
    """str() is what the CLI and website print."""
    text = str(_error("@misc{a, title={X}\n  year={2020}}"))
    assert text == (
        "line 1, column 19, entry a, field title: a comma is missing after "
        "the title field\n"
        "    @misc{a, title={X}\n"
        "                      ^\n"
        "  hint: add ',' here, before year ="
    )


def test_multiline_values_are_not_runaways():
    """A value spanning lines is fine when it doesn't swallow a field."""
    e = parse_entry(
        "@misc{a,\n  title = {Secure Software Development Framework:\n"
        "           Recommendations for Mitigating Risk},\n  year = {2022}}"
    )
    assert e.fields["year"] == "2022"


# --- Warnings ----------------------------------------------------------------


def test_duplicate_fields_and_undefined_strings():
    """What BibTeX warns about while reading is kept on the entry."""
    e = parse_entry(
        "@misc{a, title={X}, title={Y}, booktitle=CVPR, month=feb, year=2020}"
    )
    assert e.fields["title"] == "X"  # BibTeX keeps the first.
    assert e.problems == [
        "field title is given twice; BibTeX uses the first, biber stops "
        "with an error: remove one",
        "@string 'CVPR' is not defined, so the word 'CVPR' is used as the "
        "value; define it with @string or write {CVPR}",
    ]


@pytest.mark.parametrize(
    ("value", "found"),
    [
        ("Wiley & Sons", ["unescaped &"]),
        (r"Wiley \& Sons", []),
        ("50% faster", ["unescaped %"]),
        ("C# programs", ["unescaped #"]),
        ("snake_case names", ["unescaped _"]),
        ("$x_1$ and $y^2$", []),
        (r"see \url{https://a.org/x_y%20z#q}", []),
        ("costs $5", ["unbalanced $"]),
        (r"costs \$5", []),
    ],
)
def test_unescaped_characters(value, found):
    """Characters that break LaTeX, outside math and URLs."""
    got = unescaped(value)
    assert [g.split(":")[0] for g in got] == found


def test_lint_required_fields_and_text_fields_only():
    """Missing required fields; URLs and DOIs may contain anything."""
    e = parse_entry(
        "@article{a, title={Fast & Furious}, author={Doe, J.},"
        " url={https://x.org/a_b%20c}, doi={10.1000/a_b}}"
    )
    assert lint(e) == [
        (
            "syntax",
            "title: unescaped &: write \\& (a bare & breaks LaTeX: "
            "'Misplaced alignment tab')",
        ),
        (
            "fields",
            "@article requires journal, year; BibTeX leaves it out "
            "of the reference and warns",
        ),
    ]
    book = parse_entry(
        "@book{b, editor={E}, title={T}, publisher={P}, year={2020}}"
    )
    assert lint(book) == []  # author OR editor
    assert lint(parse_entry("@online{c, title={T}}")) == []  # Not BibTeX's


# --- CLI and website ------------------------------------------------------


def test_cli_reports_syntax_errors_and_checks_the_rest(web, tmp_path, capsys):
    """Broken entries are listed first; the rest are checked; exit 1."""
    path = tmp_path / "refs.bib"
    path.write_text(RUNAWAY)
    assert main([str(path), "--no-links"]) == 1
    out = capsys.readouterr().out
    assert out.startswith(
        "1 syntax error; this entry was skipped:\n"
        "  line 6, column 18, entry cisa2023securebydesign, field author:"
    )
    assert "Checked 2 entries:" in out
    assert "1 skipped (syntax errors)" in out


def test_cli_json_lists_syntax_errors(web, tmp_path, capsys):
    """--json has them under syntax_errors, counted in the summary."""
    path = tmp_path / "refs.bib"
    path.write_text(RUNAWAY)
    main([str(path), "--no-links", "--json"])
    report = json.loads(capsys.readouterr().out)
    assert report["summary"]["syntax_errors"] == 1
    error = report["syntax_errors"][0]
    assert (error["line"], error["key"], error["field"]) == (
        6,
        "cisa2023securebydesign",
        "author",
    )
    assert [e["key"] for e in report["entries"]] == ["ok1", "ok2"]


def test_cli_only_errors(tmp_path, capsys):
    """A file with nothing readable exits 1 with every error."""
    path = tmp_path / "refs.bib"
    path.write_text("@misc{a title={X}}\n@misc{b, title {Y}}")
    assert main([str(path)]) == 1
    err = capsys.readouterr().err
    assert "cannot read the key" in err
    assert "'=' is missing" in err
