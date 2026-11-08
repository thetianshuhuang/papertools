"""Format-independent decisions: terms, link rules, and self-links."""

import posixpath
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from html.entities import html5
from typing import Literal

from .config import Config, matches_any, prefix_matches

#: Absolute URLs, ending before delimiters and trailing punctuation.
URL = r"https?://[^\s\"'<>()\[\]{}\\`]*[^\s\"'<>()\[\]{}\\`.,;:!?*]"
URL_RE = re.compile(URL, re.IGNORECASE)

_ENTITIES: defaultdict[str, list[str]] = defaultdict(list)
for _name, _char in html5.items():
    if _name.endswith(";"):
        _ENTITIES[_char].append(re.escape(f"&{_name}"))


def term_pattern(term: str) -> str:
    r"""A regex matching ``term`` as HTML, Markdown, or plain text writes it.

    Whitespace matches any run of whitespace or non-breaking spaces, and each
    punctuation character also matches its Markdown backslash escape and its
    HTML character references (``Tartan\*``, ``A&amp;M``).
    """
    parts = []
    for char in term:
        if char.isspace():
            parts.append(r"(?:\s|&nbsp;|&#160;|&#xa0;)+")
        elif char.isalnum():
            parts.append(re.escape(char))
        else:
            forms = [
                re.escape(char),
                r"\\" + re.escape(char),
                f"&#0*{ord(char)};",
                f"&#x0*{ord(char):x};",
                *_ENTITIES.get(char, ()),
            ]
            parts.append(f"(?:{'|'.join(forms)})")
    return "".join(parts)


Action = Literal[
    "keep", "replace", "unwrap", "remove", "self", "default", "plain"
]


@dataclass(frozen=True)
class Decision:
    """What to do with one URL.

    Attributes:
        action: ``keep``, ``replace``, ``unwrap``, or ``remove`` from a link
            rule; ``self`` for the site's own URLs; ``default`` for other URLs
            containing a term; ``plain`` for everything else.
        target: The replacement URL, or the site-relative path for ``self``.

    """

    action: Action
    target: str | None = None


@dataclass
class Stats:
    """Counts reported after a run."""

    terms: Counter[str] = field(default_factory=Counter)
    links: Counter[str] = field(default_factory=Counter)
    stripped: Counter[str] = field(default_factory=Counter)
    unruled: set[str] = field(default_factory=set)


class Rewriter:
    """Apply a :class:`Config` to text, independent of the file format.

    Args:
        config: The validated configuration.
        site_url: The site's own URL prefix, ending in ``/``, if known.

    """

    def __init__(self, config: Config, site_url: str | None = None):
        self.config = config
        self.site_url = site_url
        self.stats = Stats()
        #: Output paths of every written file, for :meth:`file_url`.
        self.files: set[str] = set()
        terms = sorted(config.terms, key=len, reverse=True)
        self._terms = {f"t{i}": t for i, t in enumerate(terms)}
        alternatives = "|".join(
            f"(?P<{name}>{term_pattern(t)})" for name, t in self._terms.items()
        )
        self.term_re = re.compile(alternatives, re.IGNORECASE)
        self._text_re = re.compile(
            f"(?P<url>{URL})|{alternatives}", re.IGNORECASE
        )

    # Terms ----------------------------------------------------------------

    def _term(self, match: re.Match[str]) -> str:
        term = self.term_of(match)
        self.stats.terms[term] += 1
        return self.config.terms[term]

    def term_of(self, match: re.Match[str]) -> str:
        """The configured term that a :attr:`term_re` match is for."""
        return self._terms[match.lastgroup or ""]

    def replace_terms(self, text: str) -> str:
        """Replace every term in ``text``, ignoring URL structure."""
        return self.term_re.sub(self._term, text)

    def rename(self, path: str) -> str:
        """The output path for a site-relative input path."""
        return self.term_re.sub(
            lambda m: self.config.terms[self.term_of(m)], path
        )

    # Links ----------------------------------------------------------------

    def decide(self, url: str) -> Decision:
        """Classify an absolute URL."""
        for rule in self.config.links:
            if rule.matches(url):
                return Decision(rule.action, rule.replace)
        if self.site_url and prefix_matches(self.site_url.rstrip("/"), url):
            return Decision("self", url[len(self.site_url) :])
        if self.term_re.search(url):
            return Decision("default")
        return Decision("plain")

    def is_skipped(self, path: str) -> bool:
        """Whether a site-relative path is left out of the output."""
        return matches_any(path, self.config.skip)

    def link_action(self, href: str, rel_dir: str) -> str | None:
        """How a link with link text should change: unwrap, remove, or None.

        ``None`` means the URL itself is left to :meth:`text_pass`. Links to
        skipped files are unwrapped, since their target will not exist.
        """
        href = href.strip()
        if URL_RE.fullmatch(href):
            decision = self.decide(href)
            if decision.action in ("unwrap", "remove"):
                return decision.action
            if decision.action == "default":
                self.stats.unruled.add(href)
                return "unwrap"
            if decision.action != "self":
                return None
            target = decision.target or ""
        elif re.match(r"[a-z][a-z0-9+.-]*:|//|#", href, re.IGNORECASE):
            return None
        else:
            target = posixpath.normpath(posixpath.join(rel_dir, href))
        target = re.split(r"[?#]", target, maxsplit=1)[0]
        if target and self.is_skipped(target):
            return "unwrap"
        return None

    def file_url(self, href: str, rel_dir: str, *, root: bool = False) -> str:
        """Point a relative link to a directory at its ``index.html``.

        Browsers show a directory listing, not ``index.html``, for directory
        links in a site opened from disk.

        Args:
            href: A link, in output (renamed) terms.
            rel_dir: Output directory of the file containing the link.
            root: Treat an empty path (``""``, ``#x``) as the directory
                ``rel_dir`` itself, as the search index's locations do.

        """
        if re.match(r"[a-z][a-z0-9+.-]*:|/", href, re.IGNORECASE):
            return href
        path, *rest = re.split(r"(?=[?#])", href, maxsplit=1)
        if not path and not root:
            return href
        target = posixpath.normpath(posixpath.join(rel_dir, path))
        index = "index.html" if target == "." else f"{target}/index.html"
        if target in self.files or index not in self.files:
            return href
        prefix = path if not path or path.endswith("/") else path + "/"
        self.stats.links["to index.html"] += 1
        return prefix + "index.html" + "".join(rest)

    def relativize(self, path: str, rel_dir: str) -> str:
        """Express a site-relative ``path`` relative to ``rel_dir``."""
        path, *rest = re.split(r"(?=[?#])", path, maxsplit=1)
        rel = posixpath.relpath(path or ".", rel_dir or ".")
        if not path or path.endswith("/"):
            rel = "./" if rel == "." else rel + "/"
        return rel + "".join(rest)

    def _url(self, url: str, rel_dir: str) -> str:
        decision = self.decide(url)
        match decision.action:
            case "keep" | "plain":
                if decision.action == "keep":
                    self.stats.links["kept"] += 1
                return url
            case "replace":
                self.stats.links["replaced"] += 1
                return decision.target or ""
            case "self":
                self.stats.links["relativized"] += 1
                path = self.relativize(decision.target or "", rel_dir)
                return self.replace_terms(path)
            case "unwrap" | "remove":
                self.stats.links["removed"] += 1
                return ""
            case _:
                return self.replace_terms(url)

    def text_pass(self, text: str, rel_dir: str) -> str:
        """Rewrite bare URLs and replace terms everywhere else.

        Args:
            text: Text of any format.
            rel_dir: Site-relative directory of the file, for self-links.

        """

        def sub(match: re.Match[str]) -> str:
            if match.lastgroup == "url":
                return self._url(match.group(), rel_dir)
            return self._term(match)

        return self._text_re.sub(sub, text)

    # Leaks ----------------------------------------------------------------

    def shielded(self, url: str) -> bool:
        """Whether a URL may legitimately contain terms (kept or replaced)."""
        return any(
            (r.action == "keep" and r.matches(url))
            or (r.replace is not None and prefix_matches(r.replace, url))
            for r in self.config.links
        )

    def find_terms(self, text: str) -> list[re.Match[str]]:
        """Term occurrences in ``text`` outside shielded URLs."""
        shields = [
            m.span() for m in URL_RE.finditer(text) if self.shielded(m.group())
        ]
        return [
            m
            for m in self.term_re.finditer(text)
            if not any(a <= m.start() < b for a, b in shields)
        ]
