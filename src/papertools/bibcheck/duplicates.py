"""Entries that cite the same work: same key, title, DOI, link, or act."""

from collections import Counter
from urllib.parse import urlparse

from .bibtex import Entry
from .issues import WARN, Issue
from .links import entry_urls, eu_celex
from .resolve import Resolution, entry_arxiv_id, entry_doi
from .text import norm_title


def duplicates(entries) -> dict[str, list[Issue]]:
    """Duplicate keys and duplicate titles within the file."""
    out: dict[str, list[Issue]] = {}
    keys = Counter(e.key for e in entries)
    titles: dict[str, list[str]] = {}
    for e in entries:
        if t := norm_title(e.fields.get("title", "")):
            titles.setdefault(t, []).append(e.key)
    for e in entries:
        found = []
        if keys[e.key] > 1:
            found.append(Issue(WARN, "key", "defined more than once"))
        same = [
            k for k in titles.get(norm_title(e.fields.get("title", "")), [])
        ]
        if len(same) > 1 and e.key == same[0]:
            others = ", ".join(k for k in same[1:])
            found.append(Issue(WARN, "key", f"same paper as {others}"))
        if found:
            out[e.key] = found
    return out


def _norm_url(url: str) -> str:
    parts = urlparse(url.strip())
    host = parts.netloc.lower().removeprefix("www.")
    path = parts.path.rstrip("/")
    query = f"?{parts.query}" if parts.query else ""
    return f"{host}{path}{query}"


def identities(entry: Entry, res: Resolution) -> list[tuple[str, str]]:
    """What identifies the work an entry cites: (identity, description).

    Its DOI and arXiv id (written or found), the record it matched, its
    links (written or suggested), and the EU act it cites.
    """
    out = []
    dois = {entry_doi(entry)}
    for r in (res.primary, res.published):
        if r and not r.hint_only:
            dois.add(r.doi)
            out.append((f"record:{r.source}:{r.id}", f"both match {r.label}"))
    for doi in sorted(d.lower() for d in dois if d):
        out.append((f"doi:{doi}", f"same DOI {doi}"))
    if aid := entry_arxiv_id(entry):
        out.append((f"arxiv:{aid}", f"same arXiv id {aid}"))
    urls = entry_urls(entry)
    link = res.link
    if link and link.suggestion:
        urls.append(link.suggestion.url)
    for url in urls:
        out.append((f"url:{_norm_url(url)}", f"same link {url}"))
    if celex := eu_celex(entry, urls):
        out.append((f"eu:{celex}", f"same EU act (CELEX {celex})"))
    return out


class Duplicates:
    """Spots entries that cite the same work as an earlier entry.

    Duplicate titles are caught before any lookup (:func:`duplicates`);
    this catches the rest once entries are resolved: same DOI, arXiv id,
    matched record, link, or EU act, even when the titles differ.
    """

    def __init__(self):
        self.seen: dict[str, tuple[str, str]] = {}  # identity -> key, title

    def check(self, entry: Entry, res: Resolution) -> list[Issue]:
        """Warnings for ``entry`` if an earlier entry is the same work."""
        title = norm_title(entry.fields.get("title", ""))
        found: dict[str, str] = {}
        ids = identities(entry, res)
        for ident, why in ids:
            if ident not in self.seen:
                continue
            key, other_title = self.seen[ident]
            # Same key is "defined more than once"; same title is reported
            # by duplicates().
            if key != entry.key and not (title and title == other_title):
                found.setdefault(key, why)
        for ident, _ in ids:
            self.seen.setdefault(ident, (entry.key, title))
        return [
            Issue(WARN, "key", f"same work as {key} ({why})")
            for key, why in found.items()
        ]
