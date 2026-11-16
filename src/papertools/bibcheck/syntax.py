"""Problems an entry would cause in BibTeX or LaTeX, found without lookups.

- ``syntax``: what BibTeX warns about while reading (a field given twice,
  an undefined ``@string``), and characters that break LaTeX when the
  bibliography is typeset: unescaped ``&``, ``%``, ``#``, and ``_`` or ``^``
  outside math, and unbalanced ``$``.
- ``fields``: fields BibTeX requires for the entry type that are missing or
  empty, which it warns about and leaves out of the reference.
"""

import re

from .bibtex import Entry

# BibTeX's standard types and their required fields; a tuple means "one of".
REQUIRED: dict[str, list[str | tuple[str, ...]]] = {
    "article": ["author", "title", "journal", "year"],
    "book": [("author", "editor"), "title", "publisher", "year"],
    "booklet": ["title"],
    "conference": ["author", "title", "booktitle", "year"],
    "inbook": [
        ("author", "editor"),
        "title",
        ("chapter", "pages"),
        "publisher",
        "year",
    ],
    "incollection": ["author", "title", "booktitle", "publisher", "year"],
    "inproceedings": ["author", "title", "booktitle", "year"],
    "manual": ["title"],
    "mastersthesis": ["author", "title", "school", "year"],
    "phdthesis": ["author", "title", "school", "year"],
    "proceedings": ["title", "year"],
    "techreport": ["author", "title", "institution", "year"],
    "unpublished": ["author", "title", "note"],
}

# Fields typeset as text (URLs and identifiers are typeset verbatim).
_TEXT_FIELDS = {
    "title",
    "booktitle",
    "journal",
    "author",
    "editor",
    "publisher",
    "institution",
    "school",
    "organization",
    "series",
    "address",
    "note",
    "howpublished",
    "chapter",
    "edition",
    "type",
}
# Commands whose argument is a URL or verbatim text, and math.
_VERBATIM_RE = re.compile(
    r"\\(?:url|path|nolinkurl)\s*\{[^{}]*\}|\\href\s*\{[^{}]*\}"
)
_MATH_RE = re.compile(r"(?<!\\)\$(?:[^$\\]|\\.)*(?<!\\)\$")
_SPECIALS = {
    "&": "write \\& (a bare & breaks LaTeX: 'Misplaced alignment tab')",
    "%": "write \\% (a bare % comments out the rest of the line)",
    "#": "write \\# (a bare # breaks LaTeX)",
    "_": "write \\_ (outside math, a bare _ breaks LaTeX)",
    "^": "write \\^{} or use math (outside math, a bare ^ breaks LaTeX)",
}


def unescaped(value: str) -> list[str]:
    """Advice for each special character used unescaped in ``value``."""
    text = _VERBATIM_RE.sub("", value)
    if text.count("$") - text.count("\\$") - 2 * len(_MATH_RE.findall(text)):
        return ["unbalanced $: math must open and close with $"]
    text = _MATH_RE.sub("", text)
    out = []
    for char, advice in _SPECIALS.items():
        if re.search(rf"(?<!\\){re.escape(char)}", text):
            out.append(f"unescaped {char}: {advice}")
    return out


def lint(entry: Entry) -> list[tuple[str, str]]:
    """(kind, message) for each problem, without any lookups."""
    out = [("syntax", p) for p in entry.problems]
    for name, value in entry.fields.items():
        if name in _TEXT_FIELDS:
            out += [("syntax", f"{name}: {m}") for m in unescaped(value)]
    missing = []
    for need in REQUIRED.get(entry.type, []):
        names = need if isinstance(need, tuple) else (need,)
        if not any(entry.fields.get(n, "").strip("{} ") for n in names):
            missing.append(" or ".join(names))
    if missing:
        out.append(
            (
                "fields",
                f"@{entry.type} requires {', '.join(missing)}; BibTeX leaves "
                "it out of the reference and warns",
            )
        )
    return out
