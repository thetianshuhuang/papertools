"""bibcheck's machine-readable results (``--json``, or for other programs).

The document, ``report_json``::

    {
      "version": 1,
      "file": "main.bib",
      "style": {"sentence_case": false, "why": "..."},
      "syntax_errors": [{"line", "column", "key", "field", "message",
                         "hint", "excerpt"}],  # skipped entries
      "entries": [entry_json(...), ...],
      "summary": {"checked": 2, "warnings": 1, "missing": 0,
                  "notes_only": 1, "unchecked": 0, "syntax_errors": 0},
      "fix": null | {"fixed": [{"key", "old", "new"}],
                     "skipped": [{"key", "reason"}], "backup": "main.bib.bak"}
    }

``level`` is ``warn``, ``note``, or ``ok`` for entries; ``warn`` or ``note``
for issues. Issues' ``details`` are the workings behind them.

Each entry's ``match`` says whether it was found, and how:

- ``found``: a scholarly index has it (``found`` holds the record).
- ``link``: no index has it; its URL loads and shows the title, which a
  person should confirm.
- ``suggested``: no index has it; a URL was found for it.
- ``link-problem``: no index has it, and its URL is broken, about something
  else, or couldn't be checked.
- ``not-found``: nothing has it, and no URL was found: possibly made up.
- ``unchecked``: a source failed, so this can't be said.

``match.action`` says what to do about the entry's link, if anything.
``review_links`` lists the links to open by hand: the entry's own, any
suggested one, and searches when nothing worked, each with ``advice``.

``title`` is the entry's title as plain text (LaTeX converted), and
``bibtex`` is the entry as written, and ``fix`` (with --fix) is
``{"new": ..., "change": ...}`` or ``{"skipped": reason}``, or null if it
needed none.
"""

from collections import Counter
from dataclasses import asdict

from .bibtex import Entry
from .issues import NOTE, WARN, Issue
from .link_issues import url_advice
from .resolve import Resolution
from .text import delatex

VERSION = 1
MAX_SYNTAX_ERRORS = 100


def _record(record) -> dict | None:
    if record is None:
        return None
    return {
        "source": record.source,
        "id": record.id,
        "label": record.label,
        "title": record.title,
        "venue": record.venue,
        "year": record.year,
        "doi": record.doi,
    }


def entry_level(issues: list[Issue]) -> str:
    """``warn`` if any issue is a warning, else ``note`` if any, else ``ok``."""
    if any(i.level == WARN for i in issues):
        return WARN
    return NOTE if issues else "ok"


# Every ``match`` status, in display order: its label, tone (ok, warn, err),
# and group: found; check (found another way, a person should confirm);
# missing (not found, or the link failed). Front ends display these rather
# than knowing the statuses themselves.
STATUSES = {
    "found": {"label": "Found", "tone": "ok", "group": "found"},
    "link": {"label": "Check link", "tone": "warn", "group": "check"},
    "suggested": {"label": "URL suggested", "tone": "warn", "group": "check"},
    "link-problem": {
        "label": "Link problem",
        "tone": "warn",
        "group": "missing",
    },
    "not-found": {"label": "Not found", "tone": "err", "group": "missing"},
    "unchecked": {"label": "Unchecked", "tone": "err", "group": "missing"},
}
# How each kind of link check is shown. "ok" means only that the page shows
# the title, so it's a prompt to check, not a confirmation.
LINK_VERDICTS = {
    "ok": {"label": "title found: check", "tone": "warn"},
    "mismatch": {"label": "different page", "tone": "warn"},
    "unverified": {"label": "not checked", "tone": "warn"},
    "broken": {"label": "broken", "tone": "err"},
}


def match_json(res: Resolution, issues: list[Issue]) -> dict:
    """Whether the entry was found: status, sentence, label, tone, group."""
    match = _match(res, issues)
    return match | STATUSES[match["status"]]


def _match(res: Resolution, issues: list[Issue]) -> dict:
    """Whether the entry was found, as a status and a sentence."""
    link = res.link
    given = link.given if link else None
    suggestion = link.suggestion if link else None
    if res.primary is not None:
        where = res.primary.label
        if res.primary.hint_only:
            return {
                "status": "found",
                "summary": f"Found on Semantic Scholar only ({where}), by "
                "title: check it's the same work",
            }
        return {
            "status": "found",
            "summary": f"Found: {where} (matched by {res.via})",
        }
    if given and given.status == "ok":
        return {
            "status": "link",
            "summary": "Not in the scholarly indexes. Its link shows the "
            f"title ({given.reason}): open it and check it's the source "
            "you cited",
        }
    if suggestion:
        return {
            "status": "suggested",
            "summary": "Not in the scholarly indexes; a URL was found for it "
            f"({suggestion.how})",
        }
    if given:
        problem = {
            "broken": "is broken",
            "mismatch": "shows something else",
            "unverified": "couldn't be checked automatically",
        }[given.status]
        return {
            "status": "link-problem",
            "summary": f"Not in the scholarly indexes, and its link {problem}"
            ": check it by hand",
        }
    if any(i.kind == "missing" for i in issues):
        return {
            "status": "not-found",
            "summary": "Not found anywhere, and no URL was found: check that "
            "it exists",
        }
    return {
        "status": "unchecked",
        "summary": "Not checked: a source failed (see errors); run again",
    }


# What to do with a link, by what checking it showed.
_GIVEN_ADVICE = {
    "ok": "The page shows the title, but only you can confirm it: open it "
    "and check it's the source you cited",
    "mismatch": "It shows a different page: open it and check whether this "
    "reference is still there",
    "unverified": "The site blocks automated checks: open it and check that "
    "it shows this reference",
    "broken": "It didn't load: open it to see whether it's really gone",
}
_MANUAL_ADVICE = {
    "open": None,  # The entry's own link, listed above.
    "archived copies": "Look for an archived copy of the link",
    "web search": "Search the web for the title",
    "Google Scholar": "Search Google Scholar for the title",
}


def _verdict_style(status: str) -> dict:
    style = LINK_VERDICTS[status]
    return {"status_label": style["label"], "tone": style["tone"]}


def review_links(res: Resolution) -> list[dict]:
    """Links for a person to open, each with what to check.

    Every link bibcheck checked or suggested is listed, since an automatic
    check can only compare titles; when nothing worked, searches follow.
    """
    link = res.link
    if link is None:
        return []
    out = []
    if link.given:
        out.append(
            {
                "role": "given",
                "label": "Link in the entry",
                "url": link.given.url,
                "status": link.given.status,
                **_verdict_style(link.given.status),
                "result": link.given.reason,
                "advice": _GIVEN_ADVICE[link.given.status],
            }
        )
    if link.suggestion:
        shows_title = link.suggestion.status == "ok"
        out.append(
            {
                "role": "suggested",
                "label": "Suggested link",
                "url": link.suggestion.url,
                "status": link.suggestion.status,
                **_verdict_style(link.suggestion.status),
                "result": link.suggestion.how,
                "advice": "Open it and check it's the reference you cited "
                + ("before keeping it" if shows_title else "before adding it"),
            }
        )
    for manual in link.manual:
        name, url = manual.name, manual.url
        advice = _MANUAL_ADVICE.get(name, f"Check by hand: {name}")
        if advice and url.startswith(("https://", "http://")):
            out.append(
                {
                    "role": "search",
                    "label": name[0].upper() + name[1:],
                    "url": url,
                    "status": "",
                    "status_label": "",
                    "tone": "",
                    "result": "",
                    "advice": advice,
                }
            )
    return out


def entry_json(entry: Entry, res: Resolution, issues: list[Issue]) -> dict:
    """Everything found for one entry."""
    link = res.link
    match = match_json(res, issues) | {"action": url_advice(entry, res)}
    return {
        "key": entry.key,
        "type": entry.type,
        "title": " ".join(delatex(entry.fields.get("title", "")).split()),
        "level": entry_level(issues),
        "match": match,
        "bibtex": entry.text,
        "fix": None,
        "found": _record(res.primary),
        "via": res.via or None,
        "published": _record(res.published),
        "unchecked": bool(res.errors) and res.primary is None,
        "errors": list(res.errors),
        "issues": [asdict(i) for i in issues],
        "review_links": review_links(res),
        "link": None
        if link is None
        else {
            "given": asdict(link.given) if link.given else None,
            "suggestion": asdict(link.suggestion) if link.suggestion else None,
            "steps": link.steps,
            "manual": [asdict(m) for m in link.manual],
        },
    }


def summary_json(entries: list[dict]) -> dict:
    """Counts over ``entry_json`` results."""
    counts = Counter()
    for e in entries:
        counts["checked"] += 1
        counts["warnings"] += e["level"] == WARN
        counts["notes_only"] += e["level"] == NOTE
        counts["missing"] += any(i["kind"] == "missing" for i in e["issues"])
        counts["unchecked"] += e["unchecked"]
    by_status = Counter(e["match"]["status"] for e in entries)
    return {
        k: counts[k]
        for k in ("checked", "warnings", "missing", "notes_only", "unchecked")
    } | {"by_status": {s: by_status[s] for s in STATUSES}}


def fix_json(fixes: list, backup: str | None = None) -> dict:
    """What ``--fix`` changed (``fixed``) and left alone (``skipped``).

    ``fixes`` are :class:`..fix.Fix` objects.
    """
    return {
        "fixed": [
            {
                "key": f.entry.key,
                "kind": f.kind,
                "old": f.entry.text,
                "new": f.new,
                "change": f.change,
            }
            for f in fixes
            if f.applied
        ],
        "skipped": [
            {"key": f.entry.key, "reason": f.change}
            for f in fixes
            if not f.applied
        ],
        "backup": backup,
    }


def report_json(
    file: str,
    entries: list[dict],
    *,
    sentence_case: bool,
    why: str = "",
    fix: dict | None = None,
    syntax_errors: list[dict] | None = None,
    since: int = 0,
) -> dict:
    """The whole report; ``fix`` results are also set on their entries.

    With ``since``, ``entries`` holds only the entries from that index on
    (``entries_from`` says where they start); the summary still counts all.
    Front ends polling a running check use it to fetch only what's new.
    """
    if fix:
        by_key = {
            f["key"]: {
                "kind": f["kind"],
                "new": f["new"],
                "change": f["change"],
            }
            for f in fix["fixed"]
        }
        by_key |= {s["key"]: {"skipped": s["reason"]} for s in fix["skipped"]}
        entries = [e | {"fix": by_key.get(e["key"])} for e in entries]
    return {
        "version": VERSION,
        "file": file,
        "style": {"sentence_case": sentence_case, "why": why},
        # A file can hold thousands; the first ones show what's wrong.
        "syntax_errors": (syntax_errors or [])[:MAX_SYNTAX_ERRORS],
        "syntax_errors_omitted": max(
            0, len(syntax_errors or []) - MAX_SYNTAX_ERRORS
        ),
        "entries_from": since,
        "entries": entries[since:],
        "summary": summary_json(entries)
        | {"syntax_errors": len(syntax_errors or [])},
        "fix": fix,
    }
