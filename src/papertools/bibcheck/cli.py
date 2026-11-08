"""bibcheck's console entry point."""

import math
import sys
from collections import Counter
from pathlib import Path
from typing import Annotated, Literal

import tyro

from .._cli import run
from .bibtex import parse_bib
from .checks import NOTE, WARN, Issue, check
from .errors import BibCheckError
from .fix import fix_file
from .resolve import Resolver
from .sources import Http, Sources, default_cache_dir
from .style import detect
from .text import norm_title


def _duplicates(entries) -> dict[str, list[Issue]]:
    """Duplicate keys and duplicate titles within the file."""
    out: dict[str, list[Issue]] = {}
    keys = Counter(e.key for e in entries)
    titles: dict[str, list[str]] = {}
    for e in entries:
        if t := norm_title(e.fields.get("title", "")):
            titles.setdefault(t, []).append(e.key)
    for e in entries:
        found = []
        if keys[e.key] > 1:
            found.append(Issue(WARN, "key", "defined more than once"))
        same = [
            k for k in titles.get(norm_title(e.fields.get("title", "")), [])
        ]
        if len(same) > 1 and e.key == same[0]:
            others = ", ".join(k for k in same[1:])
            found.append(Issue(WARN, "key", f"same paper as {others}"))
        if found:
            out[e.key] = found
    return out


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
    no_cache: bool = False,
    timeout: float = 30,
) -> int:
    r"""Check a .bib file against Crossref, arXiv, OpenReview, and DBLP.

    Reports titles, authors, venues, years, pages, and DOIs that disagree with
    the authoritative record, and arXiv entries with a published version.
    Exits 1 if any warning was reported.

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
        no_cache: Don't use or update the 7-day lookup cache.
        timeout: Timeout in seconds for each request.

    """
    if not math.isfinite(timeout) or timeout <= 0:
        print(
            "bibcheck: --timeout must be finite and positive.", file=sys.stderr
        )
        return 2
    try:
        text = bib.read_text()
        entries = parse_bib(text)
    except (BibCheckError, OSError, UnicodeDecodeError) as exc:
        print(f"bibcheck: {bib}: {exc}", file=sys.stderr)
        return 1
    if keys:
        wanted = {k.strip() for k in keys.split(",") if k.strip()}
        if unknown := wanted - {e.key for e in entries}:
            print(
                f"bibcheck: no such key(s): {', '.join(sorted(unknown))}",
                file=sys.stderr,
            )
            return 2
        entries = [e for e in entries if e.key in wanted]

    if case == "auto":
        sentence_case, why = detect(bib)
        print(f"Style: {why}", file=sys.stderr)
    else:
        sentence_case = case == "sentence"

    progress = _Progress()
    http = Http(
        timeout=timeout,
        cache_dir=None if no_cache else default_cache_dir(),
        on_wait=progress.detail,
    )
    sources = Sources(http)
    resolver = Resolver(sources)
    duplicates = _duplicates(entries)
    results = []
    counts = Counter()
    try:
        for n, entry in enumerate(entries, 1):
            progress.update(f"[{n}/{len(entries)}] {entry.key}")
            res = resolver.resolve(entry)
            issues = duplicates.get(entry.key, []) + check(
                entry, res, sentence_case=sentence_case
            )
            results.append((entry, res, issues))
            progress.clear()
            for error in res.errors:
                print(f"{entry.key}: {error}", file=sys.stderr)
            shown = [i for i in issues if notes or i.level != NOTE]
            if res.errors and res.primary is None:
                counts["unchecked"] += 1
            if any(i.kind == "missing" for i in issues):
                counts["missing"] += 1
            if any(i.level == WARN for i in issues):
                counts["warn"] += 1
            elif issues:
                counts["note"] += 1
            if shown:
                found = (
                    f"  ({res.primary.label}, via {res.via})"
                    if res.primary
                    else ""
                )
                print(f"{entry.key}{found}")
                for i in shown:
                    print(f"  {i.level:4}  {i.kind:7} {i.message}")
            sys.stdout.flush()
    except KeyboardInterrupt:
        progress.clear()
        print("bibcheck: cancelled.", file=sys.stderr)
        return 130

    summary = (
        f"Checked {len(entries)} entries: {counts['warn']} with warnings"
        + f", {counts['missing']} not found"
    )
    if counts["note"]:
        summary += f", {counts['note']} with notes only"
        if not notes:
            summary += " (--notes to show)"
    if counts["unchecked"]:
        summary += f", {counts['unchecked']} unchecked (source errors)"
    print(summary + ".")

    if fix:
        try:
            fixed, skipped, backup = fix_file(bib, text, results, sources)
        except OSError as exc:
            print(f"bibcheck: cannot write {bib}: {exc}", file=sys.stderr)
            return 1
        print(f"Fixed {len(fixed)}: {', '.join(fixed) or '-'}")
        for key, reason in skipped:
            print(f"Not fixed: {key} ({reason})")
        if backup:
            print(f"Backup: {backup}")
    return 1 if counts["warn"] or counts["unchecked"] else 0


def main(argv: list[str] | None = None) -> int:
    """Run the command and return its exit status."""
    return run(bibcheck, "bibcheck", argv)
