"""Regression tests for the security review's findings."""

import http.server
import socket
import threading
import time

import pytest

from papertools.bibcheck.bibtex import parse_bib_lenient
from papertools.bibcheck.config import Config, SourcesConfig
from papertools.bibcheck.sources import Http
from papertools.bibcheck.sources.http import is_public_address

# The address tests' server runs on a random high port.
ANY_PORT = Config(sources=SourcesConfig(any_link_port=True))

# --- Server-side request forgery ----------------------------------------------


@pytest.fixture
def internal_server():
    """A page only reachable from the server itself."""

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 (http.server's name)
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<title>INTERNAL secret-token</title>")

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server.server_port
    server.shutdown()


def test_dns_rebinding_cannot_reach_internal_pages(
    internal_server, monkeypatch
):
    """DNS that says "public", then "127.0.0.1", is refused on connect."""
    real = socket.getaddrinfo
    answers = []

    def rebinding(host, port, *args, **kwargs):
        if host != "rebind.example":
            return real(host, port, *args, **kwargs)
        ip = "93.184.215.14" if not answers else "127.0.0.1"
        answers.append(ip)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port or 80))]

    monkeypatch.setattr(socket, "getaddrinfo", rebinding)
    page = Http(config=ANY_PORT).page(
        f"http://rebind.example:{internal_server}/"
    )
    assert page.status is None
    assert page.error_kind == "private"
    assert b"secret" not in page.body
    assert answers == ["93.184.215.14", "127.0.0.1"]


def test_private_addresses_are_refused_directly(internal_server):
    """Plain loopback URLs never get a response."""
    page = Http(config=ANY_PORT).page(f"http://127.0.0.1:{internal_server}/")
    assert (page.status, page.error_kind) == (None, "private")


@pytest.mark.parametrize(
    ("address", "public"),
    [
        ("93.184.215.14", True),
        ("2606:4700::1111", True),
        ("127.0.0.1", False),
        ("169.254.169.254", False),
        ("10.1.2.3", False),
        ("100.64.0.1", False),
        ("::1", False),
        ("fd00::1", False),
        ("::ffff:127.0.0.1", False),
        ("64:ff9b::a9fe:a9fe", False),  # NAT64 for 169.254.169.254
        ("64:ff9b::5db8:d70e", True),  # NAT64 for 93.184.215.14
    ],
)
def test_public_addresses(address, public):
    """Embedded IPv4 addresses (NAT64) are judged as what they reach."""
    assert is_public_address(address) is public


# --- Denial of service -------------------------------------------------------


def test_broken_files_parse_in_linear_time():
    """Unclosed braces don't make each later entry rescan the file."""
    text = "".join(f"@misc{{k{i}, title={{x\n" for i in range(50_000))
    started = time.perf_counter()
    entries, errors = parse_bib_lenient(text)
    assert time.perf_counter() - started < 5  # Was ~30 minutes.
    assert len(errors) == 50_000
    assert errors[0].message == (
        "this '{' is not closed before the next entry (line 2)"
    )
    assert errors[-1].line == 50_000


def test_report_caps_syntax_errors():
    """The JSON report lists 100 syntax errors and counts the rest."""
    from papertools.bibcheck.report import report_json

    errors = [{"line": i} for i in range(250)]
    report = report_json("f", [], sentence_case=False, syntax_errors=errors)
    assert len(report["syntax_errors"]) == 100
    assert report["syntax_errors_omitted"] == 150
    assert report["summary"]["syntax_errors"] == 250


# --- Browser protections -----------------------------------------------------


def test_pdfs_are_parsed_out_of_process(monkeypatch):
    """MuPDF runs in a child process; a crash there is just 'unreadable'."""
    import subprocess

    from papertools.bibcheck import links

    calls = []
    real = subprocess.run

    def spy(args, **kwargs):
        calls.append(args)
        return real(args, **kwargs)

    monkeypatch.setattr(links.subprocess, "run", spy)
    assert links._pdf_text(b"%PDF-1.7 garbage") == ([], "")
    assert calls and calls[0][-1] == "papertools.bibcheck._pdftext"


# --- Remaining risks, fixed --------------------------------------------------


def test_only_web_ports_by_default(monkeypatch):
    """Links on other ports aren't fetched (no probing of services)."""
    looked_up = []
    monkeypatch.setattr(
        socket, "getaddrinfo", lambda *a, **k: looked_up.append(a) or []
    )
    page = Http(config=Config()).page("http://example.com:6379/")
    assert (page.status, page.error_kind) == (None, "port")
    assert "port 6379" in page.error
    assert looked_up == []  # Refused before any DNS lookup.


def test_slow_dns_is_given_up_on(monkeypatch):
    """A lookup that hangs costs DNS_TIMEOUT, not the resolver's timeout."""
    from papertools.bibcheck.sources import http as http_module

    monkeypatch.setattr(http_module, "DNS_TIMEOUT", 0.2)
    monkeypatch.setattr(
        socket, "getaddrinfo", lambda *a, **k: time.sleep(3) or []
    )
    started = time.monotonic()
    page = Http(config=Config()).page("http://slow-dns.example/")
    assert time.monotonic() - started < 2
    assert "took over" in page.error
    assert page.error_kind == "network"


def test_slow_page_is_given_up_on(monkeypatch):
    """A server dripping a byte at a time can't hold a turn."""
    from papertools.bibcheck.sources import http as http_module

    class Drip(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            for _ in range(100):
                self.wfile.write(b"x")
                self.wfile.flush()
                time.sleep(0.1)

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Drip)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setattr(http_module, "is_public_address", lambda a: True)
    config = Config(sources=SourcesConfig(any_link_port=True, link_timeout=1))
    started = time.monotonic()
    page = Http(config=config).page(f"http://127.0.0.1:{server.server_port}/")
    server.shutdown()
    assert time.monotonic() - started < 4
    assert page.error == "took over 1s"
