"""A small brace-aware BibTeX reader that keeps exact source spans."""

import re
from dataclasses import dataclass, field

from .errors import BibCheckError

_MONTHS = {
    m: m.capitalize()
    for m in "jan feb mar apr may jun jul aug sep oct nov dec".split()
}
_AT_RE = re.compile(r"@\s*([A-Za-z]+)\s*([{(])")
_NAME_RE = re.compile(r"\s*([^\s=,{}()\"#]+)\s*")
_KEY_RE = re.compile(r"\s*([^\s,{}()]*)\s*,")


@dataclass
class Entry:
    """One BibTeX entry with macros resolved, plus where it sits in the file.

    Attributes:
        type: Lowercase entry type, e.g. ``inproceedings``.
        key: Citation key.
        fields: Lowercase field name -> value, braces kept, whitespace
            collapsed, ``@String`` macros and ``#`` concatenation resolved.
        start: Offset of the ``@`` in the source text.
        end: Offset just past the closing delimiter.
        text: The entry's exact source text.

    """

    type: str
    key: str
    fields: dict[str, str] = field(default_factory=dict)
    start: int = 0
    end: int = 0
    text: str = ""


class _Reader:
    def __init__(self, text: str):
        self.text = text
        self.macros = dict(_MONTHS)

    def fail(self, pos: int, message: str) -> BibCheckError:
        line = self.text.count("\n", 0, pos) + 1
        return BibCheckError(f"line {line}: {message}")

    def braced(self, pos: int) -> tuple[str, int]:
        """Read ``{...}`` starting at ``pos``; return (inner text, end)."""
        depth = 0
        for i in range(pos, len(self.text)):
            c = self.text[i]
            if c == "\\":
                continue
            if c == "{" and self.text[i - 1] != "\\":
                depth += 1
            elif c == "}" and self.text[i - 1] != "\\":
                depth -= 1
                if depth == 0:
                    return self.text[pos + 1 : i], i + 1
        raise self.fail(pos, "unbalanced braces")

    def quoted(self, pos: int) -> tuple[str, int]:
        """Read ``"..."`` (quotes inside braces don't count)."""
        depth = 0
        for i in range(pos + 1, len(self.text)):
            c = self.text[i]
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
            elif c == '"' and depth == 0 and self.text[i - 1] != "\\":
                return self.text[pos + 1 : i], i + 1
        raise self.fail(pos, "unterminated quoted value")

    def value(self, pos: int) -> tuple[str, int]:
        """Read a ``#``-concatenated value and resolve macros."""
        parts = []
        while True:
            pos = self.skip_ws(pos)
            c = self.text[pos : pos + 1]
            if c == "{":
                part, pos = self.braced(pos)
            elif c == '"':
                part, pos = self.quoted(pos)
            else:
                m = _NAME_RE.match(self.text, pos)
                if not m:
                    raise self.fail(pos, "expected a field value")
                word, pos = m[1], m.end()
                if word.isdigit():
                    part = word
                else:
                    # BibTeX only warns about undefined macros; doi.org
                    # serves bare ones like month=June.
                    part = self.macros.get(word.lower(), word)
            parts.append(part)
            pos = self.skip_ws(pos)
            if self.text[pos : pos + 1] != "#":
                return "".join(parts), pos
            pos += 1

    def skip_ws(self, pos: int) -> int:
        while pos < len(self.text) and self.text[pos].isspace():
            pos += 1
        return pos

    def fields(self, pos: int, close: str) -> tuple[dict[str, str], int]:
        out: dict[str, str] = {}
        while True:
            pos = self.skip_ws(pos)
            if self.text[pos : pos + 1] == close:
                return out, pos + 1
            m = _NAME_RE.match(self.text, pos)
            if not m or self.text[m.end() : m.end() + 1] != "=":
                raise self.fail(pos, "expected 'field = value'")
            name = m[1].lower()
            val, pos = self.value(m.end() + 1)
            out[name] = val
            pos = self.skip_ws(pos)
            if self.text[pos : pos + 1] == ",":
                pos += 1
            elif self.text[pos : pos + 1] != close:
                raise self.fail(pos, f"expected ',' or '{close}'")

    def read(self) -> tuple[list[Entry], dict[str, str]]:
        entries = []
        pos = 0
        while m := _AT_RE.search(self.text, pos):
            kind, open_ = m[1].lower(), m[2]
            close = "}" if open_ == "{" else ")"
            if kind == "comment":
                # Skip the whole block, as biber does (JabRef stores
                # metadata in @Comment{...}).
                if open_ == "{":
                    _, pos = self.braced(m.end() - 1)
                else:
                    pos = self.text.find(")", m.end()) + 1 or len(self.text)
                continue
            if kind == "preamble":
                _, pos = self.value(m.end())
                pos = self.skip_ws(pos) + 1
                continue
            if kind == "string":
                defs, pos = self.fields(m.end(), close)
                self.macros.update(defs)
                continue
            k = _KEY_RE.match(self.text, m.end())
            if not k:
                raise self.fail(m.start(), f"cannot read key of @{m[1]}")
            fields, end = self.fields(k.end(), close)
            entries.append(
                Entry(
                    type=kind,
                    key=k[1],
                    fields={k: " ".join(v.split()) for k, v in fields.items()},
                    start=m.start(),
                    end=end,
                    text=self.text[m.start() : end],
                )
            )
            pos = end
        return entries, self.macros


def parse_bib(text: str) -> list[Entry]:
    """Parse every entry in a .bib file, resolving ``@String`` macros."""
    return _Reader(text).read()[0]


def parse_entry(text: str) -> Entry:
    """Parse a single entry, e.g. a record served by a BibTeX endpoint."""
    entries = parse_bib(text)
    if len(entries) != 1:
        raise BibCheckError(f"expected one BibTeX entry, got {len(entries)}")
    return entries[0]


def format_entry(type_: str, key: str, fields: dict[str, str]) -> str:
    """Write an entry in the ``  name={value},`` style."""
    body = ",\n".join(f"  {k}={{{v}}}" for k, v in fields.items())
    return f"@{type_}{{{key},\n{body}\n}}"
