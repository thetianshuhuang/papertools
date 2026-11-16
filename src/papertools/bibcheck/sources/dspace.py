"""DSpace 7 repositories (RECIPP), searched by title."""

from ..text import delatex, first_last
from ._parse import _json
from .http import HttpClient
from .records import Work

# DSpace 7 repositories searched by title for theses and reports that no
# index has: host -> name. Their REST APIs answer scripts even where the
# web pages sit behind a bot check.
DSPACE_REPOSITORIES = {"recipp.ipp.pt": "RECIPP (P.PORTO)"}
# Pages fetched to check a reference's URL: any other host.


class DSpace:
    """Item search in DSpace repositories."""

    def __init__(self, get: HttpClient):
        self.get = get

    def search(self, host: str, title: str) -> list[Work]:
        """Search a DSpace 7 repository's items by title."""
        body = self.get(
            f"https://{host}/server/api/discover/search/objects",
            params={"query": delatex(title), "dsoType": "ITEM", "size": 5},
            # Without an Accept header, RECIPP's firewall answers 403.
            accept="application/json",
        )
        found = (
            _json(body, host)
            .get("_embedded", {})
            .get("searchResult", {})
            .get("_embedded", {})
            .get("objects", [])
        )
        out = []
        for obj in found:
            item = obj.get("_embedded", {}).get("indexableObject", {})
            meta = item.get("metadata", {})

            def first(name: str, meta=meta) -> str:
                values = meta.get(name) or [{}]
                return values[0].get("value") or ""

            handle = item.get("handle") or ""
            out.append(
                Work(
                    title=first("dc.title") or item.get("name") or "",
                    type=first("dc.type"),
                    year=first("dc.date.issued")[:4],
                    doi=first("dc.identifier.doi"),
                    url=first("dc.identifier.uri")
                    or (f"https://hdl.handle.net/{handle}" if handle else ""),
                    authors=[
                        first_last(v.get("value") or "")
                        for v in meta.get("dc.contributor.author", [])
                    ],
                    handle=handle,
                )
            )
        return out
