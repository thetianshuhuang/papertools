"""OpenReview, and DBLP through OpenReview's mirror."""

import re
import time

from ..bibtex import parse_entry
from ..errors import BibCheckError
from ..text import delatex, first_last
from ._parse import _json, _year_from
from .http import HttpClient
from .records import Record


class OpenReview:
    """OpenReview notes, including the DBLP mirror's."""

    def __init__(self, get: HttpClient):
        self.get = get

    def search(self, title: str, legacy: bool = False) -> list[Record]:
        """Search OpenReview; returns official records and DBLP mirrors.

        With ``legacy``, search the v1 API instead, which alone holds venues
        from before OpenReview's 2023 migration (ICLR 2021, for example).
        """
        api = "api.openreview.net" if legacy else "api2.openreview.net"
        body = self.get(
            f"https://{api}/notes/search",
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
