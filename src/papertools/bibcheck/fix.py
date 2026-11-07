"""Rebuild flagged entries from authoritative BibTeX and rewrite the file."""

import re
import shutil
from pathlib import Path

from .bibtex import Entry, format_entry, parse_entry
from .checks import WARN, Issue
from .errors import BibCheckError
from .resolve import Resolution
from .sources import Record, SourceError, Sources, record_bibtex
from .text import (
    count_accents,
    delatex,
    first_last,
    name_key,
    norm_pages,
    protect,
    shouting,
)

# Fields worth keeping from a fetched record, in output order.
KEEP = [
    "title",
    "author",
    "booktitle",
    "journal",
    "volume",
    "number",
    "pages",
    "year",
    "publisher",
    "doi",
]


def replacement_record(res: Resolution) -> Record | None:
    """The record a fixed entry is rebuilt from: published if known."""
    if res.published and res.published.source != "s2":
        return res.published
    if res.primary and res.primary.source != "s2" and not res.hint:
        return res.primary
    return None


def _repair_names(authors: list[str], references: list[Record]) -> list[str]:
    """Take a reference's spelling where the record lost accents or case.

    Repairs IEEE's dropped accents (``Dollar`` -> ``Dollár``) and
    OpenReview's all-caps surnames (``HAZIZA`` -> ``Haziza``).
    """
    best: dict[str, str] = {}
    for r in references:
        for name in r.authors:
            key = name_key(name)
            if shouting(name):
                continue
            if key not in best or count_accents(name) > count_accents(
                best[key]
            ):
                best[key] = name
    out = []
    for name in authors:
        plain = delatex(name)
        better = best.get(name_key(first_last(plain)))
        if better and (
            count_accents(better) > count_accents(plain) or shouting(plain)
        ):
            out.append(_same_form(plain, better))
        else:
            out.append(name)
    return out


def _same_form(original: str, better: str) -> str:
    """Write ``better`` (First Last) in ``original``'s ``Last, First`` form."""
    if "," not in original:
        return better
    last = original.split(",")[0].split()
    words = better.split()
    if len(words) <= len(last):
        return better
    new_last, first = words[-len(last) :], words[: -len(last)]
    if name_key(" ".join(new_last)) != name_key(" ".join(last)):
        return better
    return f"{' '.join(new_last)}, {' '.join(first)}"


def build_entry(
    key: str, record: Record, raw: str, references: list[Record]
) -> str:
    """Normalize a fetched BibTeX record into an entry with ``key``.

    The edits are mechanical: keep the ``KEEP`` fields, ``--`` in pages,
    straight apostrophes in names, accents restored from references, arXiv
    ``@misc`` records turned into arXiv-preprint ``@article`` entries, and
    mixed-case title words braced.
    """
    fetched = parse_entry(raw)
    etype, f = fetched.type, dict(fetched.fields)
    if not f.get("doi") and (
        m := re.match(r"https?://(?:dx\.)?doi\.org/(.+)", f.get("url", ""))
    ):
        f["doi"] = m[1]
    if f.get("journal", "").lower() == "corr" or record.source == "arxiv":
        etype = "article"
        f["journal"] = f"arXiv preprint arXiv:{record.id}"
        for name in ("publisher", "volume", "number", "pages"):
            f.pop(name, None)
    if "author" in f:
        authors = re.split(r"\s+and\s+", f["author"].replace("’", "'"))
        f["author"] = " and ".join(_repair_names(authors, references))
    if "pages" in f:
        f["pages"] = norm_pages(f["pages"])
    if "title" in f:
        f["title"] = protect(f["title"])
    return format_entry(etype, key, {k: f[k] for k in KEEP if k in f})


def fix_file(
    path: Path,
    text: str,
    results: list[tuple[Entry, Resolution, list[Issue]]],
    sources: Sources,
) -> tuple[list[str], list[tuple[str, str]], Path | None]:
    """Rewrite flagged entries in place, after copying the file to ``.bak``.

    Returns:
        The fixed keys, (key, reason) pairs for flagged entries that were
        left alone, and the backup path (None if nothing changed).

    """
    fixed, skipped, edits = [], [], []
    for entry, res, issues in results:
        if not any(i.level == WARN for i in issues):
            continue
        record = replacement_record(res)
        if res.primary is None:
            skipped.append((entry.key, "no matching record"))
            continue
        if record is None:
            where = f" ({res.hint.where})" if res.hint else ""
            skipped.append(
                (
                    entry.key,
                    f"no BibTeX source for the published version{where}; "
                    "copy it from the publisher's page",
                )
            )
            continue
        try:
            raw = record_bibtex(sources, record)
            if raw is None:
                raise BibCheckError(f"{record.source} has no BibTeX")
            new = build_entry(entry.key, record, raw, res.references)
        except (BibCheckError, SourceError) as exc:
            skipped.append((entry.key, f"could not fetch BibTeX: {exc}"))
            continue
        if new != entry.text:
            edits.append((entry, new))
            fixed.append(entry.key)
    if not edits:
        return fixed, skipped, None
    for entry, new in sorted(edits, key=lambda e: e[0].start, reverse=True):
        text = text[: entry.start] + new + text[entry.end :]
    backup = path.with_name(path.name + ".bak")
    shutil.copyfile(path, backup)
    path.write_text(text)
    return fixed, skipped, backup
