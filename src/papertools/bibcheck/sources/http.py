"""The HTTP client: retries, per-host rate limits, cache, safe page fetches."""

import ipaddress
import json
import logging
import socket
import threading
import time
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeout
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from http.client import HTTPConnection, HTTPException, HTTPSConnection
from pathlib import Path
from typing import Protocol
from urllib.error import HTTPError
from urllib.parse import urlencode, urlparse
from urllib.request import (
    HTTPHandler,
    HTTPRedirectHandler,
    HTTPSHandler,
    ProxyHandler,
    Request,
    build_opener,
    urlopen,
)

from ... import __version__
from ..cache import DiskCache
from ..config import Config, load_config
from .errors import NotFoundError, OperationCancelledError, SourceError

log = logging.getLogger(__name__)

_RETRY_DELAYS = [2, 5, 10]
_MAX_RETRY_AFTER = 120
# Request budgets per host: (requests, per seconds). Staying under them is
# much faster than collecting 429s. arXiv asks for one request per 3 s;
# OpenReview allows 20 searches a minute on v2 and 5 on v1.
_RATE_LIMITS = {
    "export.arxiv.org": (1, 3.0),
    "api.semanticscholar.org": (1, 1.1),
    "api2.openreview.net": (20, 61.0),
    "api.openreview.net": (5, 61.0),
    "api.openalex.org": (5, 1.0),
    "publications.europa.eu": (2, 1.0),
    "archive.org": (1, 1.5),
    "web.archive.org": (1, 2.0),
    "recipp.ipp.pt": (2, 1.0),
}

_PAGE_RATE_LIMIT = (1, 1.0)
_PAGE_MAX_BYTES = 5_000_000


@dataclass
class Page:
    """What fetching a reference's URL returned.

    Attributes:
        url: The URL asked for.
        final_url: Where redirects ended.
        status: HTTP status, or None if no response arrived.
        content_type: The response's media type, e.g. ``text/html``.
        body: Up to the first 5 MB of the body.
        error: Why no response arrived.
        error_kind: ``dns`` (host does not exist), ``private`` (host is not
            on the public internet; not fetched), ``url`` (not http(s)), or
            ``network`` (timeouts, refused connections, TLS errors).

    """

    url: str
    final_url: str = ""
    status: int | None = None
    content_type: str = ""
    body: bytes = b""
    error: str = ""
    error_kind: str = ""


class _UnsafeURLError(Exception):
    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind


# NAT64 prefixes (RFC 6052, RFC 8215): the last 32 bits are an IPv4
# address, reachable through the NAT64 gateway.
_NAT64 = [
    ipaddress.ip_network("64:ff9b::/96"),
    ipaddress.ip_network("64:ff9b:1::/48"),
]


def is_public_address(address: str) -> bool:
    """Whether connecting to ``address`` reaches the public internet only."""
    ip = ipaddress.ip_address(address.split("%")[0])
    if ip.version == 6 and any(ip in net for net in _NAT64):
        ip = ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
    return ip.is_global


# getaddrinfo can't be given a timeout, so lookups run on these threads and
# are waited for at most DNS_TIMEOUT seconds: a domain whose DNS answers
# slowly on purpose can't hold the worker.
DNS_TIMEOUT = 5
_dns = ThreadPoolExecutor(max_workers=8, thread_name_prefix="bibcheck-dns")


def _resolve(host: str, port: int | None) -> list[tuple]:
    """Resolve ``host``; refuse it unless every address is public."""
    lookup = _dns.submit(
        socket.getaddrinfo, host, port, type=socket.SOCK_STREAM
    )
    try:
        infos = lookup.result(timeout=DNS_TIMEOUT)
    except FuturesTimeout as exc:
        log.info("DNS lookup for %r timed out", host)
        raise _UnsafeURLError(
            "network", f"looking up {host} took over {DNS_TIMEOUT}s"
        ) from exc
    except socket.gaierror as exc:
        if exc.errno in {socket.EAI_NONAME, getattr(socket, "EAI_NODATA", -1)}:
            raise _UnsafeURLError("dns", f"host {host} does not exist") from exc
        raise _UnsafeURLError(
            "network", f"cannot resolve {host}: {exc}"
        ) from exc
    for info in infos:
        if not is_public_address(str(info[4][0])):
            log.warning("refused to fetch %r: not a public address", host)
            raise _UnsafeURLError("private", f"{host} is not a public address")
    return infos


# Ports link checks use unless ``sources.any_link_port`` is set.
WEB_PORTS = {80, 443}


def check_public_url(url: str, any_port: bool = True) -> None:
    """Refuse URLs that aren't http(s) on a public internet address.

    Pages are fetched on behalf of whoever submitted the .bib, who may not
    be trusted (when bibcheck runs as a service), so loopback, private, and
    link-local addresses (cloud metadata services) must never be reached.
    This early check gives a clear reason; the connection itself is checked
    again (see ``_connect_public``), since DNS can answer differently the
    second time.
    """
    parts = urlparse(url)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise _UnsafeURLError("url", f"not an http(s) URL: {url}")
    try:
        port = parts.port
    except ValueError as exc:
        raise _UnsafeURLError("url", f"bad port in {url}") from exc
    if not any_port and port is not None and port not in WEB_PORTS:
        raise _UnsafeURLError(
            "port", f"not fetched: port {port} (only 80 and 443 are used)"
        )
    _resolve(parts.hostname, port)


def _connect_public(address, timeout=socket._GLOBAL_DEFAULT_TIMEOUT, *_):
    """``socket.create_connection``, to a checked address only.

    Resolves once, refuses non-public answers, and connects to exactly the
    address checked: an attacker's DNS can't answer "public" to the check
    and "127.0.0.1" to the connection (DNS rebinding).
    """
    host, port = address
    error: OSError | None = None
    for family, kind, proto, _, sockaddr in _resolve(host, port):
        sock = socket.socket(family, kind, proto)
        try:
            if timeout is not socket._GLOBAL_DEFAULT_TIMEOUT:
                sock.settimeout(timeout)
            sock.connect(sockaddr)
            return sock
        except OSError as exc:
            error = exc
            sock.close()
    raise error or OSError(f"cannot connect to {host}")


class _PublicHTTPConnection(HTTPConnection):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._create_connection = _connect_public


class _PublicHTTPSConnection(HTTPSConnection):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._create_connection = _connect_public


class _PublicHTTPHandler(HTTPHandler):
    def http_open(self, req):
        return self.do_open(_PublicHTTPConnection, req)


class _PublicHTTPSHandler(HTTPSHandler):
    def https_open(self, req):
        return self.do_open(_PublicHTTPSConnection, req, context=self._context)


class _CheckedRedirects(HTTPRedirectHandler):
    def __init__(self, any_port: bool):
        super().__init__()
        self.any_port = any_port

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        check_public_url(newurl, self.any_port)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _page_opener(any_port: bool = True):
    """An opener that only ever connects to public addresses.

    Proxies from the environment are not used: the check must apply to the
    page's host, not to the proxy's.
    """
    return build_opener(
        ProxyHandler({}),
        _PublicHTTPHandler,
        _PublicHTTPSHandler,
        _CheckedRedirects(any_port),
    )


class _TooSlowError(Exception):
    """A page took longer than its deadline."""


def _read_until(response, deadline: float) -> bytes:
    """Up to _PAGE_MAX_BYTES of ``response``, before ``deadline``.

    A per-read timeout alone lets a server send a byte every few seconds
    for as long as it likes.
    """
    # read1 returns what has arrived; read(n) would block until n bytes
    # came, so the deadline would only be checked between large reads.
    read = getattr(response, "read1", response.read)
    chunks, size = [], 0
    while size < _PAGE_MAX_BYTES:
        if time.monotonic() > deadline:
            raise _TooSlowError
        chunk = read(min(65536, _PAGE_MAX_BYTES - size))
        if not chunk:
            break
        chunks.append(chunk)
        size += len(chunk)
    return b"".join(chunks)


class HttpClient(Protocol):
    """What :class:`Sources` needs from an HTTP client (``Http``, or a fake).

    ``__call__`` returns a body or raises :class:`SourceError` /
    :class:`NotFoundError`; ``page`` fetches a reference's link and never
    raises for HTTP errors; ``using`` sets per-run options for the calls
    made inside it.
    """

    def __call__(
        self, url: str, params: dict | None = None, accept: str | None = None
    ) -> str: ...

    def page(self, url: str) -> "Page": ...

    def using(
        self,
        *,
        refresh: bool | None = None,
        on_wait: Callable[[str], None] | None = None,
        cancel: threading.Event | None = None,
    ): ...

    def maintain(self) -> None: ...


@dataclass(frozen=True)
class _RunOptions:
    refresh: bool | None = None
    on_wait: Callable[[str], None] | None = None
    cancel: threading.Event | None = None


# Options for the calls made inside ``Http.using``. A context variable, so
# concurrent checks can share one client (and its rate limits and cache)
# without sharing settings.
_RUN: ContextVar[_RunOptions] = ContextVar("bibcheck_http_run")


class Http:
    """GET with retries, per-host throttling, and an optional disk cache.

    Args:
        timeout: Seconds per request.
        cache_dir: Where to cache answers (see :mod:`.cache`); None for no
            cache.
        config: Settings: cache lifetimes and size cap, contact email,
            API key (see :mod:`.config`). Read from the config file if None.
        on_wait: Called with a reason when a request has to wait.
        refresh: Ignore cached answers, but still store fresh ones, so
            they replace the old ones. Can be changed between requests.

    Identifying the client gets faster, more reliable service: the
    contact email goes to Crossref and OpenAlex, and the API key to Semantic
    Scholar.

    """

    def __init__(
        self,
        *,
        timeout: float = 30,
        cache_dir: Path | None = None,
        on_wait: Callable[[str], None] | None = None,
        refresh: bool = False,
        config: Config | None = None,
    ):
        config = config or load_config()
        self.timeout = timeout
        self.cache_dir = cache_dir
        self.cache = None
        if cache_dir:
            self.cache = DiskCache.from_config(config.cache, Path(cache_dir))
        if self.cache:
            self.cache.maybe_prune()
        self.on_wait = on_wait
        self.refresh = refresh
        self._sent: dict[str, list[float]] = {}
        agent = f"papertools/{__version__}"
        # Crossref's "polite pool" is faster for clients that identify.
        if mailto := config.sources.crossref_mailto:
            agent += f" (mailto:{mailto})"
        self.user_agent = agent
        self.openalex_mailto = config.sources.openalex_mailto
        self.s2_key = config.sources.s2_key
        self.link_timeout = config.sources.link_timeout
        self.any_link_port = config.sources.any_link_port

    @contextmanager
    def using(
        self,
        *,
        refresh: bool | None = None,
        on_wait: Callable[[str], None] | None = None,
        cancel: threading.Event | None = None,
    ) -> Iterator[None]:
        """Run the calls inside with these options (None: the client's).

        Args:
            refresh: Ignore cached answers (and store fresh ones).
            on_wait: Told why a request waits (rate limit, retry).
            cancel: When set, waits end and the next request raises
                :class:`OperationCancelledError`.

        """
        token = _RUN.set(_RunOptions(refresh, on_wait, cancel))
        try:
            yield
        finally:
            _RUN.reset(token)

    def maintain(self) -> None:
        """Housekeeping for long-running users: prune the cache if due."""
        if self.cache:
            self.cache.maybe_prune()

    @staticmethod
    def _run() -> _RunOptions:
        return _RUN.get(_RunOptions())

    def _refreshing(self) -> bool:
        run = self._run().refresh
        return self.refresh if run is None else run

    def _check_cancel(self) -> None:
        cancel = self._run().cancel
        if cancel is not None and cancel.is_set():
            raise OperationCancelledError

    def _cached(self, key: str) -> tuple[str, bytes]:
        if self.cache is None or self._refreshing():
            return "", b""
        return self.cache.get(key)

    def __call__(
        self, url: str, params: dict | None = None, accept: str | None = None
    ) -> str:
        """Return the body of ``url``; raise NotFoundError on 404."""
        if params:
            url += "?" + urlencode(params)
        key = f"{accept}\n{url}"
        kind, cached = self._cached(key)
        if kind == "notfound":
            raise NotFoundError(url)
        if kind:
            return cached.decode("utf-8", errors="replace")
        try:
            body = self._fetch(url, accept)
        except NotFoundError:
            if self.cache:
                self.cache.put(key, "notfound", b"")
            raise
        if self.cache:
            kind = "empty" if looks_empty(body) else "ok"
            self.cache.put(key, kind, body.encode())
        return body

    def _sleep(self, seconds: float, why: str) -> None:
        if seconds <= 0:
            return
        run = self._run()
        on_wait = run.on_wait or self.on_wait
        if on_wait and seconds >= 2:
            on_wait(f"waiting {seconds:.0f}s: {why}")
        if run.cancel is None:
            time.sleep(seconds)
        elif run.cancel.wait(seconds):
            raise OperationCancelledError

    def _throttle(
        self, host: str, default: tuple[int, float] | None = None
    ) -> None:
        """Wait until sending to ``host`` stays within its budget."""
        limit = _RATE_LIMITS.get(host, default)
        if limit is None:
            return
        count, window = limit
        sent = self._sent.setdefault(host, [])
        now = time.monotonic()
        sent[:] = [t for t in sent if now - t < window]
        if len(sent) >= count:
            self._sleep(sent[0] + window - now, f"{host} rate limit")
        sent.append(time.monotonic())

    def page(self, url: str) -> Page:
        """Fetch a reference's URL the way a browser would, without raising.

        Unlike ``__call__``, error statuses are returned rather than raised
        (their bodies show bot challenges), and only public addresses are
        contacted, after every redirect too. Answers (not network errors)
        are cached for a shorter time.
        """
        key = f"page\n{url}"
        kind, cached = self._cached(key)
        if kind == "page":
            meta, _, body = cached.partition(b"\n")
            try:
                return Page(**json.loads(meta), body=body)
            except (ValueError, TypeError):
                pass  # Unreadable: fetch again.
        page = self._page(url)
        if self.cache and page.status is not None:
            meta = {
                "url": page.url,
                "final_url": page.final_url,
                "status": page.status,
                "content_type": page.content_type,
            }
            self.cache.put(
                key, "page", json.dumps(meta).encode() + b"\n" + page.body
            )
        return page

    def _page(self, url: str) -> Page:
        self._check_cancel()
        page = Page(url=url, final_url=url)
        deadline = time.monotonic() + self.link_timeout
        try:
            check_public_url(url, self.any_link_port)
        except _UnsafeURLError as exc:
            page.error, page.error_kind = str(exc), exc.kind
            return page
        host = urlparse(url).netloc
        self._throttle(host, _PAGE_RATE_LIMIT)
        request = Request(
            url,
            headers={
                "User-Agent": self.user_agent,
                "Accept": "text/html,application/xhtml+xml,application/pdf;"
                "q=0.9,*/*;q=0.8",
                "Accept-Language": "en",
            },
        )
        opener = _page_opener(self.any_link_port)
        timeout = min(self.timeout, self.link_timeout)
        try:
            with opener.open(request, timeout=timeout) as r:
                page.status = r.status
                page.final_url = r.geturl()
                page.content_type = r.headers.get_content_type()
                page.body = _read_until(r, deadline)
        except _TooSlowError:
            page.status, page.body = None, b""
            page.error = f"took over {self.link_timeout:g}s"
            page.error_kind = "network"
            log.info("gave up on %r: too slow", url)
        except HTTPError as exc:
            page.status = exc.code
            page.final_url = exc.geturl() or url
            page.content_type = exc.headers.get_content_type()
            try:
                page.body = _read_until(exc, deadline)
            except (OSError, HTTPException, _TooSlowError):
                pass
            exc.close()
        except _UnsafeURLError as exc:
            page.error, page.error_kind = str(exc), exc.kind
        except (OSError, HTTPException, ValueError) as exc:
            reason = getattr(exc, "reason", exc)
            if isinstance(reason, _UnsafeURLError):
                page.error, page.error_kind = str(reason), reason.kind
            else:
                page.error, page.error_kind = str(reason), "network"
        return page

    def _fetch(self, url: str, accept: str | None) -> str:
        headers = {"User-Agent": self.user_agent}
        if accept:
            headers["Accept"] = accept
        host = urlparse(url).netloc
        # Identification isn't part of the cache key, so it's added here.
        if host == "api.semanticscholar.org" and self.s2_key:
            headers["x-api-key"] = self.s2_key
        if host == "api.openalex.org" and self.openalex_mailto:
            url += ("&" if "?" in url else "?") + urlencode(
                {"mailto": self.openalex_mailto}
            )
        delays = iter(_RETRY_DELAYS)
        while True:
            self._check_cancel()
            self._throttle(host)
            try:
                with urlopen(
                    Request(url, headers=headers), timeout=self.timeout
                ) as r:
                    return r.read().decode("utf-8", errors="replace")
            except HTTPError as exc:
                retry_after = exc.headers.get("Retry-After", "")
                exc.close()
                if exc.code == 404:
                    raise NotFoundError(url) from exc
                delay = next(delays, None)
                if delay is None or not (exc.code == 429 or exc.code >= 500):
                    raise SourceError(f"{host}: HTTP {exc.code}") from exc
                if retry_after.isdigit():
                    delay = min(int(retry_after) + 1, _MAX_RETRY_AFTER)
                self._sleep(delay, f"{host} HTTP {exc.code}")
            except (OSError, HTTPException) as exc:
                raise SourceError(f"{host}: {exc}") from exc


# Where search APIs put their results; an empty list there is a negative
# answer, cached for less time.
_RESULT_LISTS = [
    ("message", "items"),  # Crossref
    ("notes",),  # OpenReview
    ("results", "bindings"),  # SPARQL (EU Publications Office)
    ("results",),  # OpenAlex
    ("data",),  # Semantic Scholar
    ("_embedded", "searchResult", "_embedded", "objects"),  # DSpace
]


def looks_empty(body: str) -> bool:
    """Whether ``body`` is a search that found nothing."""
    text = body.lstrip()
    if text.startswith("<"):  # arXiv's Atom feed
        return "<feed" in text[:500] and "<entry" not in text
    try:
        data = json.loads(text)
    except ValueError:
        return False
    if not isinstance(data, dict):
        return False
    if "archived_snapshots" in data:  # Wayback Machine
        return not data["archived_snapshots"]
    for path in _RESULT_LISTS:
        node = data
        for part in path:
            node = node.get(part) if isinstance(node, dict) else None
        if isinstance(node, list):
            return not node
    return False
