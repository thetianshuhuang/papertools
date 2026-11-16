"""bibcheck's console entry point."""

import json as _json
import math
import sys
from pathlib import Path
from typing import Annotated, Literal

import tyro

from .._cli import run
from .config import load_config
from .errors import BibCheckError
from .fix import write_fixed
from .issues import NOTE
from .session import (
    CheckOptions,
    CheckSession,
    NoEntriesError,
    UnknownKeysError,
)
from .sources import Http, Sources
from .style import detect


class _Progress:
    """A single self-overwriting status line, only on a terminal."""

    def __init__(self):
        self.live = sys.stderr.isatty()
        self.text = ""

    def update(self, text: str) -> None:
        self.text = text
        if self.live:
            sys.stderr.write(f"\r\033[K{text}")
            sys.stderr.flush()

    def detail(self, text: str) -> None:
        """Append a temporary detail, e.g. why the run is waiting."""
        if self.live:
            sys.stderr.write(f"\r\033[K{self.text} ({text})")
            sys.stderr.flush()

    def clear(self) -> None:
        if self.live:
            sys.stderr.write("\r\033[K")
            sys.stderr.flush()


def bibcheck(
    bib: Path,
    /,
    *,
    keys: Annotated[str | None, tyro.conf.arg(metavar="K1,K2")] = None,
    fix: bool = False,
    notes: bool = False,
    case: Literal["auto", "sentence", "keep"] = "auto",
    max_authors: int | None = None,
    no_links: bool = False,
    json: bool = False,
    no_cache: bool = False,
    refresh: bool = False,
    timeout: float | None = None,
    config: Path | None = None,
) -> int:
    r"""Check a .bib file against Crossref, arXiv, OpenReview, and DBLP.

    Reports titles, authors, venues, years, pages, and DOIs that disagree with
    the authoritative record, and arXiv entries with a published version.
    Entries no index has are checked by their URL instead, and a URL is
    suggested for those without one. Exits 1 if any warning was reported.

    Args:
        bib: BibTeX file to check.
        keys: Only check these comma-separated keys.
        fix: Rewrite entries with warnings from the authoritative BibTeX,
            keeping their keys; the original is saved as BIB.bak.
        notes: Also show notes: minor findings such as nicknames, or
            differences only arXiv or DBLP report, that are usually fine.
        case: Whether the bibliography style lowercases titles, which makes
            unbraced acronyms (MLPs, MIMO) warnings. "auto" reads the
            \bibliographystyle in .tex files next to the .bib.
        max_authors: Author lists longer than this are written by --fix as
            their first MAX_AUTHORS names and "and others", and are accepted
            in that form. 0 always writes every author. Default: the config
            file's check.max_authors (10).
        no_links: Don't fetch entries' URLs, or look for URLs for entries
            that no index has.
        json: Print the results as one JSON document (notes included)
            instead of text. Progress still goes to stderr.
        no_cache: Don't use or update the lookup cache (answers are kept 14
            days, "not found" 7 days, and checked web pages 3 days).
        refresh: Look everything up again, ignoring cached answers, and
            update the cache with the new ones.
        timeout: Timeout in seconds for each request. Default: the config
            file's sources.timeout (30).
        config: Settings file (cache lifetimes and size, contact email, API
            key, defaults). Default: $BIBCHECK_CONFIG, else
            ~/.config/papertools/bibcheck.toml, if it exists.

    """
    try:
        settings = load_config(config)
    except BibCheckError as exc:
        print(f"bibcheck: {exc}", file=sys.stderr)
        return 2
    if max_authors is None:
        max_authors = settings.check.max_authors
    if timeout is None:
        timeout = settings.sources.timeout
    if not math.isfinite(timeout) or timeout <= 0:
        print(
            "bibcheck: --timeout must be finite and positive.", file=sys.stderr
        )
        return 2
    if max_authors < 0:
        print("bibcheck: --max-authors must be 0 or more.", file=sys.stderr)
        return 2
    try:
        text = bib.read_text()
    except (OSError, UnicodeDecodeError) as exc:
        print(f"bibcheck: {bib}: {exc}", file=sys.stderr)
        return 1
    wanted = frozenset(k.strip() for k in (keys or "").split(",") if k.strip())
    if case == "auto":
        sentence_case, why = detect(bib)
    else:
        sentence_case, why = case == "sentence", f"--case {case}"
    options = CheckOptions(
        sentence_case=sentence_case,
        max_authors=max_authors,
        check_links=not no_links,
        fix=fix,
        refresh=refresh,
        keys=wanted,
    )
    progress = _Progress()
    http = Http(
        timeout=timeout,
        cache_dir=None if no_cache else settings.cache.folder(),
        on_wait=progress.detail,
        refresh=refresh,
        config=settings,
    )
    # Broken entries are reported and skipped; the rest are checked.
    try:
        session = CheckSession(text, options, Sources(http))
    except NoEntriesError as exc:
        for error in exc.syntax_errors or [exc]:
            print(f"bibcheck: {bib}: {error}", file=sys.stderr)
        return 1
    except UnknownKeysError as exc:
        print(f"bibcheck: {exc}", file=sys.stderr)
        return 2
    if case == "auto":
        print(f"Style: {why}", file=sys.stderr)

    out = _Text(notes) if not json else None
    if out and session.syntax_errors:
        out.syntax_errors(session.syntax_errors)
    total = len(session.entries)
    progress.update(f"[1/{total}] {session.entries[0].key}")
    try:
        for n, result in enumerate(session.check(), 1):
            progress.clear()
            for error in result.resolution.errors:
                print(f"{result.entry.key}: {error}", file=sys.stderr)
            if out:
                out.entry(result.report)
            if n < total:
                progress.update(f"[{n + 1}/{total}] {session.entries[n].key}")
        progress.update("Fixing" if fix else "")
        for _ in session.fix() if fix else ():
            pass
        progress.clear()
    except KeyboardInterrupt:
        progress.clear()
        print("bibcheck: cancelled.", file=sys.stderr)
        return 130

    backup = None
    if fix and session.fixes and any(f.applied for f in session.fixes):
        try:
            backup = str(write_fixed(bib, session.fixed_text))
        except OSError as exc:
            print(f"bibcheck: cannot write {bib}: {exc}", file=sys.stderr)
            return 1
    report = session.report(str(bib), why, backup)
    if out:
        out.summary(report["summary"])
        if report["fix"]:
            out.fixes(report["fix"])
    else:
        _json.dump(report, sys.stdout, indent=2, ensure_ascii=False)
        print()
    summary = report["summary"]
    failed = (
        summary["warnings"] or summary["unchecked"] or summary["syntax_errors"]
    )
    return 1 if failed else 0


class _Text:
    """The human-readable report, printed as entries are checked."""

    def __init__(self, notes: bool):
        self.notes = notes

    def entry(self, e: dict) -> None:
        shown = [i for i in e["issues"] if self.notes or i["level"] != NOTE]
        if shown:
            found = (
                f"  ({e['found']['label']}, via {e['via']})"
                if e["found"]
                else ""
            )
            print(f"{e['key']}{found}")
            for i in shown:
                print(f"  {i['level']:4}  {i['kind']:7} {i['message']}")
                for line in i["details"]:
                    print(f"{'':16}{line}")
        sys.stdout.flush()

    @staticmethod
    def syntax_errors(errors) -> None:
        n = len(errors)
        print(
            f"{n} syntax error{'s' if n > 1 else ''}; "
            f"{'these entries were' if n > 1 else 'this entry was'} skipped:"
        )
        for error in errors:
            print(f"  {str(error).replace(chr(10), chr(10) + '  ')}")
        print()

    def summary(self, s: dict) -> None:
        text = (
            f"Checked {s['checked']} entries: {s['warnings']} with warnings"
            f", {s['missing']} not found"
        )
        if s["notes_only"]:
            text += f", {s['notes_only']} with notes only"
            if not self.notes:
                text += " (--notes to show)"
        if s["unchecked"]:
            text += f", {s['unchecked']} unchecked (source errors)"
        if s.get("syntax_errors"):
            text += f", {s['syntax_errors']} skipped (syntax errors)"
        print(text + ".")

    @staticmethod
    def fixes(f: dict) -> None:
        keys = ", ".join(x["key"] for x in f["fixed"]) or "-"
        print(f"Fixed {len(f['fixed'])}: {keys}")
        for x in f["fixed"]:
            if x["kind"] != "rebuilt":
                print(f"  {x['key']}: {x['change']}")
        for x in f["skipped"]:
            print(f"Not fixed: {x['key']} ({x['reason']})")
        if f["backup"]:
            print(f"Backup: {f['backup']}")


def main(argv: list[str] | None = None) -> int:
    """Run the command and return its exit status."""
    return run(bibcheck, "pt bibcheck", argv)
