"""The Wayback Machine's snapshot lookup."""

import re

from ._parse import _json
from .errors import NotFoundError
from .http import HttpClient


class Wayback:
    """Archived copies of pages."""

    def __init__(self, get: HttpClient):
        self.get = get

    def closest(self, url: str, year: str = "") -> tuple[str, str]:
        """The Wayback Machine's closest snapshot: (snapshot URL, date)."""
        params = {"url": url}
        if re.fullmatch(r"\d{4}", year):
            params["timestamp"] = f"{year}0701"
        body = self.get("https://archive.org/wayback/available", params=params)
        snap = (
            _json(body, "Wayback Machine")
            .get("archived_snapshots", {})
            .get("closest")
        )
        if not snap or not snap.get("available") or not snap.get("url"):
            raise NotFoundError(url)
        stamp = snap.get("timestamp", "")
        date = f"{stamp[:4]}-{stamp[4:6]}-{stamp[6:8]}" if stamp else ""
        return snap["url"].replace("http://", "https://", 1), date
