"""The config file, the disk cache, and Http's use of them."""

import os
import time

import pytest

from papertools.bibcheck.cache import DiskCache
from papertools.bibcheck.config import (
    DAY,
    CacheConfig,
    Config,
    load_config,
)
from papertools.bibcheck.errors import BibCheckError
from papertools.bibcheck.sources import (
    Http,
    NotFoundError,
    Page,
    looks_empty,
)

# --- Config ------------------------------------------------------------------


def test_defaults_without_a_file(tmp_path):
    """No file means the documented defaults."""
    config = load_config()
    assert config.cache.found_days == 14
    assert config.cache.not_found_days == 7
    assert config.cache.pages_days == 3
    assert config.check.max_authors == 10
    with pytest.raises(BibCheckError, match="no such config file"):
        load_config(tmp_path / "missing.toml")


def test_file_sets_values(tmp_path, monkeypatch):
    """Every table is read; the environment wins for contact settings."""
    path = tmp_path / "bibcheck.toml"
    path.write_text(
        '[cache]\ndir = "~/c"\nfound_days = 30\nmax_size_mb = 0.5\n'
        '[sources]\ncontact_email = "a@b.c"\ntimeout = 5\n'
        "[check]\nmax_authors = 6\n"
    )
    config = load_config(path)
    assert config.cache.folder() == path.home() / "c"
    assert config.cache.ttl["ok"] == 30 * DAY
    assert config.cache.ttl["empty"] == 7 * DAY
    assert config.cache.max_bytes == 512 * 1024
    assert config.sources.crossref_mailto == "a@b.c"
    assert config.sources.openalex_mailto == "a@b.c"
    assert config.check.max_authors == 6
    monkeypatch.setenv("CROSSREF_MAILTO", "env@b.c")
    assert config.sources.crossref_mailto == "env@b.c"


def test_config_env_variable(tmp_path, monkeypatch):
    """$BIBCHECK_CONFIG names the file."""
    path = tmp_path / "x.toml"
    path.write_text("[cache]\nfound_days = 1\n")
    monkeypatch.setenv("BIBCHECK_CONFIG", str(path))
    assert load_config().cache.found_days == 1


@pytest.mark.parametrize(
    ("text", "error"),
    [
        ("[cahce]\n", "unknown table [cahce]"),
        ("[web]\nport = 1\n", "unknown table [web]"),  # bibcheck-web's.
        ("[cache]\nfound_day = 3\n", "cache.found_day is not a setting"),
        ('[cache]\nfound_days = "3"\n', "cache.found_days must be a number"),
        ("[cache]\nfound_days = true\n", "must be a number"),
        ("[cache]\nfound_days = -1\n", "must be 0 or more"),
        ("[check]\nmax_authors = 2.5\n", "check.max_authors must be a whole"),
        ('[sources]\nany_link_port = "yes"\n', "must be true or false"),
        ("[cache]\ndir = 3\n", "cache.dir must be a string"),
        ("cache = 3\n", "[cache] must be a table"),
        ("[cache\n", "not valid TOML"),
    ],
)
def test_config_mistakes_are_reported(tmp_path, text, error):
    """Typos and wrong types are errors naming the file and setting."""
    path = tmp_path / "bad.toml"
    path.write_text(text)
    with pytest.raises(BibCheckError) as exc:
        load_config(path)
    assert error in str(exc.value)
    assert str(path) in str(exc.value)


def test_cli_reports_bad_config(tmp_path, capsys):
    """The CLI exits 2 with the config error."""
    from papertools.bibcheck.cli import main

    bad = tmp_path / "bad.toml"
    bad.write_text("[cache]\nnope = 1\n")
    bib = tmp_path / "a.bib"
    bib.write_text("@misc{a, title={A}}")
    assert main([str(bib), "--config", str(bad)]) == 2
    assert "cache.nope is not a setting" in capsys.readouterr().err


def test_example_config_is_valid():
    """The example file at the repository root loads, with the defaults."""
    from pathlib import Path

    example = Path(__file__).parents[2] / "bibcheck.example.toml"
    assert load_config(example) == Config()


# --- DiskCache ---------------------------------------------------------------


def _cache(tmp_path, **kw) -> DiskCache:
    return DiskCache.from_config(CacheConfig(**kw), tmp_path / "c")


def test_kinds_have_their_own_lifetimes(tmp_path):
    """Found answers outlive negative ones, which outlive pages."""
    cache = _cache(tmp_path)
    for kind in ("ok", "empty", "notfound", "page"):
        cache.put(kind, kind, b"body")
    now = time.time()
    assert cache.get("ok", now + 13 * DAY) == ("ok", b"body")
    assert cache.get("ok", now + 15 * DAY) == ("", b"")
    assert cache.get("empty", now + 6 * DAY) == ("empty", b"body")
    assert cache.get("empty", now + 8 * DAY) == ("", b"")
    assert cache.get("notfound", now + 8 * DAY) == ("", b"")
    assert cache.get("page", now + 2 * DAY) == ("page", b"body")
    assert cache.get("page", now + 4 * DAY) == ("", b"")


def test_writes_are_atomic_and_tidy(tmp_path):
    """No temporary files are left; overwrites replace whole files."""
    cache = _cache(tmp_path)
    cache.put("k", "ok", b"one")
    cache.put("k", "ok", b"two")
    assert cache.get("k") == ("ok", b"two")
    assert not list(cache.root.glob("??/*.tmp"))


def test_legacy_files_are_still_read(tmp_path):
    """Caches written before the header existed keep working."""
    cache = _cache(tmp_path)
    # Older versions wrote "\0404" for a 404: " 4", as "\040" is a space.
    for key, body in (("found", b'{"x": 1}'), ("gone", b" 4")):
        path = cache.path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
    assert cache.get("found") == ("ok", b'{"x": 1}')
    assert cache.get("gone") == ("notfound", b"")


def test_prune_drops_expired_then_oldest(tmp_path):
    """Expired files go first; then the oldest, down to the size cap."""
    cache = _cache(tmp_path, max_size_mb=150 / 1024**2)
    now = time.time()
    for i, kind in enumerate(["empty", "ok", "ok", "ok"]):
        cache.put(f"k{i}", kind, b"x" * 50)
        os.utime(cache.path(f"k{i}"), (now - 10 * DAY + i, now - 10 * DAY + i))
    stray = cache.path("k0").parent / "left.tmp"
    stray.write_bytes(b"")
    os.utime(stray, (now - 7200, now - 7200))
    removed, kept = cache.prune(now)
    # k0 (empty, 10 days) expired; k1 is the oldest of three 70-byte files.
    assert removed == 3
    assert cache.get("k0", now) == ("", b"")
    assert cache.get("k1", now) == ("", b"")
    assert cache.get("k3", now)[0] == "ok"
    assert kept <= 150
    assert not stray.exists()


def test_maybe_prune_runs_once_per_interval(tmp_path):
    """The stamp file limits pruning to once per prune_every_hours."""
    cache = _cache(tmp_path, prune_every_hours=1)
    assert cache.maybe_prune() is True
    assert cache.maybe_prune() is False


# --- Http --------------------------------------------------------------------


def _http(tmp_path, monkeypatch, bodies: dict, **kw) -> tuple[Http, list]:
    calls = []

    def fetch(self, url, accept):
        calls.append(url)
        body = bodies[url]
        if body is None:
            raise NotFoundError(url)
        return body

    monkeypatch.setattr(Http, "_fetch", fetch)
    http = Http(cache_dir=tmp_path / "c", config=Config(), **kw)
    return http, calls


def test_http_caches_and_refresh_bypasses(tmp_path, monkeypatch):
    """Answers are reused; refresh refetches and updates the cache."""
    http, calls = _http(tmp_path, monkeypatch, {"https://a/": '{"x": 1}'})
    assert http("https://a/") == '{"x": 1}'
    assert http("https://a/") == '{"x": 1}'
    assert len(calls) == 1
    http.refresh = True
    http("https://a/")
    assert len(calls) == 2
    http.refresh = False
    http("https://a/")
    assert len(calls) == 2


def test_http_marks_negative_answers(tmp_path, monkeypatch):
    """404s and empty searches are stored as negative kinds."""
    http, calls = _http(
        tmp_path,
        monkeypatch,
        {"https://gone/": None, "https://s/": '{"message": {"items": []}}'},
    )
    for _ in range(2):
        with pytest.raises(NotFoundError):
            http("https://gone/")
        http("https://s/")
    assert len(calls) == 2
    assert http.cache.get("None\nhttps://gone/")[0] == "notfound"
    assert http.cache.get("None\nhttps://s/")[0] == "empty"


def test_http_caches_pages(tmp_path, monkeypatch):
    """Pages that answered are cached; network errors aren't."""
    fetched = []

    def page(self, url):
        fetched.append(url)
        if "down" in url:
            return Page(url, url, error="timed out", error_kind="network")
        return Page(url, url + "#x", 200, "text/html", b"<title>T</title>")

    monkeypatch.setattr(Http, "_page", page)
    http = Http(cache_dir=tmp_path / "c", config=Config())
    first = http.page("https://p/")
    again = http.page("https://p/")
    assert again == first
    assert again.final_url == "https://p/#x"
    http.page("https://down/")
    http.page("https://down/")
    assert fetched == ["https://p/", "https://down/", "https://down/"]


def test_identification_is_sent_but_not_cached(tmp_path, monkeypatch):
    """The contact email and API key come from the config."""
    config = load_config()
    config.sources.contact_email = "me@uni.edu"
    config.sources.semantic_scholar_api_key = "KEY"
    http = Http(config=config)
    assert http.user_agent.endswith("(mailto:me@uni.edu)")
    assert http.openalex_mailto == "me@uni.edu"
    assert http.s2_key == "KEY"


@pytest.mark.parametrize(
    ("body", "empty"),
    [
        ('{"message": {"items": []}}', True),
        ('{"message": {"items": [{"DOI": "x"}]}}', False),
        ('{"notes": []}', True),
        ('{"results": []}', True),
        ('{"results": {"bindings": []}}', True),
        ('{"results": {"bindings": [{"t": 1}]}}', False),
        ('{"archived_snapshots": {}}', True),
        (
            '{"_embedded": {"searchResult": {"_embedded": {"objects": []}}}}',
            True,
        ),
        ('<feed xmlns="http://www.w3.org/2005/Atom"></feed>', True),
        ("<feed><entry>x</entry></feed>", False),
        ('{"DOI": "10.1/x", "title": "T"}', False),
        ("@article{x, title={T}}", False),
    ],
)
def test_looks_empty(body, empty):
    """Empty search results are recognized per source."""
    assert looks_empty(body) is empty


def test_odd_cached_json_is_a_source_error(tmp_path, monkeypatch):
    """Non-object JSON fails one source, not the whole run."""
    from papertools.bibcheck.sources import SourceError, Sources

    monkeypatch.setattr(Http, "_fetch", lambda self, url, accept: "4")
    sources = Sources(Http(config=Config()))
    with pytest.raises(SourceError, match="unexpected JSON"):
        sources.s2_match("Any Title")
