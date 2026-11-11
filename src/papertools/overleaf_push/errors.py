"""Errors that can be presented directly to a command-line user."""


class OverleafPushError(Exception):
    """An expected failure that retrying will not fix."""


class RepoStateError(OverleafPushError):
    """The repository is not in a state where pushing makes sense."""


class ConflictError(OverleafPushError):
    """Local commits conflict with edits made on Overleaf."""

    def __init__(self, files: list[str]):
        self.files = files
        listing = "".join(f"\n  {name}" for name in files)
        super().__init__(
            "local commits conflict with Overleaf edits in:"
            f"{listing}\n"
            "The rebase was aborted, so your commits are unchanged. Run "
            "`git pull --rebase`, resolve the conflicts, `git rebase "
            "--continue`, then rerun overleaf-push."
        )
