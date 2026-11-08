"""HTTP client tests: caching, rate-limit budgets, and Retry-After."""

import io
from email.message import Message
from urllib.error import HTTPError

import pytest

from papertools.bibcheck import sources
from papertools.bibcheck.sources import Http, NotFoundError, SourceError


class Clock:
    """Fake monotonic clock that advances only when slept on."""

    def __init__(self):
        self.now = 1000.0
        self.slept: list[float] = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.now += seconds


@pytest.fixture
def clock(monkeypatch):
    """Replace time in sources with a fake clock."""
    c = Clock()
    monkeypatch.setattr(sources.time, "monotonic", c.monotonic)
    monkeypatch.setattr(sources.time, "sleep", c.sleep)
    return c


def respond(monkeypatch, replies):
    """Make urlopen return or raise ``replies`` in order; record URLs."""
    urls = []

    def fake_urlopen(request, timeout):
        urls.append(request.full_url)
        reply = replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return io.BytesIO(reply.encode())

    monkeypatch.setattr(sources, "urlopen", fake_urlopen)
    return urls


def http_error(code, retry_after=None):
    """An HTTPError with an optional Retry-After header."""
    headers = Message()
    if retry_after is not None:
        headers["Retry-After"] = str(retry_after)
    return HTTPError("https://x", code, "err", headers, io.BytesIO())


def test_rate_limit_window(monkeypatch, clock):
    """The sixth v1 OpenReview search in a minute waits for the window."""
    respond(monkeypatch, ["{}"] * 6)
    waits = []
    http = Http(on_wait=waits.append)
    for i in range(6):
        http(f"https://api.openreview.net/notes/search?term={i}")
    assert clock.slept == [61.0]
    assert waits == ["waiting 61s: api.openreview.net rate limit"]


def test_retry_after_is_honored(monkeypatch, clock):
    """429s wait as long as the server says, then succeed."""
    respond(monkeypatch, [http_error(429, retry_after=38), "ok"])
    assert Http()("https://api2.openreview.net/notes/search") == "ok"
    assert 39 in clock.slept


def test_errors_and_cache(monkeypatch, clock, tmp_path):
    """404s are cached as misses; other errors are raised and not cached."""
    urls = respond(
        monkeypatch, [http_error(404), http_error(403), "body", "unused"]
    )
    http = Http(cache_dir=tmp_path)
    for _ in range(2):
        with pytest.raises(NotFoundError):
            http("https://doi.org/10.1/missing")
    with pytest.raises(SourceError, match="HTTP 403"):
        http("https://doi.org/10.1/forbidden")
    assert http("https://doi.org/10.1/forbidden") == "body"
    assert http("https://doi.org/10.1/forbidden") == "body"
    assert len(urls) == 3
