"""Format adapters: HTML splicing, Markdown links, and JSON strings."""

import json

import pytest

from papertools.anondoc.config import parse_config
from papertools.anondoc.formats import decode, rewrite_text
from papertools.anondoc.htmledit import HtmlEditor


@pytest.fixture
def rw():
    """A rewriter with one term, link rules, and the source preset."""
    from papertools.anondoc.rewrite import Rewriter

    config = parse_config(
        {
            "terms": ["Acme"],
            "links": [
                {"match": "https://acme.io/lib/", "action": "keep"},
                {"match": "https://github.com/Acme/gone", "action": "remove"},
            ],
            "strip": [
                {"selector": "link[rel=canonical]"},
                {"selector": ".md-source"},
                {"selector": "details.mkdocstrings-source"},
                {
                    "pattern": r"(?s)Source code in <code>[^<]*</code>\s*"
                    r"<pre><code>.*?</code></pre>",
                    "files": ["search.json"],
                },
                {"pattern": r"(?s)```.*?```\n", "files": ["*.md"]},
            ],
            "json_links": {"search.json": ["location"]},
        }
    )
    return Rewriter(config, "https://acme.io/proj/")


def test_html_edits_leave_other_bytes_alone(rw):
    """Only the edited elements change; markup elsewhere is untouched."""
    page = (
        '<!doctype html><html><head><meta charset="utf-8">'
        '<link rel="canonical" href="https://acme.io/proj/a/"></head><body>'
        '<a href="https://github.com/Acme/p" class="md-source">Acme/p</a>'
        '<svg viewBox="0 0 1 1"><path d="M0"/></svg>'
        '<p>See <a href="https://github.com/Acme/q" title="x">the <b>code'
        '</b></a> and <a href="https://github.com/Acme/gone">this</a>.</p>'
        '<a class="k" href="https://acme.io/lib/x">lib</a>'
        '<a href="https://acme.io/proj/b/">b</a></body></html>'
    )
    out = rewrite_text(page, "a/index.html", rw)
    assert out == (
        '<!doctype html><html><head><meta charset="utf-8">'
        "</head><body>"
        '<svg viewBox="0 0 1 1"><path d="M0"/></svg>'
        "<p>See the <b>code</b> and .</p>"
        '<a class="k" href="https://acme.io/lib/x">lib</a>'
        '<a href="../b/">b</a></body></html>'
    )
    assert rw.stats.links == {
        "unwrapped": 1,
        "removed": 1,
        "kept": 1,
        "relativized": 1,
    }
    assert rw.stats.unruled == {"https://github.com/Acme/q"}


def test_html_file_urls(rw):
    """Directory links in pages, not in JSON fragments, get index.html."""
    rw.files = {"index.html", "b/index.html"}
    page = "<a href='b/'>b</a><a class=x href=\"./\">c</a>"
    assert rewrite_text(page, "index.html", rw) == (
        "<a href='b/index.html'>b</a><a class=x href=\"./index.html\">c</a>"
    )
    data = {"location": "b/#x", "text": '<a href="b/">b</a>'}
    out = json.loads(rewrite_text(json.dumps(data), "search.json", rw))
    assert out == {"location": "b/index.html#x", "text": '<a href="b/">b</a>'}


def test_html_source_preset(rw):
    """The mkdocstrings source block is removed with its contents."""
    page = (
        "<div><p>Doc</p>\n<details class='mkdocstrings-source'>\n"
        "<summary>Source code in <code>x.py</code></summary>\n"
        "<pre><code>acme()</code></pre></details>\n</div>"
    )
    assert rewrite_text(page, "index.html", rw) == "<div><p>Doc</p>\n\n</div>"


def test_html_implicitly_closed_elements():
    """Spans follow bs4's nesting for unclosed tags."""
    editor = HtmlEditor("<ul><li>a<li>b</ul><p>c</p>")
    editor.delete(editor.soup.select("li")[0])
    assert editor.result() == "<ul></ul><p>c</p>"


def test_markdown_links(rw):
    """Inline links, autolinks, and reference definitions follow the rules."""
    text = (
        "By [Acme](https://github.com/Acme/x) with [lib](<https://acme.io/lib/>)"
        ' and ![logo](https://github.com/Acme/gone/l.png "t").\n'
        "<https://github.com/Acme/gone>\n"
        "[home](https://acme.io/proj/guide/)\n"
        "[r]: https://github.com/Acme/gone\n"
        "```\nacme\n```\n"
        "Proj\\* end\n"
    )
    assert rewrite_text(text, "sub/index.md", rw) == (
        "By anonymous with [lib](<https://acme.io/lib/>) and .\n\n"
        "[home](../guide/)\n"
        "Proj\\* end\n"
    )


def test_json_stays_valid(rw):
    """Each string is rewritten; HTML fragments are stripped too."""
    data = {
        "items": [
            {
                "location": "acme/#acme.x",
                "text": "<p>Acme</p> Source code in <code>a.py</code> "
                "<pre><code>x &lt; y</code></pre>",
            }
        ],
        "acme": 1,
    }
    out = json.loads(rewrite_text(json.dumps(data), "search.json", rw))
    assert out == {
        "items": [
            {"location": "anonymous/#anonymous.x", "text": "<p>anonymous</p> "}
        ],
        "anonymous": 1,
    }


def test_decode_binary():
    """NUL bytes or invalid UTF-8 mean binary."""
    assert decode(b"caf\xc3\xa9") == "café"
    assert decode(b"\x89PNG\0") is None
    assert decode(b"\xff\xfe") is None
