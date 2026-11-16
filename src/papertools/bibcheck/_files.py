"""Small file helpers for bibcheck's on-disk files."""

import os
import tempfile
from pathlib import Path

# A temporary file older than this was left by a writer that crashed.
STALE_TMP_SECONDS = 3600


def atomic_write(path: Path, data: bytes) -> None:
    """Write ``data`` to ``path`` so readers see the old or the new file.

    Missing folders are created. Raises OSError.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def is_stale_tmp(path: Path, age: float) -> bool:
    """Whether ``path`` is a temporary file abandoned by ``atomic_write``."""
    return path.suffix == ".tmp" and age > STALE_TMP_SECONDS
