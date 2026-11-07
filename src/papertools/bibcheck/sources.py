"""Clients for the bibliographic sources bibcheck consults.

Every lookup returns :class:`Record` objects with authors as ``First Last``
Unicode strings, so they can be compared directly.

Source notes:

- dblp.org serves scripts a JavaScript bot challenge, so DBLP records come from
  OpenReview's ``DBLP.org`` mirror instead.
- OpenReview's ``/notes?id=`` sits behind a challenge too; ``/notes/search``
  doesn't, so OpenReview is searched by title and matched by id.
- PMLR (ICML, AISTATS, CoRL) mints no DOIs and has no standalone ``.bib``
  URL; Semantic Scholar is used only to notice that such a paper was
  published, never as a BibTeX source.
- Crossref's IEEE venues read ``2022 IEEE/CVF Conference on ... (CVPR)``.
"""

import hashlib
import html
import json
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import dataclass, field
from http.client import HTTPException
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import quote, urlencode, urlparse
from urllib.request import Request, urlopen

from .. import __version__
from .bibtex import parse_entry
from .errors import BibCheckError
from .text import delatex, first_last, norm_title, split_authors

_RETRY_DELAYS = [2, 5, 10]
# Minimum seconds between requests, per host (arXiv asks for 3 s).
_MIN_INTERVAL = {"export.arxiv.org": 3.0, "api.semanticscholar.org": 1.1}
_CACHE_TTL = 7 * 24 * 3600
_ATOM = {
    "a": "http://www.w3.org/2005/Atom",
    "arxiv": "http://arxiv.org/schemas/atom",
}
ARXIV_ID = r"(\d{4}\.\d{4,5}|[a-z][a-z.-]*/\d{7})"


class SourceError(BibCheckError):
    """A source could not be reached or returned something unreadable."""


class NotFoundError(SourceError):
    """The source answered 404."""


@dataclass
class Record:
    """Bibliographic metadata from one source.

    Attributes:
        source: ``crossref`` (any DOI), ``arxiv``, ``openreview``, ``dblp``
            (OpenReview's mirror), or ``s2`` (Semantic Scholar).
        id: DOI, arXiv id, OpenReview note id, or Semantic Scholar id.
        preprint: True for arXiv/CoRR records.
        bibtex: Raw BibTeX when the source embeds it (OpenReview, DBLP).
        published_doi: For arXiv records, the DOI of the published version
            if the authors registered one.

    """

    source: str
    id: str
    title: str
    authors: list[str] = field(default_factory=list)
    venue: str = ""
    year: str = ""
    pages: str = ""
    doi: str = ""
    preprint: bool = False
    bibtex: str | None = None
    published_doi: str = ""

    @property
    def label(self) -> str:
        """Short human-readable identifier, e.g. ``doi 10.1109/...``."""
        name = {
            "crossref": "doi",
            "arxiv": "arXiv",
            "openreview": "openreview",
            "dblp": "dblp",
            "s2": "Semantic Scholar",
        }[self.source]
        return f"{name} {self.id}"

    @property
    def where(self) -> str:
        """Venue and year, e.g. ``ICLR 2024 oral``."""
        if self.year and self.year not in self.venue:
            return f"{self.venue} {self.year}".strip()
        return self.venue


class Http:
    """GET with retries, per-host throttling, and an optional disk cache."""

    def __init__(self, *, timeout: float = 30, cache_dir: Path | None = None):
        self.timeout = timeout
        self.cache_dir = cache_dir
        self._last: dict[str, float] = {}
        agent = f"papertools/{__version__}"
        # Crossref's "polite pool" is faster for clients that identify.
        if mailto := os.environ.get("CROSSREF_MAILTO"):
            agent += f" (mailto:{mailto})"
        self.user_agent = agent

    def __call__(
        self, url: str, params: dict | None = None, accept: str | None = None
    ) -> str:
        """Return the body of ``url``; raise NotFoundError on 404."""
        if params:
            url += "?" + urlencode(params)
        cached = self._cache_path(url, accept)
        if cached and cached.exists():
            if time.time() - cached.stat().st_mtime < _CACHE_TTL:
                body = cached.read_text()
                if body == "\0404":
                    raise NotFoundError(url)
                return body
        try:
            body = self._fetch(url, accept)
        except NotFoundError:
            self._store(cached, "\0404")
            raise
        self._store(cached, body)
        return body

    def _cache_path(self, url: str, accept: str | None) -> Path | None:
        if not self.cache_dir:
            return None
        digest = hashlib.sha256(f"{accept}\n{url}".encode()).hexdigest()
        return self.cache_dir / digest[:2] / digest

    @staticmethod
    def _store(path: Path | None, body: str) -> None:
        if path:
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(body)
            except OSError:
                pass  # A cache that can't be written is just a slower run.

    def _throttle(self, host: str) -> None:
        wait = _MIN_INTERVAL.get(host, 0) - (
            time.monotonic() - self._last.get(host, -1e9)
        )
        if wait > 0:
            time.sleep(wait)
        self._last[host] = time.monotonic()

    def _fetch(self, url: str, accept: str | None) -> str:
        headers = {"User-Agent": self.user_agent}
        if accept:
            headers["Accept"] = accept
        host = urlparse(url).netloc
        delays = iter(_RETRY_DELAYS)
        while True:
            self._throttle(host)
            try:
                with urlopen(
                    Request(url, headers=headers), timeout=self.timeout
                ) as r:
                    return r.read().decode("utf-8", errors="replace")
            except HTTPError as exc:
                exc.close()
                if exc.code == 404:
                    raise NotFoundError(url) from exc
                delay = next(delays, None)
                if delay is None or not (exc.code == 429 or exc.code >= 500):
                    raise SourceError(f"{host}: HTTP {exc.code}") from exc
                time.sleep(delay)
            except (OSError, HTTPException) as exc:
                raise SourceError(f"{host}: {exc}") from exc


def _json(body: str, what: str) -> dict:
    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        raise SourceError(f"{what} returned malformed JSON") from exc


def _plain(text: str) -> str:
    """Strip HTML/MathML tags and entities from a Crossref title."""
    return " ".join(html.unescape(re.sub(r"<[^>]+>", "", text)).split())


def _year_from(*texts: str) -> str:
    for t in texts:
        if m := re.search(r"\b(19|20)\d{2}\b", t or ""):
            return m[0]
    return ""


class Sources:
    """All lookups, sharing one HTTP client."""

    def __init__(self, get: Callable[..., str]):
        self.get = get

    # --- DOIs ---------------------------------------------------------------

    def doi(self, doi: str) -> Record:
        """Look up any DOI (Crossref or DataCite) via doi.org."""
        body = self.get(
            f"https://doi.org/{quote(doi, safe='/')}",
            accept="application/vnd.citationstyles.csl+json",
        )
        return self._csl(_json(body, "doi.org"))

    @staticmethod
    def _csl(m: dict) -> Record:
        title = m.get("title") or ""
        if isinstance(title, list):
            title = title[0] if title else ""
        venue = m.get("container-title") or m.get("event", {}) or ""
        if isinstance(venue, dict):
            venue = venue.get("name", "")
        if isinstance(venue, list):
            venue = venue[0] if venue else ""
        parts = (m.get("issued") or {}).get("date-parts") or [[None]]
        year = str(parts[0][0]) if parts[0] and parts[0][0] else ""
        authors = []
        for a in m.get("author") or []:
            name = a.get("literal") or a.get("name")
            if not name:
                name = " ".join(
                    p for p in (a.get("given"), a.get("family")) if p
                )
            authors.append(" ".join(name.split()))
        return Record(
            source="crossref",
            id=m.get("DOI", ""),
            title=_plain(title),
            authors=authors,
            venue=_plain(venue),
            year=year,
            pages=m.get("page", ""),
            doi=m.get("DOI", ""),
            preprint=m.get("type") in {"posted-content", "article"}
            and not venue,
        )

    def crossref_search(self, title: str) -> list[Record]:
        """Search Crossref by title."""
        body = self.get(
            "https://api.crossref.org/works",
            params={
                "query.bibliographic": delatex(title),
                "rows": 5,
                "select": "DOI,title,container-title,issued,author,type,page",
            },
        )
        items = _json(body, "Crossref")["message"]["items"]
        return [
            self._csl(it)
            for it in items
            if it.get("type") not in {"component", "peer-review", "dataset"}
        ]

    def bibtex(self, doi: str) -> str:
        """Fetch a DOI's BibTeX from doi.org content negotiation."""
        return self.get(
            f"https://doi.org/{quote(doi, safe='/')}",
            accept="application/x-bibtex",
        )

    # --- arXiv --------------------------------------------------------------

    def arxiv_id(self, arxiv_id: str) -> Record:
        """Look up an arXiv paper by id."""
        found = self._arxiv({"id_list": arxiv_id})
        if not found:
            raise NotFoundError(arxiv_id)
        return found[0]

    def arxiv_search(self, title: str) -> list[Record]:
        """Search arXiv titles."""
        words = re.sub(r"[^A-Za-z0-9 ]", " ", delatex(title))
        return self._arxiv(
            {
                "search_query": f'ti:"{" ".join(words.split())}"',
                "max_results": 5,
            }
        )

    def _arxiv(self, params: dict) -> list[Record]:
        body = self.get("https://export.arxiv.org/api/query", params=params)
        try:
            root = ET.fromstring(body)
        except ET.ParseError as exc:
            raise SourceError("arXiv returned malformed XML") from exc
        out = []
        for e in root.findall("a:entry", _ATOM):
            m = re.search(
                r"arxiv\.org/abs/" + ARXIV_ID, e.findtext("a:id", "", _ATOM)
            )
            if not m:  # arXiv reports errors as an entry without an id
                continue
            aid = m[1]
            out.append(
                Record(
                    source="arxiv",
                    id=aid,
                    title=" ".join(e.findtext("a:title", "", _ATOM).split()),
                    authors=[
                        " ".join(a.findtext("a:name", "", _ATOM).split())
                        for a in e.findall("a:author", _ATOM)
                    ],
                    venue="arXiv",
                    year=e.findtext("a:published", "", _ATOM)[:4],
                    doi=f"10.48550/arXiv.{aid}",
                    preprint=True,
                    published_doi=e.findtext("arxiv:doi", "", _ATOM).strip(),
                )
            )
        return out

    # --- OpenReview and its DBLP mirror -------------------------------------

    def openreview_search(self, title: str) -> list[Record]:
        """Search OpenReview; returns official records and DBLP mirrors."""
        body = self.get(
            "https://api2.openreview.net/notes/search",
            params={"term": delatex(title), "source": "forum", "limit": 20},
        )
        out = []
        for n in _json(body, "OpenReview").get("notes", []):
            if record := self._openreview_note(n):
                out.append(record)
        return out

    @staticmethod
    def _openreview_note(n: dict) -> Record | None:
        c = n.get("content", {})

        def val(k: str):
            v = c.get(k)
            return v.get("value") if isinstance(v, dict) else v

        venue, venueid = val("venue") or "", val("venueid") or ""
        bib = val("_bibtex") or ""
        is_dblp = venueid.startswith("dblp.org/") or "{DBLP:" in bib
        if not is_dblp and (
            not venueid
            or venueid.startswith("OpenReview.net/")  # ORCID imports etc.
            or re.search(
                r"submitted|reject|withdrawn|desk", venue + venueid, re.I
            )
        ):
            return None
        authors = []
        for a in val("authors") or []:
            name = a.get("fullname", "") if isinstance(a, dict) else a
            authors.append(first_last(name))
        pages = doi = ""
        try:
            fields = parse_entry(bib).fields if bib else {}
        except BibCheckError:
            fields = {}
        pages = fields.get("pages", "")
        doi = fields.get("doi", "")
        if m := re.match(
            r"https?://(?:dx\.)?doi\.org/(.+)", fields.get("url", "")
        ):
            doi = doi or m[1]
        pdate = n.get("pdate") or n.get("cdate")
        year = _year_from(venue, fields.get("year", ""))
        if not year and pdate:
            year = time.strftime("%Y", time.gmtime(pdate / 1000))
        if venue.startswith("Accepted by "):
            venue = venue.removeprefix("Accepted by ")
        return Record(
            source="dblp" if is_dblp else "openreview",
            id=n["id"],
            title=" ".join((val("title") or "").split()),
            authors=authors,
            venue=venue,
            year=year,
            pages=pages,
            doi=doi,
            preprint=bool(re.search(r"\bcorr\b", f"{venueid} {venue}", re.I))
            or doi.lower().startswith("10.48550/"),
            bibtex=bib or None,
        )

    # --- Semantic Scholar ---------------------------------------------------

    def s2_match(self, title: str) -> Record:
        """Semantic Scholar's best title match (used for venue hints)."""
        body = self.get(
            "https://api.semanticscholar.org/graph/v1/paper/search/match",
            params={
                "query": delatex(title),
                "fields": "title,venue,year,authors,externalIds",
            },
        )
        data = _json(body, "Semantic Scholar").get("data") or []
        if not data:
            raise NotFoundError(title)
        p = data[0]
        venue = p.get("venue") or ""
        ids = p.get("externalIds") or {}
        return Record(
            source="s2",
            id=p.get("paperId", ""),
            title=p.get("title") or "",
            authors=[a.get("name", "") for a in p.get("authors") or []],
            venue=venue,
            year=str(p.get("year") or ""),
            doi=ids.get("DOI", ""),
            preprint=not venue or bool(re.search(r"arxiv", venue, re.I)),
        )


def record_bibtex(sources: Sources, record: Record) -> str | None:
    """Raw BibTeX for a record, or None if its source has none."""
    if record.source in {"openreview", "dblp"}:
        return record.bibtex
    if record.source in {"crossref", "arxiv"}:
        return sources.bibtex(record.doi)
    return None


def default_cache_dir() -> Path:
    """``$XDG_CACHE_HOME/papertools/bibcheck`` (``~/.cache`` by default)."""
    base = os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache"
    return Path(base) / "papertools" / "bibcheck"


def title_matches(record: Record, title: str) -> bool:
    """Exact title match after normalization."""
    return bool(title) and norm_title(record.title) == norm_title(title)


def entry_authors(field_value: str) -> list[str]:
    """Authors of a .bib entry as ``First Last`` strings."""
    return split_authors(field_value) if field_value else []


def warn(message: str) -> None:
    """Print a progress or source warning to stderr."""
    print(message, file=sys.stderr)
