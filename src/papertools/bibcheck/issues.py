"""What a check finds: issues, their kinds and levels."""

from dataclasses import dataclass, field
from enum import Enum

WARN, NOTE = "warn", "note"
# Author lists longer than this are written (by --fix) and accepted (by the
# check) as their first names followed by "and others".
DEFAULT_MAX_AUTHORS = 10


class IssueKind(str, Enum):
    """What an issue is about. A ``str``, so it compares and prints as one."""

    MISSING = "missing"
    URL = "url"
    TITLE = "title"
    AUTHOR = "author"
    ARXIV = "arxiv"
    VENUE = "venue"
    YEAR = "year"
    PAGES = "pages"
    DOI = "doi"
    KEY = "key"
    SYNTAX = "syntax"
    FIELDS = "fields"

    def __str__(self) -> str:
        """The kind's name, e.g. ``author``."""
        return self.value

    @property
    def rebuild_fixes(self) -> bool:
        """Whether rebuilding the entry from its record can fix this.

        Not a link (a rebuild would drop it), a duplicate, or an escaping
        problem; ``--fix`` doesn't rebuild an entry for those alone.
        """
        return self not in {IssueKind.URL, IssueKind.KEY, IssueKind.SYNTAX}


@dataclass
class Issue:
    """One finding. ``level`` is ``warn`` (likely wrong) or ``note``.

    ``details`` holds the workings behind it: what was fetched and what it
    showed, and links for checking by hand.
    """

    level: str
    kind: IssueKind
    message: str
    details: list[str] = field(default_factory=list)

    def __post_init__(self):
        """Accept a kind's name; reject unknown kinds."""
        self.kind = IssueKind(self.kind)
