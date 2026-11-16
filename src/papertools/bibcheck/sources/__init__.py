"""Clients for the bibliographic sources bibcheck consults.

Every lookup returns :class:`Record` objects with authors as ``First Last``
Unicode strings, so they can be compared directly.

Source notes:

- dblp.org serves scripts a JavaScript bot challenge, so DBLP records come from
  OpenReview's ``DBLP.org`` mirror instead.
- OpenReview's ``/notes?id=`` sits behind a challenge too; ``/notes/search``
  doesn't, so OpenReview is searched by title and matched by id.
- PMLR (ICML, AISTATS, CoRL) mints no DOIs and has no standalone ``.bib``
  URL; Semantic Scholar is used only to notice that such a paper was
  published, never as a BibTeX source.
- Crossref's IEEE venues read ``2022 IEEE/CVF Conference on ... (CVPR)``.
"""

from .arxiv import ARXIV_ID, Arxiv
from .crossref import Crossref
from .dspace import DSPACE_REPOSITORIES, DSpace
from .errors import NotFoundError, OperationCancelledError, SourceError
from .eurlex import EUPublications
from .http import (
    Http,
    HttpClient,
    Page,
    _UnsafeURLError,  # noqa: F401  (tests)
    check_public_url,
    looks_empty,
)
from .openalex import OpenAlex
from .openreview import OpenReview
from .records import SOURCE_INFO, Record, SourceInfo, Work
from .semanticscholar import SemanticScholar
from .wayback import Wayback

__all__ = [
    "ARXIV_ID",
    "DSPACE_REPOSITORIES",
    "SOURCE_INFO",
    "Http",
    "HttpClient",
    "NotFoundError",
    "OperationCancelledError",
    "Page",
    "Record",
    "SourceError",
    "SourceInfo",
    "Sources",
    "Work",
    "check_public_url",
    "looks_empty",
    "record_bibtex",
]


class Sources:
    """Every source, sharing one HTTP client.

    Each source is its own object (``crossref``, ``arxiv``, ...); the
    methods here are the names the resolver, link checker, and fixer use.
    """

    def __init__(self, get: HttpClient):
        self.get = get
        self.crossref = Crossref(get)
        self.arxiv = Arxiv(get)
        self.openreview = OpenReview(get)
        self.semantic_scholar = SemanticScholar(get)
        self.openalex = OpenAlex(get)
        self.dspace = DSpace(get)
        self.eu = EUPublications(get)
        self.wayback_machine = Wayback(get)

    @property
    def can_fetch_pages(self) -> bool:
        """Whether the client can fetch pages (link checks).

        ``HttpClient`` requires ``page``; this guards plain callables (old
        test doubles) that only answer API requests.
        """
        return callable(getattr(self.get, "page", None))

    def page(self, url: str) -> Page:
        """Fetch a reference's link; never raises for HTTP errors."""
        return self.get.page(url)

    def doi(self, doi: str) -> Record:
        return self.crossref.doi(doi)

    def bibtex(self, doi: str) -> str:
        return self.crossref.bibtex(doi)

    def crossref_search(self, title: str) -> list[Record]:
        return self.crossref.search(title)

    def arxiv_id(self, arxiv_id: str) -> Record:
        return self.arxiv.by_id(arxiv_id)

    def arxiv_search(self, title: str) -> list[Record]:
        return self.arxiv.search(title)

    def openreview_search(self, title: str, legacy: bool = False):
        return self.openreview.search(title, legacy)

    def s2_match(self, title: str) -> Record:
        return self.semantic_scholar.match(title)

    def openalex_search(self, title: str) -> list[Work]:
        return self.openalex.search(title)

    def dspace_search(self, host: str, title: str) -> list[Work]:
        return self.dspace.search(host, title)

    def eu_act_title(self, celex: str) -> str:
        return self.eu.act_title(celex)

    def wayback(self, url: str, year: str = "") -> tuple[str, str]:
        return self.wayback_machine.closest(url, year)


def record_bibtex(sources: Sources, record: Record) -> str | None:
    """Raw BibTeX for a record, or None if its source has none."""
    how = record.info.bibtex
    if how == "embedded":
        return record.bibtex
    if how == "doi":
        return sources.bibtex(record.doi)
    return None
