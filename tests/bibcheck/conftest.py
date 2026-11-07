"""A fake web of Crossref, arXiv, OpenReview, and Semantic Scholar replies."""

import json
from urllib.parse import urlparse

import pytest

from papertools.bibcheck import cli
from papertools.bibcheck.sources import NotFoundError, SourceError
from papertools.bibcheck.text import norm_title

MAE_CSL = {
    "DOI": "10.1109/cvpr52688.2022.01553",
    "type": "paper-conference",
    "title": "Masked Autoencoders Are Scalable Vision Learners",
    "container-title": "2022 IEEE/CVF Conference on Computer Vision and "
    "Pattern Recognition (CVPR)",
    "issued": {"date-parts": [[2022, 6]]},
    "page": "15979-15988",
    "author": [
        {"given": "Kaiming", "family": "He"},
        {"given": "Piotr", "family": "Dollar"},
    ],
}
MAE_BIBTEX = (
    " @inproceedings{He_2022, title={Masked Autoencoders Are Scalable Vision"
    " Learners}, url={http://dx.doi.org/10.1109/cvpr52688.2022.01553},"
    " DOI={10.1109/cvpr52688.2022.01553}, booktitle={2022 IEEE/CVF Conference"
    " on Computer Vision and Pattern Recognition (CVPR)}, publisher={IEEE},"
    " author={He, Kaiming and Dollar, Piotr}, year={2022}, month=June,"
    " pages={15979–15988} }"
)
MAE_DBLP_NOTE = {
    "id": "dblpMAE",
    "content": {
        "title": {"value": "Masked Autoencoders Are Scalable Vision Learners"},
        "venue": {"value": "CVPR 2022"},
        "venueid": {"value": "dblp.org/conf/CVPR/2022"},
        "authors": {"value": ["Kaiming He", "Piotr Dollár"]},
        "_bibtex": {
            "value": "@inproceedings{DBLP:conf/cvpr/HeD22,\n"
            "  author={Kaiming He and Piotr Dollár},\n"
            "  title={Masked Autoencoders Are Scalable Vision Learners},\n"
            "  year={2022},\n  pages={15979-15988},\n"
            "  url={https://doi.org/10.1109/CVPR52688.2022.01553},\n"
            "  booktitle={CVPR}\n}"
        },
    },
}
REG_TITLE = "Vision Transformers Need Registers"
REG_NOTE = {
    "id": "2dnO3LLiJ1",
    "pdate": 1705410880910,
    "content": {
        "title": {"value": REG_TITLE},
        "venue": {"value": "ICLR 2024 oral"},
        "venueid": {"value": "ICLR.cc/2024/Conference"},
        "authors": {"value": ["Timothée Darcet", "Maxime Oquab"]},
        "_bibtex": {
            "value": "@inproceedings{darcet2024vision,\n"
            "title={Vision Transformers Need Registers},\n"
            "author={Timoth{\\'e}e Darcet and Maxime Oquab},\n"
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
REG_ATOM = atom("2309.16588", REG_TITLE, ["Timothée Darcet", "Maxime Oquab"])
REG_DATACITE = (
    "@misc{https://doi.org/10.48550/arxiv.2309.16588,\n"
    "  doi = {10.48550/ARXIV.2309.16588},\n"
    "  author = {Darcet, Timothée and Oquab, Maxime},\n"
    "  title = {Vision Transformers Need Registers},\n"
    "  publisher = {arXiv},\n  year = {2023}\n}"
)


def _lookup(table: dict, title: str, default):
    """Find ``title`` in ``table`` the way a search engine would."""
    for key, value in table.items():
        if norm_title(key) == norm_title(title):
            return value
    return default


class FakeWeb:
    """Callable standing in for ``Http``; records every request."""

    def __init__(self):
        self.calls: list[str] = []
        self.down: set[str] = set()
        self.dois = {
            "10.1109/cvpr52688.2022.01553": (MAE_CSL, MAE_BIBTEX),
            "10.48550/arxiv.2309.16588": (None, REG_DATACITE),
        }
        self.openreview = {
            "Masked Autoencoders Are Scalable Vision Learners": [MAE_DBLP_NOTE],
            REG_TITLE: [REG_NOTE, REJECTED_NOTE],
            "Rejected Paper About Things": [REJECTED_NOTE],
        }
        self.arxiv_ids = {"2309.16588": REG_ATOM}
        self.arxiv_titles = {"Vision Transformers Need Registers": REG_ATOM}
        self.crossref: dict[str, list[dict]] = {}
        self.s2: dict[str, dict] = {}

    def __call__(self, url, params=None, accept=None):
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
        raise AssertionError(f"unexpected request {url}")


@pytest.fixture
def web(monkeypatch):
    """Route bibcheck's HTTP through a FakeWeb."""
    fake = FakeWeb()
    monkeypatch.setattr(cli, "Http", lambda **kwargs: fake)
    return fake
