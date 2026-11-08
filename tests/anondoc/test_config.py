"""Config parsing and validation tests."""

import re

import pytest

from papertools.anondoc.config import (
    DEFAULT_SKIP,
    DEFAULT_STRIP,
    JsonScript,
    load_config,
    matches_any,
    parse_config,
    prefix_matches,
)
from papertools.anondoc.errors import AnonDocError


def test_terms_mapping_and_list():
    """Null replacements use the placeholder; lists of names are accepted."""
    config = parse_config({"placeholder": "X", "terms": {"Acme": None, "B": 1}})
    assert config.terms == {"Acme": "X", "B": "1"}
    config = parse_config({"terms": ["Acme", {"Proj": "Anon"}]})
    assert config.terms == {"Acme": "anonymous", "Proj": "Anon"}


def test_defaults_are_replaced():
    """Defaults apply unless skip or strip is given, which replaces them."""
    config = parse_config({"terms": ["a"]})
    assert config.skip == DEFAULT_SKIP
    assert config.strip == DEFAULT_STRIP
    assert config.file_urls and not config.json_links
    config = parse_config(
        {
            "terms": ["a"],
            "skip": "*.md",
            "strip": [
                {"name": "button", "selector": ".md-source"},
                {"pattern": "x+", "files": "*.json"},
            ],
        }
    )
    assert config.skip == ("*.md",)
    assert [(r.label, r.selector) for r in config.strip] == [
        ("button", ".md-source"),
        ("x+", None),
    ]
    assert config.strip[1].pattern.pattern == "x+"
    assert config.strip[1].files == ("*.json",)
    assert parse_config({"terms": ["a"], "strip": []}).strip == ()


def test_json_links_and_scripts():
    """JSON link keys and script copies are read from the config."""
    config = parse_config(
        {
            "terms": ["a"],
            "json_links": {"search.json": ["location"]},
            "json_scripts": [
                {
                    "from": "/search.json",
                    "to": "search.js",
                    "template": "v={json}",
                }
            ],
        }
    )
    assert config.json_links == {"search.json": ("location",)}
    assert config.json_scripts == (
        JsonScript("search.json", "search.js", "v={json}"),
    )


def test_link_rules_and_site_url():
    """Link rules accept actions or a replacement URL."""
    config = parse_config(
        {
            "terms": ["a"],
            "site_url": "https://x.io/p/index.html#top",
            "links": [
                {"match": "https://x.io/lib", "action": "keep"},
                {"match": "https://x.io/t", "replace": "https://pypi.org/t"},
            ],
        }
    )
    assert config.site_url == "https://x.io/p/"
    assert [r.action for r in config.links] == ["keep", "replace"]
    assert config.links[1].replace == "https://pypi.org/t"


@pytest.mark.parametrize(
    ("data", "message"),
    [
        ([], "must be a YAML mapping"),
        ({}, "needs a 'terms'"),
        ({"terms": {}}, "terms is empty"),
        ({"terms": {"": None}}, "cannot be empty"),
        ({"terms": ["Acme", "ACME"]}, "are the same"),
        ({"terms": ["a"], "colour": 1}, "unknown key(s) colour"),
        ({"terms": ["a"], "links": [{"match": "x"}]}, "action must be"),
        ({"terms": ["a"], "links": [{"action": "keep"}]}, "needs a 'match'"),
        (
            {
                "terms": ["a"],
                "links": [{"match": "x", "action": "keep", "replace": "y"}],
            },
            "conflicts",
        ),
        ({"terms": ["a"], "strip": ["nope"]}, "must be a mapping"),
        ({"terms": ["a"], "json_links": ["x"]}, "map file globs"),
        (
            {"terms": ["a"], "json_scripts": [{"from": "a"}]},
            "needs template, to",
        ),
        (
            {
                "terms": ["a"],
                "json_scripts": [{"from": "a", "to": "b", "template": "x"}],
            },
            "must contain {json}",
        ),
        ({"terms": ["a"], "strip": [{"pattern": "("}]}, "bad pattern"),
        ({"terms": ["a"], "strip": [{"selector": "a[["}]}, "bad selector"),
        ({"terms": ["a"], "strip": [{}]}, "exactly one of"),
        ({"terms": ["a"], "file_urls": "no"}, "true or false"),
        ({"terms": ["a"], "skip": {"a": 1}}, "list of strings"),
    ],
)
def test_invalid_configs(data, message):
    """Reject malformed configs with a readable message."""
    with pytest.raises(AnonDocError, match=re.escape(message)):
        parse_config(data)


def test_load_config_errors(tmp_path):
    """Report unreadable files and invalid YAML."""
    with pytest.raises(AnonDocError, match="Cannot read config"):
        load_config(tmp_path / "missing.yaml")
    bad = tmp_path / "bad.yaml"
    bad.write_text("terms: [unclosed\n")
    with pytest.raises(AnonDocError, match="Invalid YAML"):
        load_config(bad)


def test_prefix_matches_whole_segments():
    """Prefixes match at path-segment boundaries, ignoring case."""
    assert prefix_matches("https://x.io/a", "https://X.io/a")
    assert prefix_matches("https://x.io/a", "https://x.io/a/b")
    assert prefix_matches("https://x.io/a", "https://x.io/a#c")
    assert not prefix_matches("https://x.io/a", "https://x.io/ab")
    assert prefix_matches("https://x.io/a/", "https://x.io/a/b")


def test_matches_any_gitignore_style():
    """Globs without a slash match file names anywhere."""
    assert matches_any("a/b/llms.txt", ("llms.txt",))
    assert not matches_any("a/llms.txt", ("b/llms.txt",))
    assert matches_any("a/x.md", ("a/*.md",))
