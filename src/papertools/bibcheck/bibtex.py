"""A small brace-aware BibTeX reader that keeps exact source spans."""

import bisect
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
        problems: Things BibTeX would warn about while reading it: a field
            given twice, an undefined ``@String`` macro.

    """

    type: str
    key: str
    fields: dict[str, str] = field(default_factory=dict)
    start: int = 0
    end: int = 0
    text: str = ""
    problems: list[str] = field(default_factory=list)


class BibSyntaxError(BibCheckError):
    """A .bib syntax error, located and explained.

    Attributes:
        line: 1-based line of the error.
        column: 1-based column.
        key: The entry it's in, if known.
        field: The field it's in, if known.
        message: What's wrong.
        hint: How to fix it, if there's a likely fix.
        excerpt: The source line, and a caret under the column.

    """

    def __init__(
        self,
        text: str,
        pos: int,
        message: str,
        *,
        hint: str = "",
        key: str = "",
        field: str = "",
        line: int | None = None,
    ):
        pos = max(0, min(pos, len(text)))
        start = text.rfind("\n", 0, pos) + 1
        end = text.find("\n", pos)
        source = text[start : end if end >= 0 else len(text)]
        self.line = line or text.count("\n", 0, pos) + 1
        self.column = pos - start + 1
        self.key, self.field = key, field
        self.message, self.hint = message, hint
        caret = " " * len(source[: pos - start].expandtabs()) + "^"
        self.excerpt = f"{source.expandtabs()}\n{caret}"
        super().__init__(str(self))

    def __str__(self) -> str:
        """Where, what, the source line with a caret, and the hint."""
        where = f"line {self.line}, column {self.column}"
        if self.key:
            where += f", entry {self.key}"
        if self.field:
            where += f", field {self.field}"
        out = f"{where}: {self.message}\n" + _indent(self.excerpt)
        if self.hint:
            out += f"\n  hint: {self.hint}"
        return out

    def as_dict(self) -> dict:
        """The error's parts, for JSON."""
        return {
            "line": self.line,
            "column": self.column,
            "key": self.key or None,
            "field": self.field or None,
            "message": self.message,
            "hint": self.hint,
            "excerpt": self.excerpt,
        }


def _indent(text: str) -> str:
    return "\n".join(f"    {line}" for line in text.splitlines())


# A line inside a value that looks like the next field or entry: a sign
# the value's braces don't balance and it ran on.
_RUNAWAY_RE = re.compile(
    r"\n[ \t]*(@[A-Za-z]+[ \t]*[{(]|[A-Za-z][\w-]*[ \t]*=[ \t]*[{\"\w])"
)
_ENTRY_START_RE = re.compile(r"^[ \t]*@[ \t]*[A-Za-z]+[ \t]*[{(]", re.M)


class _Reader:
    def __init__(self, text: str):
        self.text = text
        self.macros = dict(_MONTHS)
        # Offsets of newlines, for line numbers in O(log n): a file full of
        # errors must not cost a scan from the top for each.
        self._newlines = [m.start() for m in re.finditer("\n", text)]
        self.key = ""  # Where we are, for error messages.
        self.field = ""
        self.undefined: list[str] = []

    def fail(self, pos: int, message: str, hint: str = "") -> BibSyntaxError:
        return BibSyntaxError(
            self.text,
            pos,
            message,
            hint=hint,
            key=self.key,
            field=self.field,
            line=self._line_of(pos),
        )

    def _line_of(self, pos: int) -> int:
        return bisect.bisect_left(self._newlines, pos) + 1

    def _runs_into_entry(self, i: int) -> bool:
        """Whether the newline at ``i`` starts a new entry's line.

        A value can't contain one; stopping there keeps reading linear (a
        brace that never closes doesn't make every later entry rescan the
        rest of the file).
        """
        return self.text[i] == "\n" and bool(
            _ENTRY_START_RE.match(self.text, i + 1)
        )

    def _unclosed(self, pos: int, what: str, hint: str, end: int):
        if end < len(self.text):
            line = self._line_of(end + 1)
            return self.fail(
                pos,
                f"{what} is not closed before the next entry (line {line})",
                hint,
            )
        return self.fail(pos, f"{what} is never closed", hint)

    def braced(self, pos: int) -> tuple[str, int]:
        """Read ``{...}`` starting at ``pos``; return (inner text, end)."""
        depth = 0
        for i in range(pos, len(self.text)):
            c = self.text[i]
            if c == "\n" and self._runs_into_entry(i):
                raise self._unclosed(pos, "this '{'", "add the missing '}'", i)
            if c == "\\":
                continue
            if c == "{" and self.text[i - 1] != "\\":
                depth += 1
            elif c == "}" and self.text[i - 1] != "\\":
                depth -= 1
                if depth == 0:
                    return self.text[pos + 1 : i], i + 1
        raise self._unclosed(
            pos,
            "this '{'",
            "add the missing '}' (the file ends before the braces balance)",
            len(self.text),
        )

    def quoted(self, pos: int) -> tuple[str, int]:
        """Read ``"..."`` (quotes inside braces don't count)."""
        depth = 0
        for i in range(pos + 1, len(self.text)):
            c = self.text[i]
            if c == "\n" and self._runs_into_entry(i):
                raise self._unclosed(
                    pos, "this quoted value", "add the closing '\"'", i
                )
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
            elif c == '"' and depth == 0 and self.text[i - 1] != "\\":
                return self.text[pos + 1 : i], i + 1
        raise self._unclosed(
            pos,
            "this quoted value",
            "add the closing '\"' (quotes inside {braces} don't count)",
            len(self.text),
        )

    def value(self, pos: int) -> tuple[str, int]:
        """Read a ``#``-concatenated value and resolve macros."""
        parts = []
        while True:
            pos = self.skip_ws(pos)
            c = self.text[pos : pos + 1]
            if c == "{":
                start = pos
                part, pos = self.braced(pos)
                self._check_runaway(start, part)
            elif c == '"':
                part, pos = self.quoted(pos)
            else:
                m = _NAME_RE.match(self.text, pos)
                if not m:
                    found = self.text[pos : pos + 1] or "the end of the file"
                    raise self.fail(
                        pos,
                        f"expected a value, found {found!r}",
                        'write the value in {braces}, "quotes", or as a '
                        "number or @string name",
                    )
                word, pos = m[1], m.end()
                if word.isdigit():
                    part = word
                else:
                    # BibTeX only warns about undefined macros; doi.org
                    # serves bare ones like month=June.
                    if word.lower() not in self.macros:
                        self.undefined.append(word)
                    part = self.macros.get(word.lower(), word)
            parts.append(part)
            pos = self.skip_ws(pos)
            if self.text[pos : pos + 1] != "#":
                return "".join(parts), pos
            pos += 1

    def _check_runaway(self, start: int, value: str) -> None:
        """Catch a value that swallowed the next field or entry."""
        m = _RUNAWAY_RE.search(value)
        if not m:
            return
        first_line_end = self.text.find("\n", start)
        line = self.text[start:first_line_end]
        opened, closed = line.count("{"), line.count("}")
        swallowed = m[1].strip()
        what = (
            "the next entry"
            if swallowed.startswith("@")
            else f"the next field ({swallowed.split('=')[0].strip()} =)"
        )
        raise self.fail(
            start,
            f"the braces of this value don't balance: it runs on into "
            f"{what} on line {self._line_of(start + 1 + m.start(1))}",
            f"this line opens {opened} '{{' and closes {closed} '}}'; add "
            f"the missing '}}', or remove the extra '{{'"
            if opened != closed
            else "a '}' is missing in this value",
        )

    def skip_ws(self, pos: int) -> int:
        while pos < len(self.text) and self.text[pos].isspace():
            pos += 1
        return pos

    def fields(
        self, pos: int, close: str, problems: list[str] | None = None
    ) -> tuple[dict[str, str], int]:
        out: dict[str, str] = {}
        while True:
            pos = self.skip_ws(pos)
            if self.text[pos : pos + 1] == close:
                return out, pos + 1
            m = _NAME_RE.match(self.text, pos)
            if not m or self.text[m.end() : m.end() + 1] != "=":
                raise self._expected_field(pos, m, close)
            name = m[1].lower()
            self.field = name
            val, pos = self.value(m.end() + 1)
            if name in out and problems is not None:
                problems.append(
                    f"field {name} is given twice; BibTeX uses the first, "
                    "biber stops with an error: remove one"
                )
            out.setdefault(name, val)
            pos = self.skip_ws(pos)
            if self.text[pos : pos + 1] == ",":
                pos += 1
            elif self.text[pos : pos + 1] != close:
                raise self._expected_comma(pos, name, close)
            self.field = ""

    def _expected_field(self, pos: int, m, close: str) -> BibSyntaxError:
        if pos >= len(self.text):
            return self.fail(
                pos,
                "the file ends inside this entry",
                f"close the entry with '{close}'",
            )
        if self.text[pos] == "@":
            return self.fail(
                pos,
                "a new entry starts before the previous one is closed",
                f"add '{close}' at the end of the previous entry",
            )
        if m and self.text[m.end() : m.end() + 1] in {"{", '"'}:
            return self.fail(
                m.end(),
                f"'=' is missing after the field name {m[1]!r}",
                f"write {m[1]} = {{...}}",
            )
        found = self.text[pos : pos + 20].split("\n")[0]
        return self.fail(
            pos,
            f"expected a field (name = value), found {found!r}",
            "fields are written name = {value}, separated by commas",
        )

    def _expected_comma(self, pos: int, name: str, close: str):
        if pos >= len(self.text):
            return self.fail(
                pos,
                "the file ends inside this entry",
                f"close the entry with '{close}'",
            )
        nxt = _NAME_RE.match(self.text, pos)
        if nxt and self.text[nxt.end() : nxt.end() + 1] == "=":
            # Point at the end of the previous value, where the comma goes.
            end = pos
            while end > 0 and self.text[end - 1].isspace():
                end -= 1
            self.field = name
            return self.fail(
                end,
                f"a comma is missing after the {name} field",
                f"add ',' here, before {nxt[1]} =",
            )
        if self.text[pos : pos + 1] == "@":
            return self.fail(
                pos,
                "a new entry starts before this one is closed",
                f"add '{close}' to close the entry",
            )
        found = self.text[pos : pos + 20].split("\n")[0]
        found = found or "the end of the file"
        return self.fail(
            pos,
            f"expected ',' or '{close}' after the {name} value, found "
            f"{found!r}",
            "if this is part of the value, put the whole value in {braces}",
        )

    def read(
        self, errors: list[BibSyntaxError] | None = None
    ) -> tuple[list[Entry], dict[str, str]]:
        """Read every entry.

        With ``errors``, a broken entry is recorded there and skipped,
        resuming at the next line that starts an entry; without, the first
        error is raised.
        """
        entries = []
        pos = 0
        while m := _AT_RE.search(self.text, pos):
            self.key = self.field = ""
            try:
                pos = self._read_one(m, entries)
            except BibSyntaxError as exc:
                if errors is None:
                    raise
                errors.append(exc)
                nxt = _ENTRY_START_RE.search(self.text, m.end())
                pos = nxt.start() if nxt else len(self.text)
        return entries, self.macros

    def _read_one(self, m: re.Match, entries: list[Entry]) -> int:
        kind, open_ = m[1].lower(), m[2]
        close = "}" if open_ == "{" else ")"
        if kind == "comment":
            # Skip the whole block, as biber does (JabRef stores
            # metadata in @Comment{...}).
            if open_ == "{":
                return self.braced(m.end() - 1)[1]
            return self.text.find(")", m.end()) + 1 or len(self.text)
        if kind == "preamble":
            _, pos = self.value(m.end())
            return self.skip_ws(pos) + 1
        if kind == "string":
            defs, pos = self.fields(m.end(), close)
            self.macros.update(defs)
            return pos
        k = _KEY_RE.match(self.text, m.end())
        if not k:
            raise self.fail(
                m.end(),
                f"cannot read the key of this @{m[1]}",
                f"entries start with @{m[1]}{{key, and the key can't "
                "contain spaces, braces, or commas",
            )
        self.key = k[1]
        self.undefined = []
        problems: list[str] = []
        fields, end = self.fields(k.end(), close, problems)
        problems += [
            f"@string {w!r} is not defined, so the word {w!r} is used as "
            "the value; define it with @string or write {" + w + "}"
            for w in dict.fromkeys(self.undefined)
        ]
        entries.append(
            Entry(
                type=kind,
                key=k[1],
                fields={k: " ".join(v.split()) for k, v in fields.items()},
                start=m.start(),
                end=end,
                text=self.text[m.start() : end],
                problems=problems,
            )
        )
        return end


def parse_bib(text: str) -> list[Entry]:
    """Parse every entry in a .bib file, resolving ``@String`` macros.

    Raises:
        BibSyntaxError: At the first syntax error.

    """
    return _Reader(text).read()[0]


def parse_bib_lenient(text: str) -> tuple[list[Entry], list[BibSyntaxError]]:
    """Parse what can be parsed; broken entries are skipped and reported."""
    errors: list[BibSyntaxError] = []
    entries = _Reader(text).read(errors)[0]
    return entries, errors


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
