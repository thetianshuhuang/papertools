"""A tiny built site and config shared by the anondoc tests."""

import json
import zlib
from pathlib import Path

import pytest

INDEX_HTML = """\
<!doctype html>
<html lang="en" class="no-js">
<head>
  <meta charset="utf-8">
  <link rel="canonical" href="https://acme.github.io/proj/">
  <link rel="describedby" href="llms.txt">
  <title>Proj* docs</title>
</head>
<body>
  <a href="https://github.com/Acme/proj" class="md-source">\
<svg viewBox="0 0 1 1"><path d="M0"/></svg>Acme/proj</a>
  <p>Made by Acme&nbsp;Lab. See
    <a href="https://acme.github.io/lib/api/#x">lib</a>,
    <a href="https://github.com/Acme/tool">tool</a>,
    <a href="https://github.com/Acme/secret/tree/main">the code</a>, and
    <a href="https://acme.github.io/proj/guide/#setup">the guide</a>.</p>
  <p>Plain: https://github.com/Acme/other and https://example.com/x.</p>
  <details class="mkdocstrings-source">
    <summary>Source code in <code>src/x.py</code></summary>
    <pre>def f(): pass  # Acme</pre>
  </details>
</body>
</html>
"""

GUIDE_HTML = """\
<!doctype html>
<html><head><link rel="canonical" href="https://acme.github.io/proj/guide/">
</head><body>
<a href="https://acme.github.io/proj/">Home</a>
<a href="../">Up</a>
<h2 id="setup">Setup for acme users</h2>
</body></html>
"""

INDEX_MD = """\
# Proj\\* docs

By [Acme](https://github.com/Acme/secret) using \
[lib](<https://acme.github.io/lib/>).

<https://github.com/Acme/secret>

[ref]: https://github.com/Acme/secret
"""

SEARCH = {
    "config": {"lang": ["en"]},
    "items": [
        {
            "location": "",
            "title": "Proj* docs",
            "text": "<p>By Acme.</p> Source code in <code>src/x.py</code> "
            "<pre><code>def f(): pass</code></pre>",
        }
    ],
}

CONFIG = """\
terms:
  Acme Lab: Anonymous Lab
  Acme:
  Proj*: Anon
links:
  - match: https://acme.github.io/lib/
    action: keep
  - match: https://github.com/Acme/tool
    replace: https://pypi.org/project/tool/
strip:
  - selector: link[rel=canonical]
  - selector: .md-source
  - name: source
    selector: details.mkdocstrings-source
  - name: source
    pattern: '(?s)Source code in <code>.*?</code></pre>'
    files: [search.json]
json_links:
  search.json: [location]
json_scripts:
  - from: search.json
    to: search.js
    template: "var __index = {json};"
"""


@pytest.fixture
def site(tmp_path) -> Path:
    """Build a small Zensical-like site containing identifying content."""
    root = tmp_path / "site"
    (root / "guide").mkdir(parents=True)
    (root / "index.html").write_text(INDEX_HTML)
    (root / "guide/index.html").write_text(GUIDE_HTML)
    (root / "index.md").write_text(INDEX_MD)
    (root / "search.json").write_text(json.dumps(SEARCH))
    (root / "llms.txt").write_text("# Acme\n")
    (root / "404.html").write_text('<a href="/proj/">Acme</a>')
    (root / "sitemap.xml").write_text(
        "<urlset><url><loc>https://acme.github.io/proj/</loc></url></urlset>"
    )
    (root / "objects.inv").write_bytes(
        b"# Sphinx inventory version 2\n# Project: Proj*\n# Version: 0\n"
        b"# The remainder of this file is compressed using zlib.\n"
        + zlib.compress(b"proj py:module 1 index.html#$ -\n")
    )
    return root


@pytest.fixture
def config_file(tmp_path) -> Path:
    """Write the config matching :func:`site`."""
    path = tmp_path / "anondoc.yaml"
    path.write_text(CONFIG)
    return path
