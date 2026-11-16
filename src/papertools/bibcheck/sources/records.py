"""What sources return: records, works, and facts about each source."""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class SourceInfo:
    """What a record's source is, as far as checking and fixing care.

    Attributes:
        name: The source, for people ("Crossref").
        label: Prefix of a record's label ("doi" in "doi 10.1/x").
        bibtex: Where its BibTeX comes from: ``embedded`` in the record,
            fetched by ``doi``, or "" (none).
        hint_only: Its records only say a paper exists and where it was
            published (Semantic Scholar): no authoritative metadata, so
            they're never compared with, or used to rebuild, an entry.

    """

    name: str
    label: str
    bibtex: str = ""
    hint_only: bool = False


SOURCE_INFO = {
    "crossref": SourceInfo("Crossref", "doi", bibtex="doi"),
    "arxiv": SourceInfo("arXiv", "arXiv", bibtex="doi"),
    "openreview": SourceInfo("OpenReview", "openreview", bibtex="embedded"),
    "dblp": SourceInfo("DBLP", "dblp", bibtex="embedded"),
    "s2": SourceInfo("Semantic Scholar", "Semantic Scholar", hint_only=True),
}


@dataclass
class Record:
    """Bibliographic metadata from one source.

    Attributes:
        source: ``crossref`` (any DOI), ``arxiv``, ``openreview``, ``dblp``
            (OpenReview's mirror), or ``s2`` (Semantic Scholar).
        id: DOI, arXiv id, OpenReview note id, or Semantic Scholar id.
        preprint: True for arXiv/CoRR records.
        bibtex: Raw BibTeX when the source embeds it (OpenReview, DBLP).
        published_doi: For arXiv records, the DOI of the published version
            if the authors registered one.

    """

    source: str
    id: str
    title: str
    authors: list[str] = field(default_factory=list)
    venue: str = ""
    year: str = ""
    pages: str = ""
    doi: str = ""
    preprint: bool = False
    bibtex: str | None = None
    published_doi: str = ""
    other_years: set[str] = field(default_factory=set)

    @property
    def info(self) -> SourceInfo:
        return SOURCE_INFO[self.source]

    @property
    def hint_only(self) -> bool:
        """Only a hint (see :class:`SourceInfo`); never authoritative."""
        return self.info.hint_only

    @property
    def label(self) -> str:
        """Short human-readable identifier, e.g. ``doi 10.1000/...``."""
        return f"{self.info.label} {self.id}"

    @property
    def where(self) -> str:
        """Venue and year, e.g. ``ICLR 2024 oral``."""
        if self.year and self.year not in self.venue:
            return f"{self.venue} {self.year}".strip()
        return self.venue


@dataclass
class Work:
    """An OpenAlex or repository work: any output, including theses."""

    title: str
    type: str = ""
    year: str = ""
    doi: str = ""
    url: str = ""
    authors: list[str] = field(default_factory=list)
    handle: str = ""
