"""One run of bibcheck over a .bib text: parse, check each entry, fix.

A session can be run to the end, as the command line does, or stepped
through one entry at a time, so several sessions can take turns and be
cancelled between entries.
"""

from collections.abc import Iterator
from dataclasses import dataclass, field

from .bibtex import BibSyntaxError, Entry, parse_bib_lenient
from .checks import check
from .duplicates import Duplicates, duplicates
from .errors import BibCheckError
from .fix import Fix, fix_entry, splice
from .issues import DEFAULT_MAX_AUTHORS, Issue
from .links import LinkChecker
from .report import MAX_SYNTAX_ERRORS, entry_json, fix_json, report_json
from .resolve import Resolution, Resolver
from .sources import Sources


@dataclass(frozen=True)
class CheckOptions:
    """How to check a file, whoever runs the check.

    Attributes:
        sentence_case: The bibliography style lowercases titles.
        max_authors: Longer author lists are written (and accepted) as
            their first names and "and others"; 0 for no limit.
        check_links: Fetch links, and look for links for unindexed entries.
        fix: Work out fixes after checking.
        refresh: Ignore cached answers. Applied by whoever runs the session
            (``Http(refresh=...)`` or ``Http.using(refresh=...)``).
        keys: Only check these entries; empty for all.

    """

    sentence_case: bool = False
    max_authors: int = DEFAULT_MAX_AUTHORS
    check_links: bool = True
    fix: bool = False
    refresh: bool = False
    keys: frozenset[str] = frozenset()

    def __post_init__(self):
        """Reject impossible values."""
        if self.max_authors < 0:
            raise BibCheckError("max_authors must be 0 or more")


class UnknownKeysError(BibCheckError):
    """Keys were asked for that the file doesn't have."""


class NoEntriesError(BibCheckError):
    """Nothing in the text could be read as an entry."""

    def __init__(self, syntax_errors: list[BibSyntaxError]):
        self.syntax_errors = syntax_errors
        shown = syntax_errors[:MAX_SYNTAX_ERRORS]
        more = len(syntax_errors) - len(shown)
        message = "\n\n".join(str(e) for e in shown) or "no entries found"
        if more:
            message += f"\n\n...and {more} more syntax errors"
        super().__init__(message)


@dataclass
class EntryResult:
    """One checked entry: what was found, the issues, and its report."""

    entry: Entry
    resolution: Resolution
    issues: list[Issue]
    report: dict = field(repr=False, default_factory=dict)


class CheckSession:
    """Check (and optionally fix) one .bib text.

    Raises (on creation):
        NoEntriesError: Nothing could be parsed.
        UnknownKeysError: ``options.keys`` names entries the file lacks.

    """

    def __init__(self, text: str, options: CheckOptions, sources: Sources):
        self.text = text
        self.options = options
        self.sources = sources
        entries, self.syntax_errors = parse_bib_lenient(text)
        if not entries:
            raise NoEntriesError(self.syntax_errors)
        if options.keys:
            if unknown := options.keys - {e.key for e in entries}:
                raise UnknownKeysError(
                    f"no such key(s): {', '.join(sorted(unknown))}"
                )
            entries = [e for e in entries if e.key in options.keys]
        self.entries = entries
        self.results: list[EntryResult] = []
        self.fixes: list[Fix] | None = None
        self.fixed_text: str | None = None
        self.phase = "checking"  # then "fixing" (if asked), then "done"
        self._fixing = ""

    @property
    def current_key(self) -> str:
        """The entry the next step works on ("" when done)."""
        if self.phase == "checking" and len(self.results) < len(self.entries):
            return self.entries[len(self.results)].key
        return self._fixing if self.phase == "fixing" else ""

    def check(self) -> Iterator[EntryResult]:
        """Check the entries, yielding each result as it's ready."""
        resolver = Resolver(self.sources)
        links = LinkChecker(self.sources) if self.options.check_links else None
        dups, same_work = duplicates(self.entries), Duplicates()
        for entry in self.entries:
            res = resolver.resolve(entry)
            if links:
                res.link = links.check(entry, res)
            issues = dups.get(entry.key, []) + same_work.check(entry, res)
            issues += check(
                entry,
                res,
                sentence_case=self.options.sentence_case,
                max_authors=self.options.max_authors,
            )
            result = EntryResult(
                entry, res, issues, entry_json(entry, res, issues)
            )
            self.results.append(result)
            yield result

    def fix(self) -> Iterator[Fix]:
        """Work out each entry's fix (after ``check``), yielding each one.

        When done, ``fixes`` and ``fixed_text`` are set. Nothing is written.
        """
        fixes = []
        for r in self.results:
            self._fixing = r.entry.key
            if f := fix_entry(
                r.entry,
                r.resolution,
                r.issues,
                self.sources,
                self.options.max_authors,
            ):
                fixes.append(f)
                yield f
        self.fixes = fixes
        self.fixed_text = splice(self.text, fixes)

    def steps(self) -> Iterator[EntryResult | Fix]:
        """Check, then fix if asked: one entry per step. Sets ``phase``."""
        self.phase = "checking"
        yield from self.check()
        if self.options.fix:
            self.phase = "fixing"
            yield from self.fix()
        self.phase = "done"

    def run(self) -> "CheckSession":
        """Run every step."""
        for _ in self.steps():
            pass
        return self

    def report(
        self,
        file: str = "",
        why: str = "",
        backup: str | None = None,
        since: int = 0,
    ) -> dict:
        """The JSON report (see :mod:`.report`), entries from ``since`` on."""
        return report_json(
            file,
            [r.report for r in self.results],
            sentence_case=self.options.sentence_case,
            why=why,
            fix=None if self.fixes is None else fix_json(self.fixes, backup),
            syntax_errors=[e.as_dict() for e in self.syntax_errors],
            since=since,
        )
