"""Find the authoritative records for a .bib entry."""

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .bibtex import Entry
from .sources import ARXIV_ID, NotFoundError, Record, SourceError, Sources
from .text import (
    delatex,
    norm_title,
    same_paper,
    split_authors,
    venue_family,
)

if TYPE_CHECKING:
    from .links import LinkCheck

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
        link: What checking the entry's URL (or finding one) showed; None
            when there was nothing to check.

    """

    primary: Record | None = None
    via: str = ""
    published: Record | None = None
    hint: Record | None = None
    references: list[Record] = field(default_factory=list)
    bad_doi: bool = False
    errors: list[str] = field(default_factory=list)
    link: "LinkCheck | None" = None


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


class Resolver:
    """Find what the sources say about entries, collecting source errors.

    Link checking is a separate step (:class:`.links.LinkChecker`).
    """

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
        self._by_identifier(entry, res)
        primary = res.primary
        title = primary.title if primary and primary.title else ""
        title = title or entry.fields.get("title", "")
        if not title:
            return res
        authors = (
            primary.authors
            if primary
            else split_authors(entry.fields.get("author", ""))
        )
        matches = self._search_title(res, title, authors)
        if res.primary is None:
            self._match_by_title(entry, res, title, matches)
            if res.primary is None:
                return res
        res.primary = self._upgrade(res, res.primary)
        self._find_published(entry, res, title, matches)
        self._add_references(res, matches)
        return res

    def _by_identifier(self, entry: Entry, res: Resolution) -> None:
        """Look the entry up by its DOI, else its arXiv id."""
        doi, aid = entry_doi(entry), entry_arxiv_id(entry)
        if doi and not doi.lower().startswith("10.48550/"):
            res.primary = self._try(res, "doi.org", self.sources.doi, doi)
            res.via = "doi"
            res.bad_doi = res.primary is None and not res.errors
        if res.primary is None and aid:
            res.primary = self._try(res, "arXiv", self.sources.arxiv_id, aid)
            res.via = "arXiv id"

    def _search_title(
        self, res: Resolution, title: str, authors: list[str]
    ) -> list[Record]:
        """Records with exactly this title, by overlapping authors."""
        s, primary = self.sources, res.primary
        want_published = primary is None or primary.preprint
        # (source, search, whether it's worth asking)
        searches = [
            ("Crossref", s.crossref_search, want_published),
            ("OpenReview", s.openreview_search, True),
            (
                "arXiv",
                s.arxiv_search,
                primary is None or primary.source != "arxiv",
            ),
        ]

        def matching(records: list[Record]) -> list[Record]:
            return [
                r
                for r in records
                if norm_title(r.title) == norm_title(title)
                and same_paper(authors, r.authors)
            ]

        matches = matching(
            [
                record
                for name, search, wanted in searches
                if wanted
                for record in self._try(res, name, search, title) or []
            ]
        )
        # OpenReview's v1 API holds pre-2023 venues (ICLR 2021, ...) but
        # allows only 5 searches a minute, so it's the last resort.
        if (
            want_published
            and not any(not r.preprint for r in matches)
            and not (primary and primary.published_doi)
        ):
            legacy = self._try(
                res, "OpenReview", s.openreview_search, title, True
            )
            matches += matching(legacy or [])
        return matches

    def _match_by_title(
        self, entry: Entry, res: Resolution, title: str, matches: list
    ) -> None:
        """No identifier: the best title match, else Semantic Scholar's."""
        res.primary = self._pick(matches, entry)
        if res.primary is None:
            hint = self._try(
                res, "Semantic Scholar", self.sources.s2_match, title
            )
            if hint and norm_title(hint.title) == norm_title(title):
                res.primary = hint
        res.via = "title" if res.primary else ""

    def _find_published(
        self, entry: Entry, res: Resolution, title: str, matches: list
    ) -> None:
        """The published version: the primary itself, or found for it."""
        primary = res.primary
        if not primary.preprint:
            res.published = primary
            return
        if primary.published_doi:
            res.published = self._try(
                res, "doi.org", self.sources.doi, primary.published_doi
            )
        if res.published is None:
            res.published = self._pick(
                [r for r in matches if not r.preprint], entry
            )
            if res.published:
                res.published = self._upgrade(res, res.published)
        if res.published is None and not primary.hint_only:
            hint = self._try(
                res, "Semantic Scholar", self.sources.s2_match, title
            )
            if (
                hint
                and not hint.preprint
                and norm_title(hint.title) == norm_title(title)
            ):
                res.hint = hint

    @staticmethod
    def _add_references(res: Resolution, matches: list[Record]) -> None:
        """DBLP and arXiv records, to check authors against."""
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
