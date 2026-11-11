"""overleaf-push's console entry point."""

import math
import sys
from pathlib import Path
from typing import Annotated

import tyro

from .._cli import run
from . import push
from .errors import OverleafPushError, RepoStateError


def overleaf_push(
    *,
    message: Annotated[
        str | None, tyro.conf.arg(aliases=("-m",), metavar="TEXT")
    ] = None,
    burst: int = 5,
    timeout: float = 900,
    repo: Path = Path("."),
) -> int:
    """Commit, rebase onto Overleaf, and push, retrying until it lands.

    Uncommitted changes are committed first (git add -A). Each attempt
    fetches, rebases your commits onto the remote, and pushes; rejections
    caused by coauthors editing online are retried in bursts with
    exponential backoff between them. Conflicts abort the rebase and stop.

    Args:
        message: Commit message, if needed; "Update from <hostname>" if unset.
        burst: Attempts per burst, each up to 1 s apart, before backing off.
        timeout: Give up after this many seconds.
        repo: Repository to push.

    """
    if burst < 1:
        print("overleaf-push: --burst must be at least 1.", file=sys.stderr)
        return 2
    if not math.isfinite(timeout) or timeout <= 0:
        print(
            "overleaf-push: --timeout must be finite and positive.",
            file=sys.stderr,
        )
        return 2
    try:
        summary = push.overleaf_push(
            repo, message=message, burst=burst, timeout=timeout
        )
    except RepoStateError as exc:
        print(f"overleaf-push: {exc}", file=sys.stderr)
        return 2
    except OverleafPushError as exc:
        print(f"overleaf-push: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("overleaf-push: cancelled.", file=sys.stderr)
        return 130
    print(summary, file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    """Run the command and return its exit status."""
    return run(overleaf_push, "overleaf-push", argv)
