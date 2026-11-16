"""OpenAlex: theses, reports, and books the other indexes lack."""

from ..text import delatex, first_last
from ._parse import _json
from .http import HttpClient
from .records import Work


class OpenAlex:
    """OpenAlex's work search."""

    def __init__(self, get: HttpClient):
        self.get = get

    def search(self, title: str) -> list["Work"]:
        """Search OpenAlex, which also indexes theses, reports, and books."""
        body = self.get(
            "https://api.openalex.org/works",
            params={
                "search": delatex(title),
                "per_page": 5,
                "select": "display_name,doi,type,publication_year,"
                "primary_location,locations,authorships",
            },
        )
        out = []
        for w in _json(body, "OpenAlex").get("results", []):
            locations = [w.get("primary_location") or {}] + (
                w.get("locations") or []
            )
            urls = [
                u
                for loc in locations
                for u in (loc.get("landing_page_url"), loc.get("pdf_url"))
                if u
            ]
            doi = (w.get("doi") or "").removeprefix("https://doi.org/")
            out.append(
                Work(
                    title=w.get("display_name") or "",
                    type=w.get("type") or "",
                    year=str(w.get("publication_year") or ""),
                    doi=doi,
                    url=next(
                        (u for u in urls if "doi.org/" not in u),
                        urls[0] if urls else "",
                    ),
                    # Usually "First Last", but repositories' records can be
                    # "LAST, FIRST".
                    authors=[
                        first_last(
                            (a.get("author") or {}).get("display_name") or ""
                        )
                        for a in w.get("authorships") or []
                    ],
                )
            )
        return out
