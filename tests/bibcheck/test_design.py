"""The core's structure: issue kinds, checkers, sources, sessions."""

import json

from papertools.bibcheck.bibtex import parse_entry
from papertools.bibcheck.checks import CHECKS, check
from papertools.bibcheck.issues import Issue, IssueKind
from papertools.bibcheck.links import ManualLink, manual_links
from papertools.bibcheck.resolve import Resolution
from papertools.bibcheck.session import CheckOptions, CheckSession
from papertools.bibcheck.sources import Record, Sources


def test_issue_kinds_say_whether_a_rebuild_fixes_them():
    """--fix asks the kind; strings still compare and serialize."""
    assert IssueKind.AUTHOR.rebuild_fixes
    assert not IssueKind.URL.rebuild_fixes
    assert not IssueKind.KEY.rebuild_fixes
    assert not IssueKind.SYNTAX.rebuild_fixes
    issue = Issue("warn", "title", "x")
    assert issue.kind is IssueKind.TITLE
    assert issue.kind == "title"
    assert json.dumps(issue.kind) == '"title"'


def test_unknown_issue_kind_is_rejected():
    """A typo in a kind fails where the issue is made."""
    import pytest

    with pytest.raises(ValueError):
        Issue("warn", "titel", "x")


def test_checks_run_in_order_and_skip_unindexed_entries():
    """Record checks don't run without a record; the list is the order."""
    names = [c.__name__ for c in CHECKS]
    assert names == [
        "syntax_issues",
        "bad_doi",
        "not_indexed",
        "own_link",
        "missing_doi",
        "title",
        "authors",
        "published_version",
        "metadata",
    ]
    entry = parse_entry("@misc{k, title={T}}")
    kinds = [i.kind for i in check(entry, Resolution())]
    assert kinds == [IssueKind.MISSING]


def test_hint_only_records():
    """Semantic Scholar records are hints, not authorities."""
    s2 = Record(source="s2", id="1", title="T")
    crossref = Record(source="crossref", id="10.1/x", title="T")
    assert s2.hint_only and not crossref.hint_only
    assert crossref.label == "doi 10.1/x"
    assert crossref.info.name == "Crossref"


def test_session_reports_its_phase_and_current_entry(web):
    """Front ends ask the session where it is."""
    session = CheckSession(
        "@misc{a, title={A}}\n@misc{b, title={B}}",
        CheckOptions(check_links=False, fix=True),
        Sources(web),
    )
    steps = session.steps()
    assert session.current_key == "a"
    next(steps)
    assert (session.phase, session.current_key) == ("checking", "b")
    for _ in steps:
        pass
    assert (session.phase, session.current_key) == ("done", "")


def test_manual_links_are_structured():
    """Name and URL, printed as 'name: url' in workings."""
    links = manual_links(parse_entry("@misc{k, title={T}}"), None)
    assert links[0] == ManualLink(
        "web search", "https://duckduckgo.com/?q=%22T%22"
    )
    assert str(links[0]) == "web search: https://duckduckgo.com/?q=%22T%22"
