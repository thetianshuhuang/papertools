"""bibcheck's on-disk lookup cache.

One file per request, named by a hash of the request, under
``$XDG_CACHE_HOME/papertools/bibcheck``. Each file starts with a header line
naming its kind, which decides how long it is kept:

- ``ok``: a record or search results (14 days);
- ``empty``: a search that found nothing (7 days);
- ``notfound``: a 404 (7 days);
- ``page``: a web page fetched to check a reference's link (3 days).

The lifetimes, the size cap, the folder, and how often to prune are the
config file's ``[cache]`` table (see :mod:`.config`).

Negative answers are kept for less time because they're the ones that change:
a paper gets published, a DOI gets registered. Files are written to a
temporary name and renamed, so a reader never sees half a file, even with
several processes or threads sharing the folder. ``prune`` deletes expired files
and then the oldest ones until the cache fits its size cap; it runs at most
once a day per cache folder (``maybe_prune``).
"""

import hashlib
import time
from pathlib import Path

from ._files import atomic_write, is_stale_tmp
from .config import CacheConfig

_DEFAULTS = CacheConfig()
DEFAULT_TTL = _DEFAULTS.ttl
DEFAULT_MAX_BYTES = _DEFAULTS.max_bytes
PRUNE_EVERY = _DEFAULTS.prune_every_hours * 3600
_MAGIC = b"papertools-cache 1 "
# Older versions marked a 404 with the Python string "\0404", which is " 4"
# ("\040" is an octal escape for a space).
_LEGACY_404 = b" 4"
_STAMP = ".last-prune"


class DiskCache:
    """Kind-aware, size-capped file cache. Never raises on I/O errors.

    Args:
        root: The cache folder.
        ttl: Seconds to keep each kind (defaults to ``DEFAULT_TTL``).
        max_bytes: Size cap enforced by ``prune``.

    """

    def __init__(
        self,
        root: Path,
        *,
        ttl: dict[str, float] | None = None,
        max_bytes: int = DEFAULT_MAX_BYTES,
        prune_every: float = PRUNE_EVERY,
    ):
        self.root = Path(root)
        self.ttl = DEFAULT_TTL | (ttl or {})
        self.max_bytes = max_bytes
        self.prune_every = prune_every

    @classmethod
    def from_config(
        cls, config: CacheConfig, root: Path | None = None
    ) -> "DiskCache":
        """A cache with ``config``'s settings, in ``root`` or its folder."""
        return cls(
            root or config.folder(),
            ttl=config.ttl,
            max_bytes=config.max_bytes,
            prune_every=config.prune_every_hours * 3600,
        )

    def path(self, key: str) -> Path:
        """The file holding ``key``."""
        digest = hashlib.sha256(key.encode()).hexdigest()
        return self.root / digest[:2] / digest

    def get(self, key: str, now: float | None = None) -> tuple[str, bytes]:
        """(kind, body) for ``key``, or ("", b"") if absent or expired."""
        path = self.path(key)
        try:
            age = (now or time.time()) - path.stat().st_mtime
            data = path.read_bytes()
        except OSError:
            return "", b""
        kind, body = _parse(data)
        if age >= self.ttl.get(kind, 0):
            return "", b""
        return kind, body

    def put(self, key: str, kind: str, body: bytes) -> None:
        """Store ``body`` as ``kind``, atomically."""
        try:
            atomic_write(self.path(key), _MAGIC + kind.encode() + b"\n" + body)
        except OSError:
            pass  # A cache that can't be written is just a slower run.

    def prune(self, now: float | None = None) -> tuple[int, int]:
        """Delete expired files, then the oldest until under the size cap.

        Returns:
            (files deleted, bytes kept).

        """
        now = now or time.time()
        files: list[tuple[float, int, Path]] = []
        removed = 0
        for path in self.root.glob("??/*"):
            try:
                stat = path.stat()
                age = now - stat.st_mtime
                if path.suffix == ".tmp":
                    expired = is_stale_tmp(path, age)
                else:
                    with path.open("rb") as f:
                        kind, _ = _parse(f.readline())
                    expired = age >= self.ttl.get(kind, 0)
                if expired:
                    path.unlink()
                    removed += 1
                else:
                    files.append((stat.st_mtime, stat.st_size, path))
            except OSError:
                continue
        total = sum(size for _, size, _ in files)
        for _, size, path in sorted(files):
            if total <= self.max_bytes:
                break
            try:
                path.unlink()
            except OSError:
                continue
            total -= size
            removed += 1
        return removed, total

    def maybe_prune(self, every: float | None = None) -> bool:
        """Prune if it hasn't been done in ``every`` seconds."""
        every = self.prune_every if every is None else every
        stamp = self.root / _STAMP
        try:
            if time.time() - stamp.stat().st_mtime < every:
                return False
        except FileNotFoundError:
            pass
        except OSError:
            return False
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            stamp.touch()
        except OSError:
            return False
        self.prune()
        return True


def _parse(data: bytes) -> tuple[str, bytes]:
    """Split a cache file into (kind, body); headerless files are legacy."""
    if data.startswith(_MAGIC):
        header, _, body = data.partition(b"\n")
        return header[len(_MAGIC) :].decode(errors="replace"), body
    if data.rstrip(b"\n") == _LEGACY_404:
        return "notfound", b""
    return "ok", data
