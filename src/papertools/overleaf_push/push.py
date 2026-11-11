"""Commit, rebase onto the remote, and push, retrying while others edit.

Overleaf's git bridge turns online edits into commits, so a push made while
a coauthor is typing is usually rejected as non-fast-forward. Each attempt
fetches, rebases the local commits onto the remote, and pushes. Attempts come
in bursts a fraction of a second apart, to land in a pause between flushes of
someone's typing; between bursts the wait grows exponentially, to outlast
longer editing sessions without hammering the server.
"""

import os
import random
import re
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from .errors import ConflictError, OverleafPushError, RepoStateError

# Upper bound of the random wait between attempts within a burst, seconds.
BURST_JITTER = 1.0
# Wait after the first failed burst, doubled after each later one, seconds.
BACKOFF_BASE = 5.0
# Longest wait between bursts, seconds.
BACKOFF_CAP = 60.0

# Patched by tests.
sleep = time.sleep
clock = time.monotonic

# Failures that retrying will not fix.
_FATAL = re.compile(
    r"authentication failed|could not read (username|password)"
    r"|repository not found|permission denied|\b40[134]\b",
    re.IGNORECASE,
)
_REJECTED = re.compile(
    r"\[rejected\]|\[remote rejected\]|non-fast-forward|fetch first",
    re.IGNORECASE,
)


@dataclass
class Repo:
    """A work tree on a branch, with the remote branch it pushes to."""

    path: Path
    branch: str = ""
    remote: str = "origin"
    remote_branch: str = ""

    def git(self, *args: str) -> subprocess.CompletedProcess[str]:
        """Run git in the work tree; output is captured, stdin inherited.

        Credential helpers prompt on the terminal, not on stdout/stderr, so
        they still work. Messages are in English, for classifying failures.
        """
        return subprocess.run(
            ["git", "-C", str(self.path), *args],
            capture_output=True,
            text=True,
            env={**os.environ, "LC_ALL": "C"},
        )

    def check(self, *args: str) -> str:
        """Run git and return its stripped stdout; raise if it fails."""
        proc = self.git(*args)
        if proc.returncode:
            raise OverleafPushError(
                f"git {' '.join(args)} failed:\n{_output(proc)}"
            )
        return proc.stdout.strip()


def _output(proc: subprocess.CompletedProcess[str]) -> str:
    return (proc.stderr + proc.stdout).strip()


def _last_line(text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return lines[-1] if lines else "unknown error"


def is_fatal(output: str) -> bool:
    """Whether a failed fetch or push should not be retried."""
    return bool(_FATAL.search(output))


def describe(output: str) -> str:
    """A short reason for a failed fetch or push, for progress lines."""
    if _REJECTED.search(output):
        return "rejected, the remote changed"
    return _last_line(output)


def schedule(burst: int, rng: random.Random) -> Iterator[float]:
    """Yield the wait before each attempt, starting with 0 for the first.

    Attempts come in bursts of `burst`, `uniform(0, BURST_JITTER)` apart.
    After failed burst k (from 0), wait `uniform(0.5, 1) * min(BACKOFF_CAP,
    BACKOFF_BASE * 2**k)`: about 5, 10, 20, 40, 60, 60, ... seconds.
    """
    yield 0.0
    k = 0
    while True:
        for _ in range(burst - 1):
            yield rng.uniform(0, BURST_JITTER)
        backoff = min(BACKOFF_CAP, BACKOFF_BASE * 2 ** min(k, 16))
        yield rng.uniform(0.5, 1.0) * backoff
        k += 1


def open_repo(path: Path) -> Repo:
    """Check the repository can be pushed and find its upstream branch."""
    repo = Repo(path)
    proc = repo.git("rev-parse", "--is-inside-work-tree")
    if proc.returncode or proc.stdout.strip() != "true":
        raise RepoStateError(f"{path} is not inside a git work tree.")
    # Before the branch check: HEAD is detached during a rebase.
    git_dir = Path(repo.check("rev-parse", "--absolute-git-dir"))
    for name, what in [
        ("rebase-merge", "a rebase"),
        ("rebase-apply", "a rebase"),
        ("MERGE_HEAD", "a merge"),
        ("CHERRY_PICK_HEAD", "a cherry-pick"),
        ("REVERT_HEAD", "a revert"),
    ]:
        if (git_dir / name).exists():
            raise RepoStateError(
                f"{what} is in progress; finish or abort it first."
            )
    proc = repo.git("symbolic-ref", "--short", "-q", "HEAD")
    if proc.returncode:
        raise RepoStateError("HEAD is detached; check out a branch first.")
    repo.branch = proc.stdout.strip()
    remote = repo.git("config", f"branch.{repo.branch}.remote")
    merge = repo.git("config", f"branch.{repo.branch}.merge")
    if remote.returncode == 0 and merge.returncode == 0:
        repo.remote = remote.stdout.strip()
        repo.remote_branch = merge.stdout.strip().removeprefix("refs/heads/")
    else:
        repo.remote_branch = repo.branch
    if repo.remote == ".":
        raise RepoStateError(
            f"{repo.branch} tracks a local branch, not a remote."
        )
    return repo


def commit_changes(repo: Repo, message: str | None) -> bool:
    """Commit every change (respecting .gitignore); False if none."""
    if not repo.check("status", "--porcelain"):
        return False
    repo.check("add", "-A")
    if message is None:
        message = f"Update from {socket.gethostname()}"
    repo.check("commit", "-q", "-m", message)
    return True


def git_fetch(repo: Repo) -> subprocess.CompletedProcess[str]:
    """Fetch the remote branch into FETCH_HEAD."""
    return repo.git("fetch", "-q", repo.remote, repo.remote_branch)


def git_push(repo: Repo) -> subprocess.CompletedProcess[str]:
    """Push HEAD to the remote branch, without forcing."""
    return repo.git(
        "push", "-q", repo.remote, f"HEAD:refs/heads/{repo.remote_branch}"
    )


def rebase(repo: Repo, onto: str) -> None:
    """Rebase local commits onto `onto`, aborting on any conflict."""
    git_dir = Path(repo.check("rev-parse", "--absolute-git-dir"))

    def in_progress() -> bool:
        return any((git_dir / d).exists() for d in ("rebase-merge",
                                                     "rebase-apply"))

    try:
        proc = repo.git("rebase", "-q", onto)
        if proc.returncode == 0:
            return
        if not in_progress():
            raise OverleafPushError(f"git rebase failed:\n{_output(proc)}")
        files = repo.check(
            "-c", "core.quotePath=false",
            "diff", "--name-only", "--diff-filter=U",
        ).splitlines()
        raise ConflictError(files or ["(unknown files)"])
    finally:
        # Also on Ctrl-C: never leave the repository mid-rebase.
        if in_progress():
            repo.git("rebase", "--abort")


def overleaf_push(
    path: Path,
    *,
    message: str | None = None,
    burst: int = 5,
    timeout: float = 900,
    rng: random.Random | None = None,
) -> str:
    """Commit, then fetch, rebase and push until the push lands.

    Returns a summary line. Raises `OverleafPushError` (or a subclass) when
    retrying cannot help or `timeout` seconds pass without success.
    """
    repo = open_repo(path)
    if commit_changes(repo, message):
        print(f"Committed: {repo.check('log', '-1', '--format=%s')}",
              file=sys.stderr)
    target = f"{repo.remote}/{repo.remote_branch}"
    waits = schedule(burst, rng or random.Random())
    start = clock()
    attempt = 0
    wait = next(waits)
    while True:
        sleep(wait)
        attempt += 1
        proc = git_fetch(repo)
        if proc.returncode == 0:
            rebase(repo, "FETCH_HEAD")
            remote_head = repo.check("rev-parse", "FETCH_HEAD")
            head = repo.check("rev-parse", "HEAD")
            if head == remote_head:
                return f"Up to date with {target}."
            count = repo.check("rev-list", "--count", f"{remote_head}..HEAD")
            proc = git_push(repo)
            if proc.returncode == 0:
                return (
                    f"Pushed {count} commit(s) to {target} "
                    f"({remote_head[:7]}..{head[:7]}) on attempt {attempt}."
                )
            step = "push"
        else:
            step = "fetch"
        output = _output(proc)
        if is_fatal(output):
            raise OverleafPushError(f"git {step} failed:\n{output}")
        wait = next(waits)
        elapsed = clock() - start
        burst_no = (attempt - 1) // burst + 1
        if elapsed + wait >= timeout:
            raise OverleafPushError(
                f"gave up after {attempt} attempt(s) in {burst_no} burst(s) "
                f"over {elapsed:.0f}s. Last git {step} error:\n{output}\n"
                "Your commits are rebased onto the remote and safe to push; "
                "rerun overleaf-push to keep trying."
            )
        then = "backing off" if attempt % burst == 0 else "retrying"
        print(
            f"attempt {attempt} (burst {burst_no}): {step} "
            f"{describe(output)}; {then} in {wait:.1f}s",
            file=sys.stderr,
        )
