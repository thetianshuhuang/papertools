"""Semantic Scholar: only says whether and where a paper was published."""

import re

from ..text import delatex
from ._parse import _json
from .errors import NotFoundError
from .http import HttpClient
from .records import Record


class SemanticScholar:
    """Semantic Scholar's title match."""

    def __init__(self, get: HttpClient):
        self.get = get

    def match(self, title: str) -> Record:
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
