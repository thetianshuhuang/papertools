"""Tokenizing TeX and expanding a paper's own macros."""

import re
from dataclasses import dataclass
from typing import NamedTuple

from .errors import TexAbstractError

CS = "cs"
CHAR = "char"
SPACE = "space"
PAR = "par"
BEGIN = "begin"
END = "end"
PARAM = "param"


class Token(NamedTuple):
    """A TeX token.

    Attributes:
        kind: One of ``CS`` (a control sequence, named without its
            backslash), ``CHAR``, ``SPACE``, ``PAR`` (a blank line),
            ``BEGIN`` and ``END`` (braces), or ``PARAM`` (``#1``).
        text: The name, character, or parameter number.

    """

    kind: str
    text: str = ""


_TOKEN_RE = re.compile(
    r"\\([A-Za-z@]+)(\s*)"  # control word and the spaces it swallows
    r"|\\(.)"  # control symbol
    r"|(\s+)"
    r"|#(\d)"
    r"|(.)",
    re.S,
)


def tokenize(text: str) -> list[Token]:
    """Split TeX source (without comments) into tokens."""
    tokens = []
    for m in _TOKEN_RE.finditer(text):
        if m[1] is not None:
            tokens.append(Token(CS, m[1]))
            # TeX skips spaces and one newline after a control word, but a
            # blank line still ends the paragraph.
            if m[2].count("\n") >= 2:
                tokens.append(Token(PAR))
        elif m[3] is not None:
            # "\" followed by a newline or tab is a control space.
            name = " " if m[3].isspace() else m[3]
            tokens.append(Token(CS, name))
        elif m[4] is not None:
            tokens.append(Token(PAR if m[4].count("\n") >= 2 else SPACE, " "))
        elif m[5] is not None:
            tokens.append(Token(PARAM, m[5]))
        elif m[6] == "{":
            tokens.append(Token(BEGIN, "{"))
        elif m[6] == "}":
            tokens.append(Token(END, "}"))
        else:
            tokens.append(Token(CHAR, m[6]))
    return tokens


def detokenize(tokens: list[Token]) -> str:
    """Write tokens back out as TeX source."""
    out = []
    for i, tok in enumerate(tokens):
        if tok.kind == CS:
            out.append("\\" + tok.text)
            nxt = tokens[i + 1] if i + 1 < len(tokens) else None
            if (
                tok.text.isalpha()
                and nxt is not None
                and nxt.kind == CHAR
                and nxt.text.isalpha()
            ):
                out.append(" ")
        elif tok.kind == PARAM:
            out.append("#" + tok.text)
        elif tok.kind == PAR:
            out.append("\n\n")
        else:
            out.append(tok.text)
    return "".join(out)


_LBRACKET = Token(CHAR, "[")
_RBRACKET = Token(CHAR, "]")


class TokenStream:
    """Tokens read from the front, with room to push expansions back."""

    def __init__(self, tokens: list[Token]):
        self._stack = list(reversed(tokens))

    def __bool__(self) -> bool:
        """Whether any tokens are left."""
        return bool(self._stack)

    def peek(self) -> Token | None:
        """The next token, or None at the end."""
        return self._stack[-1] if self._stack else None

    def next(self) -> Token:
        """Remove and return the next token."""
        return self._stack.pop()

    def push(self, tokens: list[Token]) -> None:
        """Put ``tokens`` back in front of the stream."""
        self._stack.extend(reversed(tokens))

    def skip_spaces(self) -> None:
        """Drop space tokens."""
        while self._stack and self._stack[-1].kind == SPACE:
            self._stack.pop()

    def star(self) -> bool:
        """Consume a ``*`` if one comes next."""
        if self.peek() == Token(CHAR, "*"):
            self.next()
            return True
        return False

    def group(self) -> list[Token]:
        """Read up to the brace closing an already-consumed ``{``."""
        out = []
        depth = 0
        while self._stack:
            tok = self._stack.pop()
            if tok.kind == BEGIN:
                depth += 1
            elif tok.kind == END:
                if depth == 0:
                    return out
                depth -= 1
            out.append(tok)
        return out

    def arg(self) -> list[Token]:
        """Read an undelimited argument: a ``{...}`` group or one token."""
        self.skip_spaces()
        if not self._stack:
            return []
        tok = self._stack.pop()
        return self.group() if tok.kind == BEGIN else [tok]

    def optional(self) -> list[Token] | None:
        """Read a ``[...]`` argument, or return None if there isn't one.

        Spaces before a missing argument are kept.
        """
        i = len(self._stack) - 1
        while i >= 0 and self._stack[i].kind == SPACE:
            i -= 1
        if i < 0 or self._stack[i] != _LBRACKET:
            return None
        del self._stack[i:]
        out = []
        depth = 0
        while self._stack:
            tok = self._stack.pop()
            if tok.kind == BEGIN:
                depth += 1
            elif tok.kind == END:
                depth -= 1
            elif tok == _RBRACKET and depth == 0:
                return out
            out.append(tok)
        return out


@dataclass
class Macro:
    """A user-defined macro.

    Attributes:
        nargs: Number of arguments, including the optional one.
        body: Replacement text, with ``PARAM`` tokens for arguments.
        default: Default for the optional first argument, or None if the
            macro has no optional argument.

    """

    nargs: int
    body: list[Token]
    default: list[Token] | None = None


_NEWCOMMANDS = {
    "newcommand",
    "renewcommand",
    "providecommand",
    "DeclareRobustCommand",
}
_DEFS = {"def", "gdef", "edef", "xdef"}

# Handled by ``expand`` itself unless the paper defines them.
SPECIAL = {"xspace", "onedot"}

_BUILTIN_SOURCE = {
    "eg": r"e.g\onedot",
    "Eg": r"E.g\onedot",
    "ie": r"i.e\onedot",
    "Ie": r"I.e\onedot",
    "cf": r"cf\onedot",
    "Cf": r"Cf\onedot",
    "etc": r"etc\onedot",
    "vs": r"vs\onedot",
    "wrt": r"w.r.t\onedot",
    "dof": r"d.o.f\onedot",
    "etal": r"et al\onedot",
}


def builtins() -> dict[str, Macro]:
    r"""Abbreviation macros that conference styles define (``\eg``, ...).

    Styles define these with TeX conditionals that ``expand`` cannot
    evaluate, so they are replaced by plain definitions.
    """
    return {
        name: Macro(0, tokenize(body)) for name, body in _BUILTIN_SOURCE.items()
    }


def _newcommand(stream: TokenStream, kind: str, macros: dict) -> None:
    stream.star()
    names = [t for t in stream.arg() if t.kind == CS]
    if len(names) != 1:
        return
    count = stream.optional()
    text = "".join(t.text for t in count or []).strip()
    nargs = int(text) if text.isdigit() else 0
    default = stream.optional()
    body = stream.arg()
    if kind == "providecommand" and names[0].text in macros:
        return
    if not _internal(body):
        macros[names[0].text] = Macro(nargs, body, default)


def _def(stream: TokenStream, macros: dict) -> None:
    stream.skip_spaces()
    if not stream or stream.peek().kind != CS:
        return
    name = stream.next().text
    params = []
    while stream and stream.peek().kind != BEGIN:
        params.append(stream.next())
    if not stream:
        return
    stream.next()
    body = stream.group()
    # Delimited parameters (\def\a#1.{...}) are not supported.
    expected = [Token(PARAM, str(i + 1)) for i in range(len(params))]
    if params == expected and name != "csname" and not _internal(body):
        macros[name] = Macro(len(params), body)


def _internal(body: list[Token]) -> bool:
    r"""Whether a definition uses TeX internals that can't be evaluated.

    Such definitions are layout code, like ``\section`` in a style file, or
    use conditionals; expanding them would leak both branches into the text.
    """
    return any(
        t.kind == CS
        and (
            ("@" in t.text and t.text != "@")
            or t.text.startswith("if")
            or t.text in {"expandafter", "csname", "futurelet"}
        )
        for t in body
    )


def collect(tokens: list[Token], macros: dict[str, Macro]) -> None:
    r"""Add the macros defined in ``tokens`` to ``macros``, in order.

    Only definitions outside braces count, since those inside are local to
    their group.
    """
    stream = TokenStream(tokens)
    depth = 0
    while stream:
        tok = stream.next()
        if tok.kind == BEGIN:
            depth += 1
        elif tok.kind == END:
            depth = max(0, depth - 1)
        elif tok.kind == CS and depth == 0:
            if tok.text in _NEWCOMMANDS:
                _newcommand(stream, tok.text, macros)
            elif tok.text in _DEFS:
                _def(stream, macros)


def _substitute(body: list[Token], args: list[list[Token]]) -> list[Token]:
    out = []
    for tok in body:
        if tok.kind == PARAM and 0 < int(tok.text) <= len(args):
            out.extend(args[int(tok.text) - 1])
        else:
            out.append(tok)
    return out


# Tokens before which \xspace adds no space (from xspace.sty).
_NO_XSPACE_CHARS = set(",.'/?;:!~-)\"")
_NO_XSPACE_CS = {" ", "/", "footnote", "footnotemark", "xspace"}


def _xspace(nxt: Token | None) -> bool:
    if nxt is None or nxt.kind in {SPACE, PAR, BEGIN, END}:
        return False
    if nxt.kind == CHAR:
        return nxt.text not in _NO_XSPACE_CHARS
    return not (nxt.kind == CS and nxt.text in _NO_XSPACE_CS)


def expand(
    tokens: list[Token], macros: dict[str, Macro], limit: int = 10_000
) -> list[Token]:
    """Expand every macro in ``macros``, leaving other tokens alone."""
    stream = TokenStream(tokens)
    out = []
    count = 0
    while stream:
        tok = stream.next()
        if tok.kind != CS:
            out.append(tok)
        elif tok.text in macros:
            count += 1
            if count > limit:
                raise TexAbstractError(
                    f"expanding \\{tok.text} does not terminate"
                )
            macro = macros[tok.text]
            args = []
            if macro.default is not None:
                opt = stream.optional()
                args.append(macro.default if opt is None else opt)
            while len(args) < macro.nargs:
                args.append(stream.arg())
            stream.push(_substitute(macro.body, args))
        elif tok.text == "xspace":
            if _xspace(stream.peek()):
                out.append(Token(SPACE, " "))
        elif tok.text == "onedot":
            if stream.peek() != Token(CHAR, "."):
                out.append(Token(CHAR, "."))
            stream.push([Token(CS, "xspace")])
        else:
            out.append(tok)
    return out
