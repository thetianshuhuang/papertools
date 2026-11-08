"""Per-format adapters that find links and blocks for the Rewriter."""

import json
import posixpath
import re
from typing import Any

from .config import matches_any
from .htmledit import HtmlEditor
from .rewrite import Rewriter

HTML_EXTENSIONS = {".html", ".htm"}
MARKDOWN_EXTENSIONS = {".md", ".markdown", ".txt"}

# [text](url "title"), [text](<url>), and ![alt](url).
_MD_LINK = re.compile(
    r"(?P<image>!?)\[(?P<text>(?:[^\[\]\\]|\\.|\[[^\]]*\])*)\]"
    r"\((?P<url><[^>\n]*>|[^)\s]+)(?:\s+(?:\"[^\"]*\"|'[^']*'))?\)"
)
_MD_AUTOLINK = re.compile(r"<(?P<url>https?://[^>\s]+)>", re.IGNORECASE)
_MD_REFDEF = re.compile(
    r"^ {0,3}\[[^\]]+\]:[ \t]*(?P<url><[^>\n]*>|\S+).*(?:\n|$)", re.MULTILINE
)


def _extension(path: str) -> str:
    return posixpath.splitext(path)[1].lower()


def _patterns(text: str, path: str, rw: Rewriter) -> str:
    for rule in rw.config.strip:
        if rule.pattern is not None and rule.applies_to(path):
            text, n = rule.pattern.subn("", text)
            rw.stats.stripped[rule.label] += n
    return text


_HREF = re.compile(r"(<[a-z][^<>]*?\shref\s*=\s*)([\"'])((?:(?!\2).)*)\2", re.I)


def rewrite_html(
    text: str, path: str, rw: Rewriter, *, fragment: bool = False
) -> str:
    """Strip selectors and unwrap or remove links, then the text pass.

    Args:
        text: The page, or an HTML fragment from a JSON string.
        path: Site-relative path of the file.
        rw: The rewriter.
        fragment: The text is a fragment, whose links are not relative to
            ``path``, so directory links are left alone.

    """
    rel_dir = posixpath.dirname(path)
    editor = HtmlEditor(text)
    for rule in rw.config.strip:
        if rule.selector is None or not rule.applies_to(path):
            continue
        for element in editor.soup.select(rule.selector):
            editor.delete(element)
            rw.stats.stripped[rule.label] += 1
    for anchor in editor.soup.find_all("a", href=True):
        action = rw.link_action(str(anchor["href"]), rel_dir)
        if action == "unwrap":
            editor.unwrap(anchor)
            rw.stats.links["unwrapped"] += 1
        elif action == "remove":
            editor.delete(anchor)
            rw.stats.links["removed"] += 1
    # <link rel=describedby href=llms.txt> and the like.
    for link in editor.soup.find_all("link", href=True):
        if rw.link_action(str(link["href"]), rel_dir):
            editor.delete(link)
    text = rw.text_pass(_patterns(editor.result(), path, rw), rel_dir)
    if rw.config.file_urls and not fragment:
        out_dir = rw.rename(rel_dir)
        text = _HREF.sub(
            lambda m: (
                m.group(1)
                + m.group(2)
                + rw.file_url(m.group(3), out_dir)
                + m.group(2)
            ),
            text,
        )
    return text


def rewrite_markdown(text: str, path: str, rw: Rewriter) -> str:
    """Unwrap or remove Markdown links, then the text pass."""
    rel_dir = posixpath.dirname(path)
    text = _patterns(text, path, rw)

    def action(url: str) -> str | None:
        act = rw.link_action(url.strip("<>"), rel_dir)
        if act:
            rw.stats.links["unwrapped" if act == "unwrap" else "removed"] += 1
        return act

    def link(m: re.Match[str]) -> str:
        act = action(m.group("url"))
        if act == "unwrap":
            return m.group("text")
        return "" if act == "remove" else m.group()

    def bare(m: re.Match[str]) -> str:
        return m.group() if action(m.group("url")) is None else ""

    text = _MD_LINK.sub(link, text)
    text = _MD_AUTOLINK.sub(bare, text)
    text = _MD_REFDEF.sub(bare, text)
    return rw.text_pass(text, rel_dir)


def rewrite_plain(text: str, path: str, rw: Rewriter) -> str:
    """Strip patterns, then the text pass (bare URLs and terms only)."""
    return rw.text_pass(_patterns(text, path, rw), posixpath.dirname(path))


def rewrite_json(text: str, path: str, rw: Rewriter) -> str:
    """Rewrite each string in a JSON document, keeping it valid JSON.

    Strings containing tags (such as the search index's page text) are
    treated as HTML fragments; other strings as plain text. With
    ``file_urls``, values of the config's ``json_links`` keys point at files.
    """
    try:
        data = json.loads(text)
    except ValueError:
        return rewrite_plain(text, path, rw)
    link_keys = set()
    if rw.config.file_urls:
        for glob, keys in rw.config.json_links.items():
            if matches_any(path, (glob,)):
                link_keys.update(keys)
    out_dir = rw.rename(posixpath.dirname(path))

    def walk(value: Any) -> Any:
        if isinstance(value, str):
            if re.search(r"<[a-z/!]", value, re.IGNORECASE):
                return rewrite_html(value, path, rw, fragment=True)
            return rewrite_plain(value, path, rw)
        if isinstance(value, list):
            return [walk(v) for v in value]
        if isinstance(value, dict):
            out = {rw.replace_terms(k): walk(v) for k, v in value.items()}
            for key in link_keys:
                if isinstance(out.get(key), str):
                    out[key] = rw.file_url(out[key], out_dir, root=True)
            return out
        return value

    return json.dumps(walk(data), ensure_ascii=False, separators=(",", ":"))


def rewrite_text(text: str, path: str, rw: Rewriter) -> str:
    """Rewrite a text file, choosing the adapter by extension."""
    ext = _extension(path)
    if ext in HTML_EXTENSIONS:
        return rewrite_html(text, path, rw)
    if ext in MARKDOWN_EXTENSIONS:
        return rewrite_markdown(text, path, rw)
    if ext == ".json":
        return rewrite_json(text, path, rw)
    return rewrite_plain(text, path, rw)


def decode(data: bytes) -> str | None:
    """Decode a text file, or ``None`` for binary data."""
    if b"\0" in data[:8192]:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None
