"""A bare repository standing in for Overleaf, with two clones."""

import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

from papertools.overleaf_push import push


def git(path: Path, *args: str) -> str:
    """Run git in `path` and return its stripped stdout."""
    return subprocess.run(
        ["git", "-C", str(path), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def edit(repo: Path, name: str, text: str) -> None:
    """Write `text` to `name` in `repo`."""
    (repo / name).write_text(text)


@dataclass
class Remote:
    """The bare remote, our clone, and a clone edited "online"."""

    bare: Path
    local: Path
    online: Path
    git = staticmethod(git)
    edit = staticmethod(edit)

    def online_edit(self, name: str, text: str) -> None:
        """Commit and push an edit, as Overleaf does for online edits."""
        git(self.online, "pull", "-q", "--rebase")
        edit(self.online, name, text)
        git(self.online, "add", "-A")
        git(self.online, "commit", "-q", "-m", "Update on Overleaf.")
        git(self.online, "push", "-q")

    def remote_log(self) -> list[str]:
        """Commit subjects on the remote's main, newest first."""
        return git(self.bare, "log", "--format=%s", "main").splitlines()


def _identity(path: Path) -> None:
    git(path, "config", "user.name", "Test")
    git(path, "config", "user.email", "test@example.com")


@pytest.fixture
def remote(tmp_path, monkeypatch) -> Remote:
    """Set up the remote with one commit, cloned twice; never sleep."""
    bare = tmp_path / "remote.git"
    seed = tmp_path / "seed"
    subprocess.run(
        ["git", "init", "-q", "--bare", "-b", "main", str(bare)], check=True
    )
    subprocess.run(["git", "init", "-q", "-b", "main", str(seed)], check=True)
    _identity(seed)
    edit(seed, "main.tex", "line one\nline two\nline three\n")
    edit(seed, "refs.bib", "% refs\n")
    git(seed, "add", "-A")
    git(seed, "commit", "-q", "-m", "Initial")
    git(seed, "push", "-q", str(bare), "main")
    clones = {}
    for name in ("local", "online"):
        clones[name] = tmp_path / name
        subprocess.run(
            ["git", "clone", "-q", str(bare), str(clones[name])], check=True
        )
        _identity(clones[name])
    monkeypatch.setattr(push, "sleep", lambda seconds: None)
    return Remote(bare, clones["local"], clones["online"])
