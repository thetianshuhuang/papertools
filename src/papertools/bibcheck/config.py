"""bibcheck's settings, from one TOML file.

The file is ``$BIBCHECK_CONFIG`` if set, else
``$XDG_CONFIG_HOME/papertools/bibcheck.toml`` (``~/.config`` by default);
``--config`` overrides both. Every table and key is optional, and a missing
file means the defaults. ``bibcheck.example.toml`` at the repository root
lists every setting.

Environment variables still win over the file for the settings they name:
``CROSSREF_MAILTO``, ``OPENALEX_MAILTO``, and ``SEMANTIC_SCHOLAR_API_KEY``.
"""

import os
import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import TypeVar

from .errors import BibCheckError

DAY = 24 * 3600


@dataclass
class CacheConfig:
    """``[cache]``: the lookup cache (see :mod:`.cache`).

    Attributes:
        dir: The cache folder; "" for ``$XDG_CACHE_HOME/papertools/bibcheck``
            (``~/.cache`` by default). ``~`` is expanded.
        found_days: How long to keep answers that found something.
        not_found_days: How long to keep searches that found nothing, and
            404s: these are what change (papers get published).
        pages_days: How long to keep web pages fetched to check links.
        max_size_mb: Size cap; the oldest files are deleted first.
        prune_every_hours: How often expired files are deleted.

    """

    dir: str = ""
    found_days: float = 14
    not_found_days: float = 7
    pages_days: float = 3
    max_size_mb: float = 1024
    prune_every_hours: float = 24

    @property
    def ttl(self) -> dict[str, float]:
        """Seconds to keep each kind of cache file."""
        return {
            "ok": self.found_days * DAY,
            "empty": self.not_found_days * DAY,
            "notfound": self.not_found_days * DAY,
            "page": self.pages_days * DAY,
        }

    @property
    def max_bytes(self) -> int:
        return int(self.max_size_mb * 1024**2)

    def folder(self) -> Path:
        """The cache folder."""
        if self.dir:
            return Path(self.dir).expanduser()
        base = os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache"
        return Path(base) / "papertools" / "bibcheck"


@dataclass
class SourcesConfig:
    """``[sources]``: how bibcheck talks to Crossref, arXiv, and the rest.

    Attributes:
        contact_email: Sent to Crossref and OpenAlex, whose "polite pools"
            are faster and more reliable for clients that identify
            themselves. Overridden by ``CROSSREF_MAILTO`` and
            ``OPENALEX_MAILTO``.
        semantic_scholar_api_key: Avoids Semantic Scholar's shared, often
            exhausted, anonymous pool. Overridden by
            ``SEMANTIC_SCHOLAR_API_KEY``.
        timeout: Seconds to wait for each request.
        link_timeout: Most seconds fetching one reference's link may take,
            DNS to last byte, so a deliberately slow site can't hold a check.
        any_link_port: Fetch links on any port. Off, only 80 and 443 are
            used, so the server can't be used to probe other services.

    """

    contact_email: str = ""
    semantic_scholar_api_key: str = ""
    timeout: float = 30
    link_timeout: float = 30
    any_link_port: bool = False

    @property
    def crossref_mailto(self) -> str:
        return os.environ.get("CROSSREF_MAILTO", self.contact_email)

    @property
    def openalex_mailto(self) -> str:
        return os.environ.get("OPENALEX_MAILTO", self.crossref_mailto)

    @property
    def s2_key(self) -> str:
        return os.environ.get(
            "SEMANTIC_SCHOLAR_API_KEY", self.semantic_scholar_api_key
        )


@dataclass
class CheckConfig:
    """``[check]``: defaults for checking and fixing.

    Attributes:
        max_authors: Author lists longer than this are written by --fix as
            their first names and "and others", and accepted in that form;
            0 always writes every author.

    """

    max_authors: int = 10


@dataclass
class Config:
    """Every setting, one attribute per table.

    Programs built on bibcheck may subclass this to add tables of their own.
    """

    cache: CacheConfig = field(default_factory=CacheConfig)
    sources: SourcesConfig = field(default_factory=SourcesConfig)
    check: CheckConfig = field(default_factory=CheckConfig)


C = TypeVar("C", bound=Config)


def config_path() -> Path:
    """``$BIBCHECK_CONFIG``, else ``~/.config/papertools/bibcheck.toml``."""
    if env := os.environ.get("BIBCHECK_CONFIG"):
        return Path(env).expanduser()
    base = os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config"
    return Path(base) / "papertools" / "bibcheck.toml"


def load_config(path: Path | None = None, cls: type[C] = Config) -> C:
    """Read the config file; the defaults if it doesn't exist.

    An explicitly given ``path`` must exist. ``cls`` is the settings class
    to fill: ``Config`` or a subclass with more tables.

    Raises:
        BibCheckError: The file can't be read or isn't TOML, or has an
            unknown table or key, or a value of the wrong type.

    """
    explicit = path is not None
    path = Path(path).expanduser() if explicit else config_path()
    try:
        data = tomllib.loads(path.read_text())
    except FileNotFoundError as exc:
        if explicit:
            raise BibCheckError(f"{path}: no such config file") from exc
        return cls()
    except (OSError, UnicodeDecodeError) as exc:
        raise BibCheckError(f"{path}: {exc}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise BibCheckError(f"{path}: not valid TOML: {exc}") from exc
    tables = {f.name: f.default_factory for f in fields(cls)}
    out = {}
    for name, table in data.items():
        if name not in tables:
            raise BibCheckError(
                f"{path}: unknown table [{name}] (known: "
                f"{', '.join(f'[{t}]' for t in tables)})"
            )
        if not isinstance(table, dict):
            raise BibCheckError(f"{path}: [{name}] must be a table")
        out[name] = _table(path, name, table, tables[name]())
    return cls(**out)


def _table(path: Path, name: str, table: dict, defaults):
    types = {f.name: f.type for f in fields(defaults)}
    values = {}
    for key, value in table.items():
        where = f"{path}: {name}.{key}"
        if key not in types:
            raise BibCheckError(
                f"{where} is not a setting (known: {', '.join(types)})"
            )
        kind = types[key]
        if kind is bool:
            if not isinstance(value, bool):
                raise BibCheckError(f"{where} must be true or false")
        elif kind is str:
            if not isinstance(value, str):
                raise BibCheckError(f"{where} must be a string")
        elif isinstance(value, bool) or not isinstance(value, int | float):
            raise BibCheckError(f"{where} must be a number")
        elif value < 0:
            raise BibCheckError(f"{where} must be 0 or more")
        elif kind is int and value != int(value):
            raise BibCheckError(f"{where} must be a whole number")
        else:
            value = kind(value)
        values[key] = value
    return type(defaults)(**values)
