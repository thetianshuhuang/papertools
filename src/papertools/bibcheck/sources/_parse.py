"""Parsing helpers shared by the source clients."""

import html
import json
import re

from .errors import SourceError


def _json(body: str, what: str) -> dict:
    try:
        data = json.loads(body)
    except json.JSONDecodeError as exc:
        raise SourceError(f"{what} returned malformed JSON") from exc
    # Every source answers with an object; anything else (e.g. a damaged
    # cache file) is reported for this entry rather than crashing the run.
    if not isinstance(data, dict):
        raise SourceError(f"{what} returned unexpected JSON")
    return data


def _plain(text: str) -> str:
    """Strip HTML/MathML tags and entities from a Crossref title."""
    return " ".join(html.unescape(re.sub(r"<[^>]+>", "", text)).split())


def _year_from(*texts: str) -> str:
    for t in texts:
        if m := re.search(r"\b(19|20)\d{2}\b", t or ""):
            return m[0]
    return ""
