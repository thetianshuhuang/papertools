"""arXiv's API: lookups by id and title searches."""

import re
import xml.etree.ElementTree as ET

from ..text import delatex
from .errors import NotFoundError, SourceError
from .http import HttpClient
from .records import Record

_ATOM = {
    "a": "http://www.w3.org/2005/Atom",
    "arxiv": "http://arxiv.org/schemas/atom",
}
ARXIV_ID = r"(\d{4}\.\d{4,5}|[a-z][a-z.-]*/\d{7})"


class Arxiv:
    """arXiv records."""

    def __init__(self, get: HttpClient):
        self.get = get

    def by_id(self, arxiv_id: str) -> Record:
        """Look up an arXiv paper by id."""
        found = self._arxiv({"id_list": arxiv_id})
        if not found:
            raise NotFoundError(arxiv_id)
        return found[0]

    def search(self, title: str) -> list[Record]:
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
