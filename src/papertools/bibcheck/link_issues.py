"""Issues about entries' links, and what the author should do about them.

Entries no index has are judged by their link (see :mod:`.links`): working,
suggested, broken, or none at all (``missing``).
"""

from .bibtex import Entry
from .issues import NOTE, WARN, Issue
from .resolve import Resolution

_NOT_INDEXED = (
    "no matching record on Crossref, arXiv, OpenReview, DBLP, or Semantic "
    "Scholar"
)


def _workings(link) -> list[str]:
    return link.steps + [f"check by hand: {m}" for m in link.manual]


def _problem(given) -> str:
    return {
        "broken": f"link is broken ({given.reason}): {given.url}",
        "mismatch": f"link doesn't show this reference, {given.reason}: "
        f"{given.url}",
        "unverified": f"link could not be checked, {given.reason}: {given.url}",
    }[given.status]


# Entry types that usually have a DOI or arXiv id rather than just a URL.
_PAPER_TYPES = {
    "article",
    "inproceedings",
    "conference",
    "incollection",
    "inbook",
    "book",
    "proceedings",
}


def url_advice(entry: Entry, res: Resolution) -> str:
    """What the author should do about the entry's URL, or ''."""
    link = res.link
    given = link.given if link else None
    suggestion = link.suggestion if link else None
    if res.primary is not None:
        if given and given.status == "broken":
            return "fix or remove the url: it no longer works"
        return ""
    if given and given.status == "ok":
        return ""
    if suggestion and suggestion.status == "ok":
        name, value = (
            ("doi", suggestion.doi)
            if suggestion.doi
            else ("url", suggestion.url)
        )
        verb = (
            "replace the link with" if given and not suggestion.doi else "add"
        )
        return f"{verb} {name}={{{value}}} (--fix does this)"
    if suggestion:
        return (
            f"open {suggestion.url} to confirm it's this reference, then "
            + ("use it in place of the link" if given else "add it as url=")
        )
    if given:
        return {
            "broken": "replace the url with a working or archived link",
            "mismatch": "replace the url with the reference's own page",
            "unverified": "open the link to confirm it shows this reference",
        }[given.status]
    if entry.type in _PAPER_TYPES:
        return (
            "add its doi= (or arXiv id) if it has one, otherwise a url=, so "
            "it can be checked"
        )
    return (
        "add a url= for the page or document you cited (or an archived "
        "copy), so it can be checked"
    )


def _todo(message: str, advice: str) -> str:
    return f"{message}: {advice}" if advice else message


def unindexed_issues(entry: Entry, res: Resolution) -> list[Issue]:
    """Issues for an entry no index has: its URL is the only evidence.

    It is ``missing`` only when it has no URL and none could be found.
    """
    link = res.link
    advice = url_advice(entry, res)
    if link is None or (link.given is None and link.suggestion is None):
        if res.errors:
            return []  # Unchecked: a source failed.
        details = _workings(link) if link else []
        message = _NOT_INDEXED + (", and no URL was found" if link else "")
        return [Issue(WARN, "missing", _todo(message, advice), details)]
    given, suggestion = link.given, link.suggestion
    if given and given.status == "ok":
        # Matching a title doesn't prove it's the source cited: a person
        # has to look.
        return [
            Issue(
                WARN,
                "url",
                f"not in the indexes; the link shows the title "
                f"({given.reason}), but check it by hand: open {given.url} "
                "and confirm it's the source you cited",
                _workings(link),
            )
        ]
    if suggestion:
        unchecked = "" if suggestion.status == "ok" else ", page not checked"
        found = f"suggested URL ({suggestion.how}{unchecked}): {suggestion.url}"
        message = (
            f"{_problem(given)}; {found}"
            if given
            else (f"not in the indexes and no URL given; {found}")
        )
        return [Issue(WARN, "url", _todo(message, advice), _workings(link))]
    return [Issue(WARN, "url", _todo(_problem(given), advice), _workings(link))]


def link_issues(entry: Entry, res: Resolution) -> list[Issue]:
    """An indexed entry's own URL: only a broken one is a warning."""
    given = res.link.given if res.link else None
    if given is None or given.status == "ok":
        return []
    level = WARN if given.status == "broken" else NOTE
    message = _todo(_problem(given), url_advice(entry, res))
    return [Issue(level, "url", message, _workings(res.link))]
