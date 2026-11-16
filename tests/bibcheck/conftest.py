"""A fake web of Crossref, arXiv, OpenReview, and Semantic Scholar replies."""

import json
from contextlib import contextmanager
from urllib.parse import urlparse

import pytest

from papertools.bibcheck import cli
from papertools.bibcheck.sources import (
    NotFoundError,
    OperationCancelledError,
    Page,
    SourceError,
)
from papertools.bibcheck.text import norm_title

PAPER_CSL = {
    "DOI": "10.1000/example.2022.00001",
    "type": "paper-conference",
    "title": "Learning Example Representations at Scale",
    "container-title": "2022 IEEE/CVF Conference on Computer Vision and "
    "Pattern Recognition (CVPR)",
    "issued": {"date-parts": [[2022, 6]]},
    "page": "15979-15988",
    "author": [
        {"given": "Alice", "family": "Smith"},
        {"given": "Jan", "family": "Novak"},
    ],
}
PAPER_BIBTEX = (
    " @inproceedings{Smith_2022, title={Learning Example Representations at"
    " Scale}, url={http://dx.doi.org/10.1000/example.2022.00001},"
    " DOI={10.1000/example.2022.00001}, booktitle={2022 IEEE/CVF Conference"
    " on Computer Vision and Pattern Recognition (CVPR)}, publisher={IEEE},"
    " author={Smith, Alice and Novak, Jan}, year={2022}, month=June,"
    " pages={15979–15988} }"
)
PAPER_DBLP_NOTE = {
    "id": "dblpPAPER",
    "content": {
        "title": {"value": "Learning Example Representations at Scale"},
        "venue": {"value": "CVPR 2022"},
        "venueid": {"value": "dblp.org/conf/CVPR/2022"},
        "authors": {"value": ["Alice Smith", "Jan Novák"]},
        "_bibtex": {
            "value": "@inproceedings{DBLP:conf/cvpr/SmithN22,\n"
            "  author={Alice Smith and Jan Novák},\n"
            "  title={Learning Example Representations at Scale},\n"
            "  year={2022},\n  pages={15979-15988},\n"
            "  url={https://doi.org/10.1000/EXAMPLE.2022.00001},\n"
            "  booktitle={CVPR}\n}"
        },
    },
}
PREPRINT_TITLE = "Example Networks Need Placeholders"
PREPRINT_NOTE = {
    "id": "abcdEFGH12",
    "pdate": 1705410880910,
    "content": {
        "title": {"value": PREPRINT_TITLE},
        "venue": {"value": "ICLR 2024 oral"},
        "venueid": {"value": "ICLR.cc/2024/Conference"},
        "authors": {"value": ["Léa Moreau", "Bruno Garcia"]},
        "_bibtex": {
            "value": "@inproceedings{moreau2024example,\n"
            "title={Example Networks Need Placeholders},\n"
            "author={L{\\'e}a Moreau and Bruno Garcia},\n"
            "booktitle={The Twelfth International Conference on Learning "
            "Representations},\nyear={2024}\n}"
        },
    },
}
REJECTED_NOTE = {
    "id": "rejected1",
    "content": {
        "title": {"value": "Rejected Paper About Things"},
        "venue": {"value": "Submitted to ICLR 2024"},
        "venueid": {"value": "ICLR.cc/2024/Conference/Rejected_Submission"},
        "authors": {"value": ["Ann Author"]},
    },
}


def atom(aid: str, title: str, authors: list[str], doi: str = "") -> str:
    """One-entry arXiv Atom feed."""
    names = "".join(f"<author><name>{a}</name></author>" for a in authors)
    doi_xml = f"<arxiv:doi>{doi}</arxiv:doi>" if doi else ""
    return (
        '<feed xmlns="http://www.w3.org/2005/Atom" '
        'xmlns:arxiv="http://arxiv.org/schemas/atom"><entry>'
        f"<id>http://arxiv.org/abs/{aid}v2</id><title>{title}</title>"
        f"<published>2023-10-01T00:00:00Z</published>{names}{doi_xml}"
        "</entry></feed>"
    )


EMPTY_FEED = '<feed xmlns="http://www.w3.org/2005/Atom"></feed>'
PREPRINT_ATOM = atom(
    "2309.00001", PREPRINT_TITLE, ["Léa Moreau", "Bruno Garcia"]
)
PREPRINT_DATACITE = (
    "@misc{https://doi.org/10.48550/arxiv.2309.00001,\n"
    "  doi = {10.48550/ARXIV.2309.00001},\n"
    "  author = {Moreau, Léa and Garcia, Bruno},\n"
    "  title = {Example Networks Need Placeholders},\n"
    "  publisher = {arXiv},\n  year = {2023}\n}"
)


def _lookup(table: dict, title: str, default):
    """Find ``title`` in ``table`` the way a search engine would."""
    for key, value in table.items():
        if norm_title(key) == norm_title(title):
            return value
    return default


class FakeWeb:
    """Stands in for ``Http`` (the ``HttpClient`` protocol)."""

    cache = None

    @contextmanager
    def using(self, *, refresh=None, on_wait=None, cancel=None):
        """Per-run options; requests check cancellation, like ``Http``'s."""
        self._cancel = cancel
        try:
            yield
        finally:
            self._cancel = None

    def __init__(self):
        self.calls: list[str] = []
        self.down: set[str] = set()
        self.dois = {
            "10.1000/example.2022.00001": (PAPER_CSL, PAPER_BIBTEX),
            "10.48550/arxiv.2309.00001": (None, PREPRINT_DATACITE),
        }
        self.openreview = {
            "Learning Example Representations at Scale": [PAPER_DBLP_NOTE],
            PREPRINT_TITLE: [PREPRINT_NOTE, REJECTED_NOTE],
            "Rejected Paper About Things": [REJECTED_NOTE],
        }
        self.openreview_v1: dict[str, list[dict]] = {}
        self.arxiv_ids = {"2309.00001": PREPRINT_ATOM}
        self.arxiv_titles = {
            "Example Networks Need Placeholders": PREPRINT_ATOM
        }
        self.crossref: dict[str, list[dict]] = {}
        self.s2: dict[str, dict] = {}
        # Web pages by URL: (status, html) or a Page. Others don't exist.
        self.pages: dict[str, tuple[int, str] | Page] = {}
        self.openalex: dict[str, list[dict]] = {}
        self.eu_acts: dict[str, str] = {}  # CELEX -> English title
        self.wayback_snapshots: dict[str, tuple[str, str]] = {}
        # DSpace (RECIPP) items by title: (handle, authors, type, year).
        self.dspace: dict[str, tuple[str, list[str], str, str]] = {}

    def page(self, url):
        """Stand-in for ``Http.page``."""
        self.calls.append(urlparse(url).netloc)
        found = self.pages.get(url)
        if isinstance(found, Page):
            return found
        if found is None:
            host = urlparse(url).netloc
            return Page(
                url, url, error=f"host {host} does not exist", error_kind="dns"
            )
        status, body = found
        return Page(url, url, status, "text/html", body.encode())

    _cancel = None

    def maintain(self):
        """No cache to prune."""

    def __call__(self, url, params=None, accept=None):
        if self._cancel is not None and self._cancel.is_set():
            raise OperationCancelledError
        host = urlparse(url).netloc
        self.calls.append(host)
        if host in self.down:
            raise SourceError(f"{host}: HTTP 503")
        params = params or {}
        if host == "doi.org":
            doi = url.split("doi.org/", 1)[1].lower()
            if doi not in self.dois:
                raise NotFoundError(url)
            csl, bib = self.dois[doi]
            return bib if accept == "application/x-bibtex" else json.dumps(csl)
        if host == "api.crossref.org":
            items = _lookup(self.crossref, params["query.bibliographic"], [])
            return json.dumps({"message": {"items": items}})
        if host == "api.openreview.net":  # v1: venues before 2023
            notes = _lookup(self.openreview_v1, params["term"], [])
            return json.dumps({"notes": notes})
        if host == "api2.openreview.net":
            notes = _lookup(self.openreview, params["term"], [])
            return json.dumps({"notes": notes})
        if host == "export.arxiv.org":
            if "id_list" in params:
                return self.arxiv_ids.get(params["id_list"], EMPTY_FEED)
            title = params["search_query"].removeprefix("ti:").strip('"')
            return _lookup(self.arxiv_titles, title, EMPTY_FEED)
        if host == "api.semanticscholar.org":
            if paper := _lookup(self.s2, params["query"], None):
                return json.dumps({"data": [paper]})
            raise NotFoundError(url)
        if host == "api.openalex.org":
            works = _lookup(self.openalex, params["search"], [])
            return json.dumps({"results": works})
        if host == "publications.europa.eu":
            celex = params["query"].split('legal_id_celex "')[1].split('"')[0]
            title = self.eu_acts.get(celex)
            rows = [{"t": {"value": title}}] if title else []
            return json.dumps({"results": {"bindings": rows}})
        if host == "recipp.ipp.pt":
            title = params["query"]
            found = _lookup(self.dspace, title, None)
            objects = []
            if found:
                handle, authors, kind, year = found
                meta = {
                    "dc.title": [{"value": title}],
                    "dc.contributor.author": [{"value": a} for a in authors],
                    "dc.type": [{"value": kind}],
                    "dc.date.issued": [{"value": year}],
                    "dc.identifier.uri": [
                        {"value": f"http://hdl.handle.net/{handle}"}
                    ],
                }
                objects.append(
                    {
                        "_embedded": {
                            "indexableObject": {
                                "handle": handle,
                                "name": title,
                                "metadata": meta,
                            }
                        }
                    }
                )
            return json.dumps(
                {
                    "_embedded": {
                        "searchResult": {"_embedded": {"objects": objects}}
                    }
                }
            )
        if host == "archive.org":
            snap = self.wayback_snapshots.get(params["url"])
            closest = (
                {"available": True, "url": snap[0], "timestamp": snap[1]}
                if snap
                else None
            )
            return json.dumps({"archived_snapshots": {"closest": closest}})
        raise AssertionError(f"unexpected request {url}")


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    """Keep tests away from the real config file, cache, and job folder."""
    monkeypatch.setenv("BIBCHECK_CONFIG", str(tmp_path / "no-config.toml"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    for name in (
        "CROSSREF_MAILTO",
        "OPENALEX_MAILTO",
        "SEMANTIC_SCHOLAR_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def web(monkeypatch):
    """Route bibcheck's HTTP through a FakeWeb."""
    fake = FakeWeb()
    monkeypatch.setattr(cli, "Http", lambda **kwargs: fake)
    return fake
