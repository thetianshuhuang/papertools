"""Command-line arguments and repository state checks."""

import subprocess

import pytest

from papertools.overleaf_push.cli import main


def test_not_a_repository(tmp_path, capsys):
    """Refuse to run outside a git work tree."""
    assert main(["--repo", str(tmp_path)]) == 2
    assert "not inside a git work tree" in capsys.readouterr().err


def test_detached_head(remote, capsys):
    """Refuse to push from a detached HEAD."""
    remote.git(remote.local, "checkout", "-q", "--detach")
    assert main(["--repo", str(remote.local)]) == 2
    assert "detached" in capsys.readouterr().err


def test_rebase_in_progress(remote, capsys):
    """Refuse to run in the middle of a rebase."""
    remote.online_edit("main.tex", "online\n")
    remote.edit(remote.local, "main.tex", "local\n")
    remote.git(remote.local, "commit", "-qam", "Mine")
    remote.git(remote.local, "fetch", "-q")
    proc = subprocess.run(
        ["git", "-C", str(remote.local), "rebase", "origin/main"],
        capture_output=True,
    )
    assert proc.returncode != 0
    assert main(["--repo", str(remote.local)]) == 2
    assert "rebase is in progress" in capsys.readouterr().err


@pytest.mark.parametrize(
    "args", [["--burst", "0"], ["--timeout", "nan"], ["--timeout", "0"]]
)
def test_bad_values(args):
    """Reject a burst below 1 and nonpositive or nonfinite timeouts."""
    assert main(args) == 2


@pytest.mark.parametrize("argv", [["extra"], ["--burst", "many"]])
def test_invalid_arguments(argv):
    """Exit with usage status for unparseable arguments."""
    with pytest.raises(SystemExit) as exc:
        main(argv)
    assert exc.value.code == 2


def test_help_and_version(capsys):
    """Print help, including the -m alias, and the package version."""
    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "overleaf-push" in out and "-m" in out
    assert main(["--version"]) == 0
    assert capsys.readouterr().out.strip() == "pt overleaf-push 0.1.0"
