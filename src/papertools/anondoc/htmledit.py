"""Edit HTML in place: bs4 selects the elements, edits splice the source.

Re-serializing a parsed page reorders attributes, lowercases SVG attributes,
and rewrites self-closing tags. To leave everything else byte-for-byte
intact, deletions and unwraps are applied to the original text, using spans
from a second pass of the same ``html.parser`` tokenizer that bs4 uses. If
a span does not line up with its element, the page is re-serialized instead.
"""

import re
import warnings
from html.parser import HTMLParser

from bs4 import BeautifulSoup, MarkupResemblesLocatorWarning, Tag

#: Elements that bs4's ``html.parser`` builder closes immediately.
VOID = frozenset(
    "area base basefont bgsound br col command embed frame hr image img "
    "input isindex keygen link menuitem meta nextid param source spacer "
    "track wbr".split()
)

#: (start-tag end, end-tag start, element end), keyed by element start.
Span = tuple[int, int, int]


def parse(text: str) -> BeautifulSoup:
    """Parse HTML (or a fragment) with the standard library parser."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", MarkupResemblesLocatorWarning)
        return BeautifulSoup(text, "html.parser")


class _Spans(HTMLParser):
    """Element spans, matching end tags the way bs4's builder does."""

    def __init__(self, text: str):
        super().__init__(convert_charrefs=False)
        self.text = text
        self.line_starts = [0] + [m.end() for m in re.finditer("\n", text)]
        self.spans: dict[int, Span] = {}
        self._open: list[tuple[str, int, int]] = []
        self.feed(text)
        self.close()
        for _, start, open_end in self._open:
            self.spans[start] = (open_end, len(text), len(text))

    def position(self, line: int, column: int) -> int:
        return self.line_starts[line - 1] + column

    def _start(self) -> tuple[int, int]:
        start = self.position(*self.getpos())
        return start, start + len(self.get_starttag_text() or "")

    def handle_starttag(self, tag, attrs):
        start, open_end = self._start()
        if tag in VOID:
            self.spans[start] = (open_end, open_end, open_end)
        else:
            self._open.append((tag, start, open_end))

    def handle_startendtag(self, tag, attrs):
        start, end = self._start()
        self.spans[start] = (end, end, end)

    def handle_endtag(self, tag):
        close_start = self.position(*self.getpos())
        gt = self.text.find(">", close_start)
        end = len(self.text) if gt < 0 else gt + 1
        names = [name for name, _, _ in self._open]
        if tag not in names:
            return
        index = len(names) - 1 - names[::-1].index(tag)
        for _, start, open_end in self._open[index + 1 :]:
            self.spans[start] = (open_end, close_start, close_start)
        _, start, open_end = self._open[index]
        self.spans[start] = (open_end, close_start, end)
        del self._open[index:]


class HtmlEditor:
    """Delete and unwrap elements of a page without re-serializing it.

    Args:
        text: The page or fragment.

    """

    def __init__(self, text: str):
        self.text = text
        self.soup = parse(text)
        self._spans: _Spans | None = None
        self._edits: list[tuple[int, int]] = []
        self._reserialize = False

    def _span(self, tag: Tag) -> tuple[int, Span] | None:
        if self._spans is None:
            self._spans = _Spans(self.text)
        if tag.sourceline is None or tag.sourcepos is None:
            return None
        start = self._spans.position(tag.sourceline, tag.sourcepos)
        span = self._spans.spans.get(start)
        name = re.escape(tag.name)
        if span is None or not re.match(
            rf"<{name}[\s/>]", self.text[start : span[0]], re.IGNORECASE
        ):
            return None
        _, close_start, end = span
        if close_start != end and not re.fullmatch(
            rf"</{name}\s*>", self.text[close_start:end], re.IGNORECASE
        ):
            return None
        return start, span

    def delete(self, tag: Tag) -> None:
        """Delete an element and everything in it."""
        found = None if self._reserialize else self._span(tag)
        if found is None:
            self._reserialize = True
        else:
            start, (_, _, end) = found
            self._edits.append((start, end))
        tag.decompose()

    def unwrap(self, tag: Tag) -> None:
        """Delete an element's tags, keeping its contents."""
        found = None if self._reserialize else self._span(tag)
        if found is None:
            self._reserialize = True
        else:
            start, (open_end, close_start, end) = found
            self._edits += [(start, open_end), (close_start, end)]
        tag.unwrap()

    def result(self) -> str:
        """The edited text."""
        if self._reserialize:
            return str(self.soup)
        out, cursor = [], 0
        for start, end in sorted(self._edits):
            if end <= cursor:
                continue  # Inside something already deleted.
            out.append(self.text[cursor : max(start, cursor)])
            cursor = end
        out.append(self.text[cursor:])
        return "".join(out)
