"""Pushing, retrying, and conflicts against a real local git remote."""

import random
import subprocess

import pytest

from papertools.overleaf_push import push
from papertools.overleaf_push.cli import main


def run(remote, *args: str) -> int:
    """Run overleaf-push on the local clone."""
    return main(["--repo", str(remote.local), *args])


def rejection() -> subprocess.CompletedProcess[str]:
    """A push rejected because the remote has new commits."""
    return subprocess.CompletedProcess(
        [], 1, "", " ! [rejected]        main -> main (fetch first)\n"
        "error: failed to push some refs to 'https://git.overleaf.com/x'\n",
    )


def test_commits_and_pushes(remote, capsys):
    """Commit uncommitted changes with the given message and push them."""
    remote.edit(remote.local, "main.tex", "line one\nline 2\nline three\n")
    remote.edit(remote.local, "new.tex", "new file\n")
    assert run(remote, "-m", "Fix intro") == 0
    assert remote.remote_log() == ["Fix intro", "Initial"]
    files = remote.git(remote.bare, "ls-tree", "--name-only", "main")
    assert "new.tex" in files.split()
    assert "Pushed 1 commit(s) to origin/main" in capsys.readouterr().err


def test_default_message(remote):
    """Name the machine in the default commit message."""
    remote.edit(remote.local, "main.tex", "changed\n")
    assert run(remote) == 0
    assert remote.remote_log()[0].startswith("Update from ")


def test_pushes_existing_commits(remote):
    """Push commits already made, with a clean tree."""
    remote.edit(remote.local, "main.tex", "changed\n")
    remote.git(remote.local, "commit", "-qam", "Mine")
    assert run(remote) == 0
    assert remote.remote_log() == ["Mine", "Initial"]


def test_up_to_date(remote, capsys):
    """Pull in online edits and report there is nothing to push."""
    remote.online_edit("refs.bib", "% online\n")
    assert run(remote) == 0
    assert "Up to date" in capsys.readouterr().err
    assert (remote.local / "refs.bib").read_text() == "% online\n"


def test_retries_when_remote_changes_mid_push(remote, monkeypatch, capsys):
    """Retry after a real rejection caused by an edit between fetch/push."""
    real_push = push.git_push
    calls = []

    def racing_push(repo):
        calls.append(repo)
        if len(calls) == 1:
            remote.online_edit("refs.bib", "% online\n")
        return real_push(repo)

    monkeypatch.setattr(push, "git_push", racing_push)
    remote.edit(remote.local, "main.tex", "line one\nline 2\nline three\n")
    assert run(remote, "-m", "Mine") == 0
    assert len(calls) == 2
    assert remote.remote_log() == ["Mine", "Update on Overleaf.", "Initial"]
    err = capsys.readouterr().err
    assert "attempt 1 (burst 1): push rejected, the remote changed" in err
    assert "on attempt 2" in err


def test_conflict_aborts_rebase(remote, capsys):
    """Stop on a real conflict, leaving the local commit untouched."""
    remote.online_edit("main.tex", "line one\nonline\nline three\n")
    remote.edit(remote.local, "main.tex", "line one\nlocal\nline three\n")
    assert run(remote, "-m", "Mine") == 1
    err = capsys.readouterr().err
    assert "main.tex" in err and "rebase was aborted" in err
    git_dir = remote.local / ".git"
    assert not (git_dir / "rebase-merge").exists()
    assert not (git_dir / "rebase-apply").exists()
    assert remote.git(remote.local, "log", "-1", "--format=%s") == "Mine"
    assert remote.git(remote.local, "status", "--porcelain") == ""
    assert remote.remote_log()[0] == "Update on Overleaf."


def test_gives_up_after_timeout(remote, monkeypatch, capsys):
    """Keep retrying rejections until the timeout, then give up."""
    now = [0.0]
    pushes = []
    monkeypatch.setattr(push, "clock", lambda: now[0])
    monkeypatch.setattr(push, "sleep", lambda s: now.__setitem__(0, now[0] + s))
    monkeypatch.setattr(push, "git_push", lambda repo: pushes.append(1)
                        or rejection())
    remote.edit(remote.local, "main.tex", "changed\n")
    assert run(remote, "--burst", "3", "--timeout", "30") == 1
    err = capsys.readouterr().err
    assert "gave up after" in err and "fetch first" in err
    assert "backing off" in err
    assert len(pushes) > 3
    assert now[0] < 30
    assert remote.remote_log() == ["Initial"]


@pytest.mark.parametrize(
    "stderr",
    ["fatal: Authentication failed for 'https://git.overleaf.com/x/'",
     "fatal: unable to access 'https://x/': The requested URL returned "
     "error: 403"],
)
def test_fatal_push_error_is_not_retried(remote, monkeypatch, capsys, stderr):
    """Stop at once on authentication errors."""
    pushes = []

    def failing_push(repo):
        pushes.append(1)
        return subprocess.CompletedProcess([], 128, "", stderr)

    monkeypatch.setattr(push, "git_push", failing_push)
    remote.edit(remote.local, "main.tex", "changed\n")
    assert run(remote) == 1
    assert len(pushes) == 1
    assert stderr in capsys.readouterr().err


def test_schedule():
    """Bursts of short waits, then exponentially growing, capped backoff."""
    waits = push.schedule(3, random.Random(0))
    got = [next(waits) for _ in range(40)]
    assert got[0] == 0
    assert all(w <= push.BURST_JITTER for i, w in enumerate(got)
               if i % 3 != 0)
    assert 2.5 <= got[3] <= 5
    assert 5 <= got[6] <= 10
    assert 10 <= got[9] <= 20
    assert all(30 <= got[i] <= 60 for i in range(15, 40, 3))


def test_classify():
    """Tell retryable rejections from fatal errors."""
    assert not push.is_fatal(rejection().stderr)
    assert push.describe(rejection().stderr) == "rejected, the remote changed"
    assert push.is_fatal("remote: Repository not found.")
    # Hex project ids must not look like HTTP status codes.
    url = "To https://git.overleaf.com/6a403b"
    assert not push.is_fatal(f"{url}\n ! [rejected] main -> main")
