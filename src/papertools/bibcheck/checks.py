"""Compare a .bib entry with the records the sources returned.

:func:`check` runs :data:`CHECKS`, a list of small checkers, in order; each
looks at one thing and returns its issues, so a new check is a new function
in the list. Most compare the entry with its record and skip entries no
index has; those are judged by their link (:mod:`.link_issues`).
"""

import re
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass

from .bibtex import Entry
from .duplicates import Duplicates, duplicates, identities
from .issues import DEFAULT_MAX_AUTHORS, NOTE, WARN, Issue, IssueKind
from .link_issues import link_issues, unindexed_issues, url_advice
from .matching import doubtful_match, known_doi
from .resolve import Resolution, entry_doi, entry_venue, is_preprint_entry
from .sources import Record
from .syntax import lint
from .text import (
    count_accents,
    delatex,
    name_key,
    needs_protection,
    norm_pages,
    norm_title,
    shouting,
    split_authors,
    venue_family,
)

# Re-exported: these lived here before the split, and callers import them.
__all__ = [
    "CHECKS",
    "DEFAULT_MAX_AUTHORS",
    "NOTE",
    "WARN",
    "Duplicates",
    "Issue",
    "IssueKind",
    "check",
    "doubtful_match",
    "duplicates",
    "identities",
    "known_doi",
    "url_advice",
]


@dataclass(frozen=True)
class Context:
    """The options checkers need besides the entry and its resolution."""

    sentence_case: bool = False
    max_authors: int = DEFAULT_MAX_AUTHORS


Checker = Callable[[Entry, Resolution, Context], list[Issue]]


def needs_record(checker: Checker) -> Checker:
    """Skip ``checker`` for entries no index has."""

    def run(entry: Entry, res: Resolution, ctx: Context) -> list[Issue]:
        return checker(entry, res, ctx) if res.primary is not None else []

    run.__name__ = run.__qualname__ = checker.__name__
    run.__doc__ = checker.__doc__
    return run


def syntax_issues(entry: Entry, res: Resolution, ctx: Context) -> list[Issue]:
    """What BibTeX or LaTeX would trip over (see :mod:`.syntax`)."""
    return [Issue(WARN, kind, message) for kind, message in lint(entry)]


def bad_doi(entry: Entry, res: Resolution, ctx: Context) -> list[Issue]:
    """The entry's DOI doesn't resolve."""
    if not res.bad_doi:
        return []
    return [Issue(WARN, "doi", f"{entry_doi(entry)} does not resolve")]


def not_indexed(entry: Entry, res: Resolution, ctx: Context) -> list[Issue]:
    """No index has it: judge it by its link."""
    return unindexed_issues(entry, res) if res.primary is None else []


@needs_record
def own_link(entry: Entry, res: Resolution, ctx: Context) -> list[Issue]:
    """An indexed entry's link: only a broken one is a warning."""
    return link_issues(entry, res)


@needs_record
def missing_doi(entry: Entry, res: Resolution, ctx: Context) -> list[Issue]:
    """The record has a DOI the entry lacks."""
    if not (doi := known_doi(entry, res)):
        return []
    return [
        Issue(
            NOTE,
            "doi",
            f"no doi; {res.primary.label} has it (--fix adds doi={{{doi}}})",
        )
    ]


@needs_record
def title(entry: Entry, res: Resolution, ctx: Context) -> list[Issue]:
    """Title wording and capitalization."""
    return _title(entry, res.primary, ctx.sentence_case)


@needs_record
def authors(entry: Entry, res: Resolution, ctx: Context) -> list[Issue]:
    """Author count, order, spelling, accents, and ``and others``."""
    return _authors(entry, res, ctx.max_authors)


@needs_record
def published_version(
    entry: Entry, res: Resolution, ctx: Context
) -> list[Issue]:
    """An arXiv entry whose paper has been published."""
    if not is_preprint_entry(entry):
        return []
    if res.published:
        return [
            Issue(
                WARN,
                "arxiv",
                f"published version exists: {res.published.where} "
                f"({res.published.label})",
            )
        ]
    if res.hint:
        # Semantic Scholar's year is the first (arXiv) year, so not shown.
        return [
            Issue(
                WARN,
                "arxiv",
                f"published in {res.hint.venue} according to Semantic "
                "Scholar; cite that version",
            )
        ]
    return []


@needs_record
def metadata(entry: Entry, res: Resolution, ctx: Context) -> list[Issue]:
    """Venue, year, pages, and DOI against the published record."""
    return [] if is_preprint_entry(entry) else _metadata(entry, res)


# Run in this order; issues are reported in this order.
CHECKS: list[Checker] = [
    syntax_issues,
    bad_doi,
    not_indexed,
    own_link,
    missing_doi,
    title,
    authors,
    published_version,
    metadata,
]


def check(
    entry: Entry,
    res: Resolution,
    *,
    sentence_case: bool = False,
    max_authors: int = DEFAULT_MAX_AUTHORS,
) -> list[Issue]:
    """Return every issue found for ``entry``.

    With ``sentence_case`` (the paper's ``.bst`` lowercases titles), unbraced
    acronyms are warnings rather than notes. Author lists longer than
    ``max_authors`` may be cut short with ``and others``.
    """
    ctx = Context(sentence_case, max_authors)
    return [issue for checker in CHECKS for issue in checker(entry, res, ctx)]


def _title(entry: Entry, record: Record, sentence_case: bool) -> list[Issue]:
    ours = entry.fields.get("title", "")
    if not ours:
        return [Issue(WARN, "title", "missing")]
    if norm_title(ours) != norm_title(record.title):
        return [
            Issue(
                WARN,
                "title",
                f"differs: ours={delatex(ours)!r} "
                f"{record.source}={record.title!r}",
            )
        ]
    if record.title.isupper() or record.title.islower():
        return []
    issues = []
    whole = _fully_braced(ours)
    words = {}  # lowercase -> list of (as written, braced?)
    for m in re.finditer(r"[^\s{}]+", ours):
        depth = ours[: m.start()].count("{") - ours[: m.start()].count("}")
        w = m[0].strip(":,.?!;()")
        words.setdefault(w.lower(), []).append((w, whole or depth > 0))
    checked = set()
    for i, raw in enumerate(record.title.split()):
        w = raw.strip(":,.?!;()")
        if (
            w in checked
            or not needs_protection(raw, leading=i == 0)
            or w.lower() not in words
        ):
            continue
        checked.add(w)
        seen = words[w.lower()]
        if all(written != w for written, _ in seen):
            issues.append(
                Issue(
                    WARN,
                    "title",
                    f"capitalization: {seen[0][0]!r} should be {{{w}}}",
                )
            )
        elif not any(braced for written, braced in seen if written == w):
            if sentence_case:
                issue = Issue(
                    WARN,
                    "title",
                    f"{w!r} will print as {w.lower()!r}; write {{{w}}}",
                )
            else:
                issue = Issue(
                    NOTE,
                    "title",
                    f"unbraced {w!r} is lowercased by sentence-case styles; "
                    f"write {{{w}}}",
                )
            issues.append(issue)
    return issues


def _fully_braced(title: str) -> bool:
    if not (title.startswith("{") and title.endswith("}")):
        return False
    depth = 0
    for i, c in enumerate(title):
        depth += {"{": 1, "}": -1}.get(c, 0)
        if depth == 0 and i < len(title) - 1:
            return False
    return True


def _surname(name: str) -> str:
    return name_key(name.split()[-1]) if name.split() else ""


def _given_compatible(a: str, b: str) -> bool:
    """Given names agree up to initials, middle names, and spacing."""
    ga = [name_key(p) for p in a.split()[:-1] if name_key(p)]
    gb = [name_key(p) for p in b.split()[:-1] if name_key(p)]
    if not ga or not gb:
        return True
    if "".join(ga) == "".join(gb):
        return True
    first_a, first_b = ga[0], gb[0]
    return first_a.startswith(first_b) or first_b.startswith(first_a)


def _authors(entry: Entry, res: Resolution, max_authors: int) -> list[Issue]:
    field_value = entry.fields.get("author", "")
    if not field_value:
        return [Issue(WARN, "author", "missing")]
    ours = split_authors(field_value)
    refs = [r for r in [res.primary, *res.references] if r and not r.hint_only]
    refs = [r for r in refs if r.authors]
    issues = []
    if ours and ours[-1].lower() == "others":
        return _truncated(ours[:-1], refs, max_authors)
    # When the primary record agrees with ours, differences that only a
    # reference (DBLP, arXiv) reports are usually the reference's problem
    # (arXiv author order changed, typos) and are demoted to notes. Accents
    # stay warnings: IEEE records routinely drop them.
    primary = res.primary
    primary_agrees = (
        primary is not None
        and not primary.hint_only
        and [name_key(n) for n in primary.authors]
        == [name_key(n) for n in ours]
    )
    # (level, message) -> sources that agree on it
    found: dict[tuple[str, str], list[str]] = defaultdict(list)
    for r in refs:
        theirs = r.authors
        soft = WARN if r is primary or not primary_agrees else NOTE
        if len(ours) != len(theirs):
            found[
                (soft, f"count: ours={len(ours)} {{src}}={len(theirs)}")
            ].append(r.source)
            continue
        if sorted(map(name_key, ours)) == sorted(map(name_key, theirs)) and [
            name_key(o) for o in ours
        ] != [name_key(t) for t in theirs]:
            found[(soft, "order differs from {src}")].append(r.source)
            continue
        for o, t in zip(ours, theirs):
            if o == t:
                continue
            if name_key(o) == name_key(t):
                # Same letters; only accents or punctuation differ. Flag only
                # when ours lost accents the reference has.
                if count_accents(t) > count_accents(o):
                    found[(WARN, f"accent: ours={o!r} {{src}}={t!r}")].append(
                        r.source
                    )
                elif shouting(o) and not shouting(t):
                    found[(WARN, f"case: ours={o!r} {{src}}={t!r}")].append(
                        r.source
                    )
            elif _surname(o) != _surname(t):
                found[(soft, f"mismatch: ours={o!r} {{src}}={t!r}")].append(
                    r.source
                )
            elif not _given_compatible(o, t):
                found[(NOTE, f"given name: ours={o!r} {{src}}={t!r}")].append(
                    r.source
                )
    for (level, message), srcs in found.items():
        issues.append(
            Issue(level, "author", message.format(src="/".join(srcs)))
        )
    return issues


def _truncated(listed: list[str], refs, max_authors: int) -> list[Issue]:
    """An ``and others`` list: fine if the full list is long enough.

    The names given must be the first names of the full list.
    """
    full = max((r.authors for r in refs), key=len, default=[])
    n = len(full)
    where = f" (full list has {n} authors)" if n else ""
    if n and [name_key(a) for a in listed] != [
        name_key(a) for a in full[: len(listed)]
    ]:
        return [
            Issue(
                WARN,
                "author",
                f"truncated with 'and others', but the names given aren't "
                f"the first {len(listed)} of the {n}",
            )
        ]
    if n > max_authors:
        return [Issue(NOTE, "author", f"truncated with 'and others'{where}")]
    return [Issue(WARN, "author", f"truncated with 'and others'{where}")]


def _metadata(entry: Entry, res: Resolution) -> list[Issue]:
    f = entry.fields
    issues = []
    target = res.published
    venue = entry_venue(entry)
    family = venue_family(venue)
    year = f.get("year", "")
    if year and f.get("volume") == year:
        issues.append(
            Issue(
                WARN,
                "venue",
                f"volume={{{year}}} equals the year (a Google Scholar "
                "artifact)",
            )
        )
    if target is None:
        hint = res.hint
        hint_family = venue_family(hint.venue) if hint else None
        if family and hint_family and family != hint_family:
            issues.append(
                Issue(
                    WARN,
                    "venue",
                    f"ours={venue!r}, Semantic Scholar={hint.venue!r}",
                )
            )
        return issues
    their_family = venue_family(target.venue)
    if family and their_family and family != their_family:
        issues.append(
            Issue(
                WARN,
                "venue",
                f"ours={venue!r} {target.source}={target.where!r}",
            )
        )
    if their_family in {"ICLR", "TMLR"} and (f.get("pages") or f.get("number")):
        issues.append(
            Issue(
                WARN,
                "venue",
                f"{their_family} papers have no pages or issue number",
            )
        )
    if (
        year
        and target.year
        and year != target.year
        and year not in target.other_years
    ):
        # A journal article dated a year before its issue is usually cited
        # by its early-access date (IEEE does this), which Crossref often
        # lacks. Worth a look, but not a warning.
        early_access = (
            entry.type == "article"
            and year.isdigit()
            and target.year.isdigit()
            and int(year) == int(target.year) - 1
        )
        issues.append(
            Issue(
                NOTE if early_access else WARN,
                "year",
                f"ours={year} {target.source}={target.year}"
                + (" (early access?)" if early_access else ""),
            )
        )
    ours_pages, their_pages = f.get("pages", ""), target.pages
    if (
        ours_pages
        and their_pages
        and norm_pages(ours_pages) != norm_pages(their_pages)
    ):
        issues.append(
            Issue(
                WARN,
                "pages",
                f"ours={ours_pages} {target.source}={their_pages}",
            )
        )
    doi = entry_doi(entry)
    if doi and target.doi and doi.lower() != target.doi.lower():
        issues.append(
            Issue(WARN, "doi", f"ours={doi} {target.source}={target.doi}")
        )
    return issues
