"""Crossref, and any DOI through doi.org (Crossref or DataCite)."""

from urllib.parse import quote

from ..text import delatex, norm_title
from ._parse import _json, _plain
from .http import HttpClient
from .records import Record


class Crossref:
    """DOIs and Crossref's title search."""

    def __init__(self, get: HttpClient):
        self.get = get

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
        # ACM splits "SpotFi: Decimeter Level ..." into title and subtitle.
        subtitle = m.get("subtitle") or ""
        if isinstance(subtitle, list):
            subtitle = subtitle[0] if subtitle else ""
        if subtitle and norm_title(subtitle) not in norm_title(title):
            title = f"{title}: {subtitle}"
        venue = m.get("container-title") or m.get("event", {}) or ""
        if isinstance(venue, dict):
            venue = venue.get("name", "")
        if isinstance(venue, list):
            venue = venue[0] if venue else ""
        # Journals have an online (or early-access) date and an issue date,
        # and either year is a legitimate citation: PhaseLift appeared online
        # in 2012 and in vol. 66 no. 8 in 2013. The print year is reported;
        # the others are accepted too.
        years = []
        for key in ("published-print", "issued", "published-online"):
            parts = (m.get(key) or {}).get("date-parts") or [[None]]
            if parts[0] and parts[0][0]:
                years.append(str(parts[0][0]))
        year = years[0] if years else ""
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
            other_years=set(years[1:]),
        )

    def search(self, title: str) -> list[Record]:
        """Search Crossref by title."""
        body = self.get(
            "https://api.crossref.org/works",
            params={
                "query.bibliographic": delatex(title),
                "rows": 5,
                "select": "DOI,title,subtitle,container-title,issued,"
                "published-print,published-online,author,type,page",
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
