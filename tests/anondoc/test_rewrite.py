"""Term, link, and self-link decisions shared by every format."""

import pytest

from papertools.anondoc.config import parse_config
from papertools.anondoc.rewrite import Rewriter


@pytest.fixture
def rw() -> Rewriter:
    """A rewriter with terms, a keep rule, and a replace rule."""
    config = parse_config(
        {
            "terms": {"Acme Lab": "Anon Lab", "Acme": None, "A&M": "U"},
            "links": [
                {"match": "https://acme.io/lib/", "action": "keep"},
                {
                    "match": "https://github.com/Acme/t",
                    "replace": "https://p/t",
                },
                {"match": "https://github.com/Acme/x", "action": "unwrap"},
            ],
        }
    )
    return Rewriter(config, "https://acme.io/proj/")


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("by ACME and acme", "by anonymous and anonymous"),
        ("Acme Lab", "Anon Lab"),
        ("Acme&nbsp;Lab, Acme\n  Lab", "Anon Lab, Anon Lab"),
        ("Texas A&amp;M, A\\&M, A&#38;M", "Texas U, U, U"),
    ],
)
def test_terms(rw, text, expected):
    """Terms match case-insensitively, longest first, in escaped forms."""
    assert rw.text_pass(text, "") == expected


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://acme.io/lib/api/#Acme", "https://acme.io/lib/api/#Acme"),
        ("https://github.com/Acme/t", "https://p/t"),
        ("https://github.com/Acme/x/blob", ""),
        ("https://github.com/Acme/y", "https://github.com/anonymous/y"),
        ("https://example.com/a.", "https://example.com/a."),
        ("https://acme.io/proj/guide/#s", "../guide/#s"),
        ("https://acme.io/proj", "../"),
        ("https://acme.io/proj/acme.md", "../anonymous.md"),
    ],
)
def test_bare_urls(rw, url, expected):
    """Bare URLs follow the link rules; self-links become relative."""
    assert rw.text_pass(f"see {url}", "sub") == f"see {expected}"


@pytest.mark.parametrize(
    ("path", "rel_dir", "expected"),
    [
        ("", "", "./"),
        ("", "a/b", "../../"),
        ("a/", "a", "./"),
        ("a/x.md", "", "a/x.md"),
        ("b/#top", "a", "../b/#top"),
    ],
)
def test_relativize(rw, path, rel_dir, expected):
    """Site-relative paths are made relative to the file's directory."""
    assert rw.relativize(path, rel_dir) == expected


def test_link_action(rw):
    """Links with link text are unwrapped by rule, default, or skip."""
    assert rw.link_action("https://github.com/Acme/x", "") == "unwrap"
    assert rw.link_action("https://github.com/Acme/other", "") == "unwrap"
    assert rw.stats.unruled == {"https://github.com/Acme/other"}
    assert rw.link_action("https://acme.io/lib/", "") is None
    assert rw.link_action("https://acme.io/proj/llms.txt", "") == "unwrap"
    assert rw.link_action("../llms.txt", "a") == "unwrap"
    assert rw.link_action("guide/", "") is None
    assert rw.link_action("#acme", "") is None


def test_find_terms_ignores_shielded_urls(rw):
    """Kept and replacement URLs may contain terms without being leaks."""
    text = "https://acme.io/lib/x https://p/t/acme acme"
    assert [m.start() for m in rw.find_terms(text)] == [text.rindex("acme")]


def test_rename_and_counts(rw):
    """Paths are renamed with the same replacements, without counting."""
    assert rw.rename("acme/Acme Lab.png") == "anonymous/Anon Lab.png"
    assert not rw.stats.terms
    rw.replace_terms("acme ACME Acme Lab")
    assert rw.stats.terms == {"Acme": 2, "Acme Lab": 1}


@pytest.mark.parametrize(
    ("href", "rel_dir", "root", "expected"),
    [
        ("../cli/", "a", False, "../cli/index.html"),
        ("./..", "a", False, "./../index.html"),
        ("../", "a", False, "../index.html"),
        ("cli/#x", "", False, "cli/index.html#x"),
        ("cli?q=1", "", False, "cli/index.html?q=1"),
        ("cli/index.html", "", False, "cli/index.html"),
        ("img/", "", False, "img/"),  # No index.html there.
        ("#x", "", False, "#x"),
        ("#x", "", True, "index.html#x"),
        ("", "", True, "index.html"),
        ("https://acme.io/", "", False, "https://acme.io/"),
        ("/abs/", "", False, "/abs/"),
    ],
)
def test_file_url(rw, href, rel_dir, root, expected):
    """Directory links point at their index.html when it exists."""
    rw.files = {"index.html", "cli/index.html", "a/x.html", "img/a.png"}
    assert rw.file_url(href, rel_dir, root=root) == expected
