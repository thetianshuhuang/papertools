"""Find the authoritative records for a .bib entry."""

import re
from collections.abc import Callable
from dataclasses import dataclass, field

from .bibtex import Entry
from .sources import ARXIV_ID, NotFoundError, Record, SourceError, Sources
from .text import delatex, name_key, norm_title, split_authors, venue_family

# Preference among published records found by title search.
_RANK = {"crossref": 0, "openreview": 1, "dblp": 2, "s2": 3, "arxiv": 4}


@dataclass
class Resolution:
    """What the sources say about one entry.

    Attributes:
        primary: The record the entry most directly corresponds to: its own
            DOI or arXiv id if it has one, else the best title match.
        via: How ``primary`` was found: ``doi``, ``arXiv id``, or ``title``.
        published: The published (non-preprint) version, if one was found.
            Same object as ``primary`` when the primary is published.
        hint: A Semantic Scholar record saying the paper was published, for
            when no published record with metadata could be found.
        references: Extra records (DBLP mirror, arXiv) for author checks.
        bad_doi: The entry's DOI did not resolve.
        errors: Sources that failed, as messages.

    """

    primary: Record | None = None
    via: str = ""
    published: Record | None = None
    hint: Record | None = None
    references: list[Record] = field(default_factory=list)
    bad_doi: bool = False
    errors: list[str] = field(default_factory=list)


def entry_doi(entry: Entry) -> str:
    """The entry's DOI, from ``doi`` or a doi.org ``url``."""
    for value in (entry.fields.get("doi", ""), entry.fields.get("url", "")):
        if m := re.search(r"(10\.\d{4,9}/\S+)", value):
            return m[1].rstrip(".")
    return ""


def entry_arxiv_id(entry: Entry) -> str:
    """The entry's arXiv id, from whichever field mentions one."""
    f = entry.fields
    if m := re.match(r"10\.48550/arxiv\.(.+)", entry_doi(entry), re.I):
        return m[1]
    eprint = f.get("eprint", "")
    if re.fullmatch(ARXIV_ID + r"(v\d+)?", eprint):
        return re.sub(r"v\d+$", "", eprint)
    for name in (
        "journal",
        "volume",
        "booktitle",
        "note",
        "howpublished",
        "url",
    ):
        value = f.get(name, "")
        if m := re.search(
            r"(?:arxiv[:.\s]*(?:abs/|preprint\s*(?:arxiv:)?)?|abs/)" + ARXIV_ID,
            value,
            re.I,
        ):
            return m[1]
    return ""


def entry_venue(entry: Entry) -> str:
    """The venue the entry claims, as written."""
    f = entry.fields
    return delatex(
        f.get("booktitle")
        or f.get("journal")
        or f.get("howpublished")
        or f.get("archiveprefix", "")
    )


def is_preprint_entry(entry: Entry) -> bool:
    """Whether the entry cites a preprint rather than a publication."""
    family = venue_family(entry_venue(entry))
    if family == "arXiv":
        return True
    if family:
        return False
    if re.match(r"10\.48550/", entry_doi(entry), re.I):
        return True
    return entry.type in {"misc", "unpublished", "article"} and bool(
        entry_arxiv_id(entry)
    )


def same_paper(a: list[str], b: list[str]) -> bool:
    """Loose author-list agreement, to reject same-title different papers."""
    if not a or not b:
        return True

    def surnames(names):
        return {name_key(n.split()[-1]) for n in names if n.split()}

    sa, sb = surnames(a), surnames(b)
    if name_key(a[0].split()[-1]) == name_key(b[0].split()[-1]):
        return True
    return len(sa & sb) >= max(1, min(len(sa), len(sb)) // 2)


class Resolver:
    """Resolve entries against :class:`Sources`, collecting source errors."""

    def __init__(self, sources: Sources):
        self.sources = sources

    def _try(self, res: Resolution, what: str, call: Callable, *args):
        try:
            return call(*args)
        except NotFoundError:
            return None
        except SourceError as exc:
            res.errors.append(f"{what}: {exc}")
            return None

    def resolve(self, entry: Entry) -> Resolution:
        """Look up ``entry`` and return everything the sources say."""
        res = Resolution()
        s = self.sources
        ours = split_authors(entry.fields.get("author", ""))
        title = entry.fields.get("title", "")

        doi, aid = entry_doi(entry), entry_arxiv_id(entry)
        if doi and not doi.lower().startswith("10.48550/"):
            res.primary = self._try(res, "doi.org", s.doi, doi)
            res.via = "doi"
            res.bad_doi = res.primary is None and not res.errors
        if res.primary is None and aid:
            res.primary = self._try(res, "arXiv", s.arxiv_id, aid)
            res.via = "arXiv id"
        if res.primary and res.primary.title:
            title = res.primary.title
        if not title:
            return res

        candidates: list[Record] = []
        want_published = res.primary is None or res.primary.preprint
        if want_published:
            candidates += (
                self._try(res, "Crossref", s.crossref_search, title) or []
            )
        candidates += (
            self._try(res, "OpenReview", s.openreview_search, title) or []
        )
        if res.primary is None or res.primary.source != "arxiv":
            candidates += self._try(res, "arXiv", s.arxiv_search, title) or []
        authors = res.primary.authors if res.primary else ours
        matches = [
            r
            for r in candidates
            if norm_title(r.title) == norm_title(title)
            and same_paper(authors, r.authors)
        ]

        if res.primary is None:
            res.primary = self._pick(matches, entry)
            res.via = "title" if res.primary else ""
        if res.primary is None:
            hint = self._try(res, "Semantic Scholar", s.s2_match, title)
            if hint and norm_title(hint.title) == norm_title(title):
                res.primary, res.via = hint, "title"
        if res.primary is None:
            return res

        res.primary = self._upgrade(res, res.primary)
        if not res.primary.preprint:
            res.published = res.primary
        else:
            if res.primary.published_doi:
                res.published = self._try(
                    res, "doi.org", s.doi, res.primary.published_doi
                )
            if res.published is None:
                res.published = self._pick(
                    [r for r in matches if not r.preprint], entry
                )
                if res.published:
                    res.published = self._upgrade(res, res.published)
            if res.published is None and res.primary.source != "s2":
                hint = self._try(res, "Semantic Scholar", s.s2_match, title)
                if (
                    hint
                    and not hint.preprint
                    and norm_title(hint.title) == norm_title(title)
                ):
                    res.hint = hint

        for source in ("dblp", "arxiv"):
            if any(r.source == source for r in res.references):
                continue
            refs = [
                r
                for r in matches
                if r.source == source and r is not res.primary and r.authors
            ]
            # Prefer DBLP's published record over its CoRR one.
            refs.sort(key=lambda r: r.preprint)
            if refs:
                res.references.append(refs[0])
        return res

    def _upgrade(self, res: Resolution, record: Record) -> Record:
        """Swap a DBLP record for its publisher's DOI record, if it has one.

        DBLP's BibTeX abbreviates venues (``booktitle={CVPR}``); the DOI
        record is the authoritative one. The DBLP record stays available as
        an author reference.
        """
        if record.source != "dblp" or not record.doi or record.preprint:
            return record
        better = self._try(res, "doi.org", self.sources.doi, record.doi)
        if better is None:
            return record
        res.references.append(record)
        return better

    @staticmethod
    def _pick(matches: list[Record], entry: Entry) -> Record | None:
        """Best match: published first, then the entry's venue and year."""
        if not matches:
            return None
        family = venue_family(entry_venue(entry))
        year = entry.fields.get("year", "")

        def score(r: Record):
            return (
                r.preprint,
                family is not None and venue_family(r.venue) != family,
                r.year != year,
                _RANK[r.source],
            )

        return min(matches, key=score)
