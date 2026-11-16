"""Rebuild flagged entries from authoritative BibTeX and rewrite the file."""

import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from .bibtex import Entry, format_entry, parse_entry
from .errors import BibCheckError
from .issues import DEFAULT_MAX_AUTHORS, WARN, Issue
from .matching import doubtful_match, known_doi
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
    if res.published and not res.published.hint_only:
        return res.published
    if res.primary and not res.primary.hint_only and not res.hint:
        return res.primary
    return None


def _repair_names(authors: list[str], references: list[Record]) -> list[str]:
    """Take a reference's spelling where the record lost accents or case.

    Repairs IEEE's dropped accents (``Novak`` -> ``Novák``) and
    OpenReview's all-caps surnames (``SMITH`` -> ``Smith``).
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
    key: str,
    record: Record,
    raw: str,
    references: list[Record],
    max_authors: int = DEFAULT_MAX_AUTHORS,
) -> str:
    """Normalize a fetched BibTeX record into an entry with ``key``.

    The edits are mechanical: keep the ``KEEP`` fields, ``--`` in pages,
    straight apostrophes in names, accents restored from references, every
    author (or, past ``max_authors``, the first ``max_authors`` and
    ``others``), arXiv ``@misc`` records turned into arXiv-preprint
    ``@article`` entries, and mixed-case title words braced.
    """
    fetched = parse_entry(raw)
    etype, f = fetched.type, dict(fetched.fields)
    if not f.get("doi") and (
        m := re.match(r"https?://(?:dx\.)?doi\.org/(.+)", f.get("url", ""))
    ):
        f["doi"] = m[1]
    if not f.get("doi") and record.doi:
        f["doi"] = record.doi
    if f.get("journal", "").lower() == "corr" or record.source == "arxiv":
        etype = "article"
        f["journal"] = f"arXiv preprint arXiv:{record.id}"
        for name in ("publisher", "volume", "number", "pages"):
            f.pop(name, None)
    if "author" in f:
        authors = re.split(r"\s+and\s+", f["author"].replace("’", "'"))
        authors = _repair_names(authors, references)
        if authors[-1].strip().lower() != "others" and (
            0 < max_authors < len(authors)
        ):
            authors = authors[:max_authors] + ["others"]
        f["author"] = " and ".join(authors)
    if "pages" in f:
        f["pages"] = norm_pages(f["pages"])
    if "title" in f:
        f["title"] = protect(f["title"])
    return format_entry(etype, key, {k: f[k] for k in KEEP if k in f})


# --- Editing one field in place -------------------------------------------


def _value_end(text: str, pos: int) -> int:
    """End of the field value starting at ``text[pos]``."""
    if text[pos] == "{":
        depth = 0
        for i in range(pos, len(text)):
            depth += {"{": 1, "}": -1}.get(text[i], 0)
            if depth == 0:
                return i + 1
        raise BibCheckError("unbalanced braces")
    if text[pos] == '"':
        end = text.find('"', pos + 1)
        if end < 0:
            raise BibCheckError("unterminated quote")
        return end + 1
    m = re.compile(r"[^,}\s)]*").match(text, pos)
    return m.end()


def set_field(entry_text: str, name: str, value: str) -> str:
    """Set field ``name`` of one entry's source text to ``{value}``.

    An existing field's value is replaced; otherwise the field is added
    last, in the style of the entry's other fields. Every other byte stays.
    """
    header = entry_text.index(",") + 1  # Past "@type{key,".
    field_re = re.compile(rf"(?<![\w-]){name}\s*=\s*", re.I)
    if m := field_re.search(entry_text, header):
        end = _value_end(entry_text, m.end())
        return entry_text[: m.end()] + f"{{{value}}}" + entry_text[end:]
    close = entry_text.rstrip()[-1]  # "}" or ")"
    body = entry_text.rstrip()[:-1].rstrip()
    indent = re.search(r"\n([ \t]+)[\w-]+\s*=", entry_text)
    equals = re.search(r"[\w-]+(\s*=\s*)[{\"\w]", entry_text[header:])
    sep = f"\n{indent[1]}" if indent else " "
    eq = equals[1] if equals else "="
    if not body.endswith(","):
        body += ","
    end = "\n" if "\n" in entry_text else " "
    return f"{body}{sep}{name}{eq}{{{value}}}{end}{close}"


def _escaped(url: str) -> list[str]:
    r"""Ways ``url`` may be written in a .bib (``_`` as ``\_``, ...)."""
    tex = url.replace("_", "\\_").replace("%", "\\%").replace("#", "\\#")
    return [url, tex] if tex != url else [url]


def url_edit(entry: Entry, res: Resolution) -> tuple[str, str] | str:
    """Write a suggested URL or DOI, whose page showed the title, in.

    Returns:
        (new entry text, description of the change), or the reason it
        can't be done.

    """
    link = res.link
    suggestion = link.suggestion if link else None
    if suggestion is None:
        return ""
    if suggestion.status != "ok":
        return (
            f"the suggested URL's page couldn't be checked ({suggestion.url});"
            " open it and "
            "add it by hand"
        )
    if suggestion.doi:
        return (
            set_field(entry.text, "doi", suggestion.doi),
            f"added doi={{{suggestion.doi}}} ({suggestion.how})",
        )
    given = link.given
    if given is None:
        return (
            set_field(entry.text, "url", suggestion.url),
            f"added url={{{suggestion.url}}} ({suggestion.how})",
        )
    what = f"replaced {given.url} with {suggestion.url} ({suggestion.how})"
    if given.url in _escaped(entry.fields.get("url", "").strip()):
        return set_field(entry.text, "url", suggestion.url), what
    # The link sits elsewhere, e.g. \url{...} in howpublished.
    for written in _escaped(given.url):
        if entry.text.count(written) == 1:
            new = entry.text.replace(written, _escaped(suggestion.url)[-1])
            return new, what
    return (
        f"couldn't find where {given.url} is written; replace it with "
        f"{suggestion.url} by hand"
    )


# --- Fixing a file -----------------------------------------------------------


@dataclass
class Fix:
    """What ``--fix`` did, or didn't do, to one entry.

    Attributes:
        entry: The entry as written.
        kind: ``rebuilt`` (from its record), ``doi`` (a DOI added), ``url``
            (a suggested link written in), or ``skipped``.
        change: What changed, or why it was skipped.
        new: The replacement text ("" when skipped).

    """

    entry: Entry
    kind: str
    change: str
    new: str = ""

    @property
    def applied(self) -> bool:
        return self.kind != "skipped"


def _skip(entry: Entry, reason: str) -> Fix:
    return Fix(entry, "skipped", reason)


def fix_entry(
    entry: Entry,
    res: Resolution,
    issues: list[Issue],
    sources: Sources,
    max_authors: int = DEFAULT_MAX_AUTHORS,
) -> Fix | None:
    """What to do to one entry; None if it needs nothing.

    Entries with warnings and a record are rebuilt from its BibTeX; other
    matched entries get the record's DOI if they lack one. Entries no index
    has get their suggested link (or DOI) written in, if its page showed
    the title.
    """
    if res.primary is None and res.link and res.link.suggestion:
        edit = url_edit(entry, res)
        if isinstance(edit, tuple):
            return Fix(entry, "url", edit[1], edit[0])
        return _skip(entry, edit) if edit else None
    if not any(i.level == WARN and i.kind.rebuild_fixes for i in issues):
        if doi := known_doi(entry, res):
            where = res.primary.info.name
            what = (
                f"added doi={{{doi}}} (from the {where} record, matched by "
                f"{res.via})"
            )
            return Fix(entry, "doi", what, set_field(entry.text, "doi", doi))
        return None
    if res.primary is None:
        return _skip(entry, "no matching record")
    record = replacement_record(res)
    if record is None:
        where = f" ({res.hint.venue})" if res.hint else ""
        return _skip(
            entry,
            f"no BibTeX source for the published version{where}; copy it "
            "from the publisher's page",
        )
    if doubt := doubtful_match(entry, res, record):
        return _skip(entry, doubt)
    try:
        raw = record_bibtex(sources, record)
        if raw is None:
            raise BibCheckError(f"{record.source} has no BibTeX")
        new = build_entry(entry.key, record, raw, res.references, max_authors)
    except (BibCheckError, SourceError) as exc:
        return _skip(entry, f"could not fetch BibTeX: {exc}")
    if new == entry.text:
        return None
    return Fix(entry, "rebuilt", f"rebuilt from {record.label}", new)


def splice(text: str, fixes: list[Fix]) -> str:
    """``text`` with each applied fix's entry replaced, other bytes kept."""
    applied = [f for f in fixes if f.applied]
    for f in sorted(applied, key=lambda f: f.entry.start, reverse=True):
        text = text[: f.entry.start] + f.new + text[f.entry.end :]
    return text


def apply_fixes(
    text: str,
    results: list[tuple[Entry, Resolution, list[Issue]]],
    sources: Sources,
    max_authors: int = DEFAULT_MAX_AUTHORS,
) -> tuple[str, list[Fix]]:
    """Fix every entry that needs it; return (new text, fixes)."""
    fixes = [
        f
        for entry, res, issues in results
        if (f := fix_entry(entry, res, issues, sources, max_authors))
    ]
    return splice(text, fixes), fixes


def write_fixed(path: Path, new_text: str) -> Path:
    """Write ``new_text`` to ``path``, after copying it to ``path.bak``."""
    backup = path.with_name(path.name + ".bak")
    shutil.copyfile(path, backup)
    path.write_text(new_text)
    return backup
