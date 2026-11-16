"""Check a reference's URL, or suggest one, for entries the indexes lack.

Web pages, legislation, reports, and theses are rarely on Crossref or arXiv.
For those, a URL that loads and shows the cited title is the best automatic
evidence; a person still has to confirm it. Every step is recorded so a
person can redo it.
"""

import json
import logging
import re
import subprocess
import sys
from dataclasses import dataclass, field
from urllib.parse import quote_plus, urlparse

from bs4 import BeautifulSoup

from .bibtex import Entry
from .resolve import Resolution
from .sources import (
    DSPACE_REPOSITORIES,
    NotFoundError,
    Page,
    SourceError,
    Sources,
    Work,
)
from .text import (
    delatex,
    norm_title,
    same_paper,
    split_authors,
    strip_accents,
)

log = logging.getLogger(__name__)
# Seconds a PDF may take to read.
PDF_TIMEOUT = 20

OK, MISMATCH, UNVERIFIED, BROKEN = "ok", "mismatch", "unverified", "broken"

# Hosts whose links are already checked through the entry's DOI or arXiv id.
_ID_HOSTS = {"doi.org", "dx.doi.org", "arxiv.org", "www.arxiv.org"}
_URL_RE = re.compile(r"\\url\{([^}]+)\}|(https?://[^\s{}\\]+)")
# Pages that are a bot check rather than the content.
_CHALLENGE_RE = re.compile(
    r"not a bot|just a moment|attention required|access denied|captcha|"
    r"checking your browser|verify you are (a )?human|are you a robot|"
    r"request rejected|security check",
    re.I,
)
_BLOCKED = {401, 402, 403, 406, 429, 451, 999}
_GONE = {404, 410}
_STOPWORDS = set(
    "a an and as at by for from in into is of on or the to with via its"
    " their this that".split()
)
# Share of the cited title's words a page heading must contain.
_HEADING_SHARE = 0.6
# Or, failing that, the share the page text must contain.
_TEXT_SHARE = 0.85


@dataclass
class Verdict:
    """What checking one URL found.

    Attributes:
        url: The URL checked.
        status: ``ok`` (reachable and shows the title), ``mismatch``
            (reachable, but about something else), ``unverified`` (bot
            check, login wall, timeout: open it yourself), or ``broken``
            (404, 410, or the host doesn't exist).
        reason: One line on why, e.g. ``HTTP 404``.
        how: For suggestions, where the URL came from.
        doi: For suggestions, the work's DOI if it has one (better than a
            URL in a .bib).

    """

    url: str
    status: str
    reason: str = ""
    how: str = ""
    doi: str = ""


@dataclass(frozen=True)
class ManualLink:
    """A link for checking a reference by hand, e.g. a web search."""

    name: str
    url: str

    def __str__(self) -> str:
        """``name: url``, as the workings print it."""
        return f"{self.name}: {self.url}"


@dataclass
class LinkCheck:
    """The URL evidence for one entry.

    Attributes:
        given: The entry's own URL, checked (None if it has none).
        suggestion: A URL found for the entry (None if none was found or
            needed).
        steps: Everything that was tried, in order, to redo by hand.
        manual: Links for checking by hand.

    """

    given: Verdict | None = None
    suggestion: Verdict | None = None
    steps: list[str] = field(default_factory=list)
    manual: list[ManualLink] = field(default_factory=list)


def entry_urls(entry: Entry) -> list[str]:
    """The entry's URLs, except DOI and arXiv links (checked by id)."""
    found = []
    for name in ("url", "howpublished", "note"):
        value = entry.fields.get(name, "")
        if name == "url" and value.strip():
            candidates = [value.strip()]
        else:
            candidates = [a or b for a, b in _URL_RE.findall(value)]
        for url in candidates:
            url = (
                url.replace("\\_", "_").replace("\\%", "%").replace("\\#", "#")
            )
            url = url.rstrip(".,;")
            if url.startswith("www."):
                url = "https://" + url
            host = urlparse(url).netloc.lower()
            if host and host not in _ID_HOSTS and url not in found:
                found.append(url)
    return found


# --- EU legislation ----------------------------------------------------------

_EU_TYPES = {"regulation": "R", "directive": "L", "decision": "D"}
# Regulation (EU) 2024/2847; Regulation (EU) No 1025/2012; Directive
# (EU) 2022/2555; Regulation (EU, Euratom) 2018/1046.
_EU_NEW_RE = re.compile(
    r"\b(regulation|directive|decision)\s*\((?:EU|EC|EEC|EU,\s*Euratom)\)"
    r"\s*(No\.?\s*)?(\d{1,4})/(\d{1,4})\b",
    re.I,
)
# Directive 95/46/EC; Directive 2009/24/EC.
_EU_OLD_RE = re.compile(
    r"\b(directive|decision)\s+(\d{2}|\d{4})/(\d{1,4})/(?:EC|EEC|EU)\b", re.I
)
_CELEX_URL_RE = re.compile(r"CELEX[:%3A]+(\d{5}[A-Z]\d{4})", re.I)
_ELI_URL_RE = re.compile(r"/eli/(reg|dir|dec)/(\d{4})/(\d+)", re.I)


def _year(y: str) -> str:
    return y if len(y) == 4 else ("19" if int(y) > 50 else "20") + y


def eu_celex(entry: Entry, urls: list[str]) -> str:
    """The CELEX number of an EU act the entry cites, if it cites one."""
    for url in urls:
        if m := _CELEX_URL_RE.search(url):
            return m[1].upper()
        if m := _ELI_URL_RE.search(url):
            kind = {"reg": "R", "dir": "L", "dec": "D"}[m[1].lower()]
            return f"3{m[2]}{kind}{int(m[3]):04d}"
    f = entry.fields
    text = delatex(" ".join(f.get(k, "") for k in ("title", "howpublished")))
    if m := _EU_NEW_RE.search(text):
        kind = _EU_TYPES[m[1].lower()]
        a, b = m[3], m[4]
        # "No 1025/2012" is number/year (before 2015); "2024/2847" is
        # year/number.
        year, number = (b, a) if m[2] else (a, b)
        if not m[2] and len(a) != 4 and len(b) == 4:
            year, number = b, a
        return f"3{_year(year)}{kind}{int(number):04d}"
    if m := _EU_OLD_RE.search(text):
        kind = _EU_TYPES[m[1].lower()]
        return f"3{_year(m[2])}{kind}{int(m[3]):04d}"
    return ""


def eur_lex_url(celex: str) -> str:
    """The EUR-Lex page for an act."""
    return f"https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:{celex}"


# --- Matching pages against titles -------------------------------------------


def words(text: str) -> set[str]:
    """Significant lowercase words of ``text``, accents dropped."""
    plain = strip_accents(delatex(text))
    return {
        w
        for w in re.findall(r"[a-z0-9]+", plain)
        if w not in _STOPWORDS and (len(w) > 1 or w.isdigit())
    }


def share(title: str, text: str) -> tuple[int, int]:
    """(found, total): how many of ``title``'s words appear in ``text``."""
    want = words(title)
    return len(want & words(text)), len(want)


def page_text(page: Page) -> tuple[list[str], str]:
    """A page's headings (title, h1, citation metadata) and its text."""
    if page.content_type == "application/pdf" or page.body.startswith(b"%PDF"):
        return _pdf_text(page.body)
    soup = BeautifulSoup(page.body, "html.parser")
    heads = []
    if soup.title and soup.title.string:
        heads.append(soup.title.string)
    for meta in soup.find_all("meta"):
        name = (meta.get("name") or meta.get("property") or "").lower()
        if name in {"og:title", "citation_title", "dc.title", "twitter:title"}:
            heads.append(meta.get("content") or "")
    heads += [h.get_text(" ") for h in soup.find_all(["h1", "h2"])[:10]]
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    heads = [" ".join(h.split()) for h in heads if h and h.strip()]
    return heads, " ".join(soup.get_text(" ").split())[:200_000]


def _pdf_text(body: bytes) -> tuple[list[str], str]:
    """A PDF's title and first pages' text, parsed in a separate process.

    See :mod:`._pdftext`: untrusted PDFs never reach MuPDF in this process.
    """
    try:
        done = subprocess.run(
            [sys.executable, "-I", "-m", "papertools.bibcheck._pdftext"],
            input=body,
            capture_output=True,
            timeout=PDF_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        log.warning("PDF text extraction failed", exc_info=True)
        return [], ""
    if done.returncode != 0:
        return [], ""
    try:
        found = json.loads(done.stdout)
    except ValueError:
        return [], ""
    return found.get("heads", []), found.get("text", "")


def judge_page(title: str, page: Page) -> tuple[Verdict, list[str]]:
    """Decide whether ``page`` is the reference titled ``title``."""
    steps = []
    hop = (
        f" (redirected to {page.final_url})"
        if page.final_url and page.final_url != page.url
        else ""
    )
    if page.status is None:
        steps.append(f"GET {page.url}: {page.error}")
        status = BROKEN if page.error_kind == "dns" else UNVERIFIED
        if page.error_kind == "private":
            reason = "not fetched: not a public address"
        else:
            reason = page.error
        return Verdict(page.url, status, reason), steps
    ctype = f" {page.content_type}" if page.content_type else ""
    steps.append(f"GET {page.url}: HTTP {page.status}{ctype}{hop}")
    heads, text = page_text(page) if page.body else ([], "")
    if heads:
        steps.append(f"page title: {heads[0][:150]!r}")
    challenge = any(_CHALLENGE_RE.search(h) for h in heads[:1]) or (
        page.status in {202, 503} and not text
    )
    if page.status in _GONE:
        return Verdict(page.url, BROKEN, f"HTTP {page.status}"), steps
    if page.status in _BLOCKED or challenge:
        why = "bot check" if challenge else f"HTTP {page.status}"
        steps.append(f"{why}: the site refuses scripts; open it in a browser")
        return Verdict(page.url, UNVERIFIED, f"blocked ({why})"), steps
    if page.status >= 400:
        return Verdict(page.url, UNVERIFIED, f"HTTP {page.status}"), steps
    best = max((share(title, h) for h in heads), default=(0, 0))
    total = best[1] or len(words(title))
    steps.append(
        f"title words in the page title or headings: {best[0]}/{total}"
    )
    if total and best[0] / total >= _HEADING_SHARE:
        return Verdict(page.url, OK, "page title matches"), steps
    in_text = share(title, text)
    steps.append(f"title words in the page text: {in_text[0]}/{in_text[1]}")
    if in_text[1] >= 3 and in_text[0] / in_text[1] >= _TEXT_SHARE:
        return Verdict(page.url, OK, "page text has the title"), steps
    shown = f" ({heads[0][:80]!r})" if heads else ""
    return (
        Verdict(page.url, MISMATCH, f"page is about something else{shown}"),
        steps,
    )


# --- The checker -------------------------------------------------------------


class LinkChecker:
    """Check entries' URLs and suggest URLs for unindexed entries."""

    def __init__(self, sources: Sources):
        self.sources = sources

    def check(self, entry: Entry, res: Resolution) -> LinkCheck | None:
        """Check ``entry``'s URL; suggest one if it's unindexed and has none.

        Entries found in the indexes only get their URL checked (DOI and
        arXiv links are skipped: the ids were already looked up).
        """
        indexed = res.primary is not None
        urls = entry_urls(entry)
        if not self.sources.can_fetch_pages or (indexed and not urls):
            return None
        title = entry.fields.get("title", "")
        out = LinkCheck()
        celex = eu_celex(entry, urls)
        if urls:
            out.given = self._verify(entry, urls[0], title, celex, out.steps)
        if not indexed and (out.given is None or out.given.status != OK):
            out.suggestion = self._suggest(entry, title, celex, out)
        if (
            out.given
            and out.given.status != OK
            or not (out.given or out.suggestion)
        ):
            out.manual = manual_links(entry, out.given)
        return out

    def _verify(
        self, entry: Entry, url: str, title: str, celex: str, steps: list[str]
    ) -> Verdict:
        if celex and urlparse(url).netloc.endswith("europa.eu"):
            if verdict := self._eu_act(celex, title, url, steps):
                return verdict
        verdict, more = judge_page(title, self.sources.page(url))
        steps += more
        if verdict.status == OK:
            return verdict
        # Repository pages are often behind a bot check; their APIs aren't.
        host = urlparse(url).netloc.lower()
        if host == "hdl.handle.net" or host in DSPACE_REPOSITORIES:
            for work, name in self._repository_matches(entry, title, steps):
                if work.handle and work.handle in url:
                    return Verdict(
                        url, OK, f"{name} record {work.handle} has this title"
                    )
        return verdict

    def _repository_matches(
        self, entry: Entry, title: str, steps: list[str]
    ) -> list[tuple[Work, str]]:
        """Items titled ``title`` in the DSpace repositories, by our authors."""
        ours = split_authors(entry.fields.get("author", ""))
        out = []
        for host, name in DSPACE_REPOSITORIES.items():
            try:
                works = self.sources.dspace_search(host, title)
            except SourceError as exc:
                steps.append(f"{name}: {exc}")
                continue
            hits = [
                w
                for w in works
                if norm_title(w.title) == norm_title(title)
                and (not ours or not w.authors or same_paper(ours, w.authors))
            ]
            for w in hits:
                kind = ", ".join(x for x in (w.type, w.year) if x)
                steps.append(
                    f"{name}: {w.title!r} ({kind}), handle {w.handle}, "
                    f"at {w.url}"
                )
                out.append((w, name))
            if not hits:
                steps.append(
                    f"{name} (https://{host}/server/api/discover/search/"
                    f"objects?query=...): no item titled {title!r}"
                )
        return out

    def _eu_act(
        self, celex: str, title: str, url: str, steps: list[str]
    ) -> Verdict | None:
        """Verify an EU act through the Publications Office."""
        try:
            official = self.sources.eu_act_title(celex)
        except NotFoundError:
            steps.append(f"EU Publications Office: no act with CELEX {celex}")
            return Verdict(url, BROKEN, f"no EU act {celex}")
        except SourceError as exc:
            steps.append(f"EU Publications Office: {exc}")
            return None
        steps.append(
            f"EU Publications Office, CELEX {celex}: {official[:200]!r}"
        )
        found, total = share(title, official)
        steps.append(f"title words in the official title: {found}/{total}")
        if total and found / total >= _HEADING_SHARE:
            return Verdict(url, OK, f"EU act {celex} has this title")
        return Verdict(
            url, MISMATCH, f"EU act {celex} has another title: {official!r}"
        )

    def _suggest(
        self, entry: Entry, title: str, celex: str, out: LinkCheck
    ) -> Verdict | None:
        steps = out.steps
        if celex:
            url = eur_lex_url(celex)
            if out.given is None or out.given.url != url:
                verdict = self._eu_act(celex, title, url, steps)
                if verdict and verdict.status == OK:
                    verdict.how = "EUR-Lex, from the act's number"
                    return verdict
        if title:
            if verdict := self._openalex(entry, title, steps):
                return verdict
            for work, name in self._repository_matches(entry, title, steps):
                kind = work.type or "record"
                how = f"{name} {kind}" + (
                    f" with DOI {work.doi}: add doi={{{work.doi}}}"
                    if work.doi
                    else ""
                )
                return Verdict(
                    work.url, OK, f"{name} has this title", how, work.doi
                )
        if out.given and out.given.status in {BROKEN, MISMATCH}:
            return self._archived(out.given.url, entry, title, steps)
        return None

    def _openalex(
        self, entry: Entry, title: str, steps: list[str]
    ) -> Verdict | None:
        try:
            works = self.sources.openalex_search(title)
        except SourceError as exc:
            steps.append(f"OpenAlex: {exc}")
            return None
        ours = split_authors(entry.fields.get("author", ""))
        for w in works:
            if norm_title(w.title) != norm_title(title):
                continue
            if ours and w.authors and not same_paper(ours, w.authors):
                steps.append(
                    f"OpenAlex: {w.title!r} has other authors "
                    f"({', '.join(w.authors[:3])}); not used"
                )
                continue
            url = f"https://doi.org/{w.doi}" if w.doi else w.url
            if not url:
                continue
            kind = ", ".join(x for x in (w.type, w.year) if x)
            steps.append(f"OpenAlex: {w.title!r} ({kind}) at {url}")
            how = f"OpenAlex {w.type or 'record'}" + (
                f" with DOI {w.doi}: add doi={{{w.doi}}}" if w.doi else ""
            )
            return Verdict(url, OK, "OpenAlex has this title", how, w.doi)
        steps.append(f"OpenAlex: no work titled {title!r}")
        return None

    def _archived(
        self, url: str, entry: Entry, title: str, steps: list[str]
    ) -> Verdict | None:
        try:
            snap, date = self.sources.wayback(url, entry.fields.get("year", ""))
        except NotFoundError:
            steps.append(f"Wayback Machine: no snapshot of {url}")
            return None
        except SourceError as exc:
            steps.append(f"Wayback Machine: {exc}")
            return None
        steps.append(f"Wayback Machine: snapshot from {date} at {snap}")
        verdict, more = judge_page(title, self.sources.page(snap))
        steps += more
        verdict.how = f"archived copy from {date} (Wayback Machine)"
        return verdict if verdict.status in {OK, UNVERIFIED} else None


def manual_links(entry: Entry, given: Verdict | None) -> list[ManualLink]:
    """Links that let a person check the reference by hand."""
    title = delatex(entry.fields.get("title", "")).strip()
    raw = entry.fields.get("author", "").strip()
    first = re.split(r"\s+and\s+", raw)[0] if raw else ""
    if first.startswith("{") and first.endswith("}"):
        who = delatex(first).strip()  # An organization: {IBM Security}.
    else:
        authors = split_authors(raw)
        who = authors[0].split()[-1] if authors else ""
    query = quote_plus(f'"{title}" {who}'.strip())
    out = []
    if given:
        out.append(ManualLink("open", given.url))
        out.append(
            ManualLink(
                "archived copies", f"https://web.archive.org/web/*/{given.url}"
            )
        )
    if title:
        out.append(
            ManualLink("web search", f"https://duckduckgo.com/?q={query}")
        )
        out.append(
            ManualLink(
                "Google Scholar",
                f"https://scholar.google.com/scholar?q={query}",
            )
        )
    return out
