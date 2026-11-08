"""Load and validate anondoc's YAML configuration."""

import posixpath
import re
from dataclasses import dataclass, field
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Any, Literal

import soupsieve
import yaml

from .errors import AnonDocError

LinkAction = Literal["keep", "unwrap", "remove", "replace"]

#: Files that only machines or web hosts read: sitemaps, error pages, LLM
#: summaries (llms.txt), and Sphinx-format cross-reference inventories.
DEFAULT_SKIP = (
    "404.html",
    "objects.inv",
    "llms.txt",
    "llms-full.txt",
    "sitemap.xml",
    "sitemap.xml.gz",
)

#: Standard HTML that names the site's real URL or its authors.
DEFAULT_STRIP_SELECTORS = ("link[rel=canonical]", "meta[name=author]")


@dataclass(frozen=True)
class LinkRule:
    """What to do with URLs starting with ``match``.

    Attributes:
        match: URL prefix, compared case-insensitively. It matches whole path
            segments: ``https://x.io/a`` matches ``https://x.io/a/b`` and
            ``https://x.io/a#c``, but not ``https://x.io/ab``.
        action: ``keep``, ``unwrap``, ``remove``, or ``replace``.
        replace: Replacement for the whole URL when ``action`` is ``replace``.

    """

    match: str
    action: LinkAction
    replace: str | None = None

    def matches(self, url: str) -> bool:
        """Whether ``url`` falls under this rule's prefix."""
        return prefix_matches(self.match, url)


@dataclass(frozen=True)
class StripRule:
    """A block to delete: a CSS selector (HTML) or a regex (any text file).

    Attributes:
        label: Name used in reports.
        selector: CSS selector, applied to HTML pages and HTML fragments.
        pattern: Regular expression, applied to all text files.
        files: Globs limiting the files this rule applies to; all if empty.

    """

    label: str
    selector: str | None = None
    pattern: re.Pattern[str] | None = None
    files: tuple[str, ...] = ()

    def applies_to(self, path: str) -> bool:
        """Whether this rule applies to the site-relative ``path``."""
        return not self.files or matches_any(path, self.files)


DEFAULT_STRIP = tuple(StripRule(s, selector=s) for s in DEFAULT_STRIP_SELECTORS)


@dataclass(frozen=True)
class JsonScript:
    """A JavaScript copy of a JSON file, for themes that load it from disk.

    Attributes:
        source: Site-relative path of the JSON file.
        path: Site-relative path of the script to write.
        template: Script text, with ``{json}`` standing for the JSON.

    """

    source: str
    path: str
    template: str


@dataclass(frozen=True)
class Config:
    """A validated anondoc configuration.

    Attributes:
        terms: Identifying terms mapped to their replacements.
        placeholder: Default replacement for terms without one.
        site_url: The site's own URL; inferred from the site if ``None``.
        links: Link rules, first match wins.
        skip: Globs of files to leave out of the output. Setting it in the
            config replaces :data:`DEFAULT_SKIP`.
        strip: Blocks to delete. Setting it in the config replaces
            :data:`DEFAULT_STRIP`.
        file_urls: Make the site work when opened from disk by pointing
            directory links at their ``index.html``.
        json_links: JSON keys whose string values are root-relative links
            (such as a search index's result locations), by file glob. They
            get the same ``file_urls`` treatment as HTML links.
        json_scripts: JavaScript copies of JSON files to write.

    """

    terms: dict[str, str]
    placeholder: str = "anonymous"
    site_url: str | None = None
    links: tuple[LinkRule, ...] = ()
    skip: tuple[str, ...] = DEFAULT_SKIP
    strip: tuple[StripRule, ...] = DEFAULT_STRIP
    file_urls: bool = True
    json_links: dict[str, tuple[str, ...]] = field(default_factory=dict)
    json_scripts: tuple[JsonScript, ...] = ()


def prefix_matches(prefix: str, url: str) -> bool:
    """Whether ``url`` starts with ``prefix`` at a path-segment boundary."""
    prefix, url = prefix.casefold(), url.casefold()
    if not url.startswith(prefix):
        return False
    rest = url[len(prefix) :]
    return not rest or prefix.endswith(("/", "#", "?")) or rest[0] in "/?#"


def matches_any(path: str, globs: tuple[str, ...]) -> bool:
    """Match a site-relative path against globs, gitignore style.

    Globs without a ``/`` match the file name in any directory; globs with one
    match the whole path.
    """
    name = posixpath.basename(path)
    return any(
        fnmatchcase(path if "/" in g else name, g.strip("/")) for g in globs
    )


def normalize_site_url(url: str) -> str:
    """Strip the site URL to its prefix, ending in exactly one slash."""
    url = re.split(r"[?#]", url.strip(), maxsplit=1)[0]
    if url.endswith("/index.html"):
        url = url[: -len("index.html")]
    return url.rstrip("/") + "/"


def _str(value: Any, where: str) -> str:
    if not isinstance(value, (str, int, float)) or isinstance(value, bool):
        raise AnonDocError(f"{where} must be a string.")
    return str(value)


def _strings(value: Any, where: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        raise AnonDocError(f"{where} must be a list of strings.")
    return tuple(_str(v, f"{where}[{i}]") for i, v in enumerate(value))


def _keys(data: dict, allowed: set[str], where: str) -> None:
    unknown = sorted(set(data) - allowed)
    if unknown:
        raise AnonDocError(
            f"{where}: unknown key(s) {', '.join(map(str, unknown))}; "
            f"expected {', '.join(sorted(allowed))}."
        )


def _terms(value: Any, placeholder: str) -> dict[str, str]:
    if isinstance(value, list):
        items = []
        for i, v in enumerate(value):
            if isinstance(v, dict):
                items.extend(v.items())
            else:
                items.append((_str(v, f"terms[{i}]"), None))
    elif isinstance(value, dict):
        items = list(value.items())
    else:
        raise AnonDocError("terms must be a mapping of term: replacement.")
    terms: dict[str, str] = {}
    seen: dict[str, str] = {}
    for term, replacement in items:
        term = _str(term, "term")
        if not term.strip():
            raise AnonDocError("terms cannot be empty strings.")
        if term.lower() in seen:
            raise AnonDocError(
                f"terms {seen[term.lower()]!r} and {term!r} are the same "
                "(terms are case-insensitive)."
            )
        seen[term.lower()] = term
        terms[term] = (
            placeholder
            if replacement is None
            else _str(replacement, f"terms[{term!r}]")
        )
    if not terms:
        raise AnonDocError("terms is empty; list at least one term.")
    return terms


def _link(data: Any, i: int) -> LinkRule:
    where = f"links[{i}]"
    if not isinstance(data, dict):
        raise AnonDocError(f"{where} must be a mapping with a 'match' key.")
    _keys(data, {"match", "action", "replace"}, where)
    if "match" not in data:
        raise AnonDocError(f"{where} needs a 'match' URL prefix.")
    match = _str(data["match"], f"{where}.match")
    if "replace" in data:
        if data.get("action", "replace") != "replace":
            raise AnonDocError(f"{where}: 'replace' conflicts with 'action'.")
        return LinkRule(match, "replace", _str(data["replace"], where))
    action = data.get("action")
    if action not in ("keep", "unwrap", "remove"):
        raise AnonDocError(
            f"{where}.action must be keep, unwrap, or remove "
            "(or give 'replace: URL')."
        )
    return LinkRule(match, action)


def _strip(data: Any, i: int) -> StripRule:
    where = f"strip[{i}]"
    if not isinstance(data, dict):
        raise AnonDocError(
            f"{where} must be a mapping with 'selector' or 'pattern'."
        )
    _keys(data, {"name", "selector", "pattern", "files"}, where)
    files = _strings(data.get("files"), f"{where}.files")
    if ("selector" in data) == ("pattern" in data):
        raise AnonDocError(f"{where} needs exactly one of selector or pattern.")
    if "selector" in data:
        source = _str(data["selector"], f"{where}.selector")
        try:
            soupsieve.compile(source)
        except soupsieve.SelectorSyntaxError as exc:
            raise AnonDocError(f"{where}: bad selector: {exc}") from None
        pattern = None
    else:
        source = _str(data["pattern"], f"{where}.pattern")
        try:
            pattern = re.compile(source)
        except re.error as exc:
            raise AnonDocError(f"{where}: bad pattern: {exc}") from None
    name = _str(data.get("name", source), f"{where}.name")
    return StripRule(
        name,
        selector=None if pattern else source,
        pattern=pattern,
        files=files,
    )


def _json_links(data: Any) -> dict[str, tuple[str, ...]]:
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise AnonDocError("json_links must map file globs to lists of keys.")
    return {
        _str(glob, "json_links glob"): _strings(keys, f"json_links[{glob!r}]")
        for glob, keys in data.items()
    }


def _json_script(data: Any, i: int) -> JsonScript:
    where = f"json_scripts[{i}]"
    if not isinstance(data, dict):
        raise AnonDocError(f"{where} must be a mapping.")
    _keys(data, {"from", "to", "template"}, where)
    missing = {"from", "to", "template"} - set(data)
    if missing:
        raise AnonDocError(f"{where} needs {', '.join(sorted(missing))}.")
    template = _str(data["template"], f"{where}.template")
    if "{json}" not in template:
        raise AnonDocError(f"{where}.template must contain {{json}}.")
    return JsonScript(
        _str(data["from"], f"{where}.from").strip("/"),
        _str(data["to"], f"{where}.to").strip("/"),
        template,
    )


def _list(data: Any, key: str) -> list:
    value = data.get(key) or []
    if not isinstance(value, list):
        raise AnonDocError(f"{key} must be a list.")
    return value


def parse_config(data: Any) -> Config:
    """Validate parsed YAML and build a :class:`Config`."""
    if not isinstance(data, dict):
        raise AnonDocError("The config must be a YAML mapping.")
    _keys(
        data,
        {
            "terms",
            "placeholder",
            "site_url",
            "links",
            "skip",
            "strip",
            "file_urls",
            "json_links",
            "json_scripts",
        },
        "config",
    )
    placeholder = _str(data.get("placeholder", "anonymous"), "placeholder")
    if "terms" not in data:
        raise AnonDocError("The config needs a 'terms' mapping.")
    terms = _terms(data["terms"], placeholder)
    site_url = data.get("site_url")
    if site_url is not None:
        site_url = normalize_site_url(_str(site_url, "site_url"))
    skip = _strings(data["skip"], "skip") if "skip" in data else DEFAULT_SKIP
    strip = (
        tuple(_strip(r, i) for i, r in enumerate(_list(data, "strip")))
        if "strip" in data
        else DEFAULT_STRIP
    )
    file_urls = data.get("file_urls", True)
    if not isinstance(file_urls, bool):
        raise AnonDocError("file_urls must be true or false.")
    return Config(
        terms=terms,
        placeholder=placeholder,
        site_url=site_url,
        links=tuple(_link(r, i) for i, r in enumerate(_list(data, "links"))),
        skip=skip,
        strip=strip,
        file_urls=file_urls,
        json_links=_json_links(data.get("json_links")),
        json_scripts=tuple(
            _json_script(r, i)
            for i, r in enumerate(_list(data, "json_scripts"))
        ),
    )


def load_config(path: Path) -> Config:
    """Read and validate a YAML config file."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise AnonDocError(
            f"Cannot read config {path}: {exc.strerror or exc}"
        ) from None
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise AnonDocError(f"Invalid YAML in {path}: {exc}") from None
    try:
        return parse_config(data)
    except AnonDocError as exc:
        raise AnonDocError(f"{path}: {exc}") from None
