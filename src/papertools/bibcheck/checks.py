"""Compare a .bib entry with the records the sources returned."""

import re
from collections import defaultdict
from dataclasses import dataclass

from .bibtex import Entry
from .resolve import Resolution, entry_doi, entry_venue, is_preprint_entry
from .sources import Record
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

WARN, NOTE = "warn", "note"


@dataclass
class Issue:
    """One finding. ``level`` is ``warn`` (likely wrong) or ``note``."""

    level: str
    kind: str
    message: str


def check(entry: Entry, res: Resolution) -> list[Issue]:
    """Return every issue found for ``entry``."""
    issues: list[Issue] = []
    if res.bad_doi:
        issues.append(
            Issue(WARN, "doi", f"{entry_doi(entry)} does not resolve")
        )
    if res.primary is None:
        if not res.errors:
            issues.append(
                Issue(
                    WARN,
                    "missing",
                    "no matching record on Crossref, arXiv, OpenReview, "
                    "DBLP, or Semantic Scholar",
                )
            )
        return issues
    issues += _title(entry, res.primary)
    issues += _authors(entry, res)
    preprint = is_preprint_entry(entry)
    if preprint and res.published:
        issues.append(
            Issue(
                WARN,
                "arxiv",
                f"published version exists: {res.published.where} "
                f"({res.published.label})",
            )
        )
    elif preprint and res.hint:
        issues.append(
            Issue(
                WARN,
                "arxiv",
                f"published in {res.hint.where} according to Semantic "
                "Scholar; cite that version",
            )
        )
    if not preprint:
        issues += _metadata(entry, res)
    return issues


def _title(entry: Entry, record: Record) -> list[Issue]:
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
    for w in {w.strip(":,.?!;()") for w in record.title.split()}:
        if not needs_protection(w) or w.lower() not in words:
            continue
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
            issues.append(
                Issue(
                    NOTE,
                    "title",
                    f"unbraced {w!r} is lowercased by sentence-case styles; "
                    f"write {{{w}}}",
                )
            )
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


def _authors(entry: Entry, res: Resolution) -> list[Issue]:
    field_value = entry.fields.get("author", "")
    if not field_value:
        return [Issue(WARN, "author", "missing")]
    ours = split_authors(field_value)
    refs = [r for r in [res.primary, *res.references] if r and r.source != "s2"]
    refs = [r for r in refs if r.authors]
    issues = []
    if ours and ours[-1].lower() == "others":
        n = max((len(r.authors) for r in refs), default=0)
        issues.append(
            Issue(
                WARN,
                "author",
                "truncated with 'and others'"
                + (f" (full list has {n} authors)" if n else ""),
            )
        )
        return issues
    # When the primary record agrees with ours, differences that only a
    # reference (DBLP, arXiv) reports are usually the reference's problem
    # (arXiv author order changed, typos) and are demoted to notes. Accents
    # stay warnings: IEEE records routinely drop them.
    primary = res.primary
    primary_agrees = (
        primary is not None
        and primary.source != "s2"
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
                    f"ours={venue!r}, Semantic Scholar={hint.where!r}",
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
    if year and target.year and year != target.year:
        issues.append(
            Issue(WARN, "year", f"ours={year} {target.source}={target.year}")
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
    elif not doi and target.doi and not res.bad_doi:
        issues.append(Issue(NOTE, "doi", f"missing; record has {target.doi}"))
    return issues
