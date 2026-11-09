"""Converting a paper's abstract to plain text."""

import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from .._latex import ACCENTS, SYMBOLS
from .errors import TexAbstractError
from .macros import (
    BEGIN,
    CHAR,
    CS,
    END,
    PAR,
    PARAM,
    SPACE,
    SPECIAL,
    Macro,
    Token,
    TokenStream,
    builtins,
    collect,
    detokenize,
    expand,
    tokenize,
)
from .source import find_mains, load, read

MathMode = Literal["unicode", "tex"]

_BEGIN_RE = re.compile(r"\\begin\s*\{abstract\}")
_END_RE = re.compile(r"\\end\s*\{abstract\}")
_COMMAND_RE = re.compile(r"\\abstract(?![A-Za-z@])\s*\{")


def find_abstract(text: str) -> tuple[int, str] | None:
    r"""Find the abstract environment, or else an ``\abstract{...}`` command.

    Returns:
        The offset where the abstract starts and its body, or None.

    """
    if m := _BEGIN_RE.search(text):
        end = _END_RE.search(text, m.end())
        if end is None:
            raise TexAbstractError("\\begin{abstract} has no \\end{abstract}")
        return m.start(), text[m.end() : end.start()]
    if m := _COMMAND_RE.search(text):
        depth = 1
        i = m.end()
        while i < len(text) and depth:
            if text[i] == "\\":
                i += 1
            elif text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
            i += 1
        return m.start(), text[m.end() : i - 1]
    return None


# Argument signatures: s = optional star, o = optional argument, m = dropped
# argument, M = argument kept as text, U = argument kept verbatim (URLs).
_SIGNATURES = {
    # Dropped with their arguments.
    "footnote": "om",
    "footnotetext": "om",
    "footnotemark": "o",
    "thanks": "m",
    "label": "m",
    "index": "m",
    "nocite": "m",
    "vspace": "sm",
    "hspace": "sm",
    "color": "om",
    "pagecolor": "om",
    "phantom": "m",
    "hphantom": "m",
    "vphantom": "m",
    "includegraphics": "som",
    "setlength": "mm",
    "addtolength": "mm",
    "end": "m",
    # Formatting whose argument is kept.
    "textbf": "M",
    "textit": "M",
    "textsl": "M",
    "emph": "M",
    "texttt": "M",
    "textsc": "M",
    "textrm": "M",
    "textsf": "M",
    "textmd": "M",
    "textup": "M",
    "textnormal": "M",
    "textsuperscript": "M",
    "textsubscript": "M",
    "underline": "M",
    "uline": "M",
    "mbox": "M",
    "hbox": "M",
    "fbox": "M",
    "makebox": "ooM",
    "text": "M",
    "textcolor": "omM",
    "colorbox": "omM",
    "href": "mM",
    "hyperref": "oM",
    "url": "U",
    "nolinkurl": "U",
    "texorpdfstring": "Mm",
}
_DECLARATIONS = {
    *"tiny scriptsize footnotesize small normalsize large Large LARGE huge "
    "Huge bf it em sl sc tt rm sf bfseries mdseries itshape slshape scshape "
    "upshape ttfamily rmfamily sffamily normalfont centering raggedright "
    "raggedleft noindent indent maketitle null relax protect nobreak "
    "allowbreak smallskip medskip bigskip clearpage newpage unskip "
    "ignorespaces hfill vfill hfil vfil sloppy frenchspacing "
    "nonfrenchspacing makeatletter makeatother xspace".split(),
    "/",
    "-",
    "@",
    "!",
}
_SPACES = {
    " ",
    ",",
    ";",
    ":",
    ">",
    "quad",
    "qquad",
    "enspace",
    "enskip",
    "thinspace",
    "nobreakspace",
    "space",
    "item",
}
_LINEBREAKS = {"\\", "newline", "linebreak"}
_CITES = {
    "cite",
    "citep",
    "citet",
    "citealp",
    "citealt",
    "citeauthor",
    "citeyear",
    "citeyearpar",
    "autocite",
    "parencite",
    "textcite",
}
_REFS = {
    "ref",
    "cref",
    "Cref",
    "autoref",
    "eqref",
    "pageref",
    "nameref",
    "vref",
}
_TEXT_SYMBOLS = {
    **SYMBOLS,
    "&": "&",
    "%": "%",
    "$": "$",
    "#": "#",
    "_": "_",
    "{": "{",
    "}": "}",
    "ldots": "…",
    "dots": "…",
    "textellipsis": "…",
    "S": "§",
    "P": "¶",
    "textdegree": "°",
    "degree": "°",
    "textregistered": "®",
    "texttrademark": "™",
    "copyright": "©",
    "textcopyright": "©",
    "LaTeX": "LaTeX",
    "LaTeXe": "LaTeX2e",
    "TeX": "TeX",
    "BibTeX": "BibTeX",
    "textendash": "–",
    "textemdash": "—",
    "textasciitilde": "~",
    "textasciicircum": "^",
    "textbackslash": "\\",
    "textbar": "|",
    "textless": "<",
    "textgreater": ">",
    "textquoteleft": "‘",
    "textquoteright": "’",
    "textquotedblleft": "“",
    "textquotedblright": "”",
    "textbullet": "•",
    "dag": "†",
    "ddag": "‡",
    "pounds": "£",
    "euro": "€",
    "textmu": "µ",
    "texttimes": "×",
    "textpm": "±",
    "slash": "/",
}
_MATH_ENVS = {
    "math",
    "displaymath",
    "equation",
    "equation*",
    "align",
    "align*",
    "gather",
    "gather*",
    "multline",
    "multline*",
    "eqnarray",
    "eqnarray*",
}

# Math symbols and their spacing class: ord, op, bin, rel, open, close, punct.
_MATH_SYMBOLS: dict[str, tuple[str, str]] = {
    **{
        name: (char, "ord")
        for name, char in {
            "alpha": "α",
            "beta": "β",
            "gamma": "γ",
            "delta": "δ",
            "epsilon": "ε",
            "varepsilon": "ε",
            "zeta": "ζ",
            "eta": "η",
            "theta": "θ",
            "vartheta": "ϑ",
            "iota": "ι",
            "kappa": "κ",
            "lambda": "λ",
            "mu": "µ",
            "nu": "ν",
            "xi": "ξ",
            "pi": "π",
            "varpi": "ϖ",
            "rho": "ρ",
            "varrho": "ϱ",
            "sigma": "σ",
            "varsigma": "ς",
            "tau": "τ",
            "upsilon": "υ",
            "phi": "φ",
            "varphi": "φ",
            "chi": "χ",
            "psi": "ψ",
            "omega": "ω",
            "Gamma": "Γ",
            "Delta": "Δ",
            "Theta": "Θ",
            "Lambda": "Λ",
            "Xi": "Ξ",
            "Pi": "Π",
            "Sigma": "Σ",
            "Upsilon": "Υ",
            "Phi": "Φ",
            "Psi": "Ψ",
            "Omega": "Ω",
            "infty": "∞",
            "partial": "∂",
            "nabla": "∇",
            "forall": "∀",
            "exists": "∃",
            "emptyset": "∅",
            "varnothing": "∅",
            "prime": "′",
            "ell": "ℓ",
            "hbar": "ℏ",
            "angle": "∠",
            "neg": "¬",
            "lnot": "¬",
            "top": "⊤",
            "bot": "⊥",
            "degree": "°",
            "%": "%",
            "$": "$",
            "#": "#",
            "&": "&",
            "_": "_",
            "|": "‖",
            "ldots": "…",
            "dots": "…",
            "dotsc": "…",
            "cdots": "⋯",
            "dotsb": "⋯",
            "vdots": "⋮",
            "ddots": "⋱",
        }.items()
    },
    **{
        name: (char, "bin")
        for name, char in {
            "times": "×",
            "cdot": "·",
            "pm": "±",
            "mp": "∓",
            "div": "÷",
            "ast": "∗",
            "star": "⋆",
            "circ": "∘",
            "setminus": "∖",
            "cup": "∪",
            "cap": "∩",
            "wedge": "∧",
            "land": "∧",
            "vee": "∨",
            "lor": "∨",
            "oplus": "⊕",
            "otimes": "⊗",
            "odot": "⊙",
        }.items()
    },
    **{
        name: (char, "rel")
        for name, char in {
            "le": "≤",
            "leq": "≤",
            "ge": "≥",
            "geq": "≥",
            "ne": "≠",
            "neq": "≠",
            "approx": "≈",
            "sim": "~",
            "simeq": "≃",
            "cong": "≅",
            "equiv": "≡",
            "propto": "∝",
            "to": "→",
            "rightarrow": "→",
            "leftarrow": "←",
            "gets": "←",
            "Rightarrow": "⇒",
            "Leftarrow": "⇐",
            "leftrightarrow": "↔",
            "Leftrightarrow": "⇔",
            "iff": "⟺",
            "implies": "⟹",
            "mapsto": "↦",
            "in": "∈",
            "notin": "∉",
            "ni": "∋",
            "subset": "⊂",
            "subseteq": "⊆",
            "supset": "⊃",
            "supseteq": "⊇",
            "ll": "≪",
            "gg": "≫",
            "lesssim": "≲",
            "gtrsim": "≳",
            "perp": "⊥",
            "mid": "∣",
            "parallel": "∥",
        }.items()
    },
    "sum": ("∑", "op"),
    "prod": ("∏", "op"),
    "int": ("∫", "op"),
    "oint": ("∮", "op"),
    "langle": ("⟨", "open"),
    "lfloor": ("⌊", "open"),
    "lceil": ("⌈", "open"),
    "{": ("{", "open"),
    "rangle": ("⟩", "close"),
    "rfloor": ("⌋", "close"),
    "rceil": ("⌉", "close"),
    "}": ("}", "close"),
}
_MATH_FUNCTIONS = set(
    "arg cos cosh cot deg det dim exp gcd inf ker lim liminf limsup ln log "
    "max min Pr sin sinh sup tan tanh".split()
)
_MATH_FONTS = set(
    "mathrm mathbf mathit mathsf mathtt mathcal mathscr mathfrak mathbb "
    "boldsymbol bm operatorname mathnormal".split()
)
_MATH_TEXT = set(
    "text textrm textbf textit textsf texttt textnormal mbox hbox emph".split()
)
_MATH_SPACES = {",", ";", ":", ">", " ", "quad", "qquad", "enspace", "\\"}
_MATH_IGNORED = set(
    "! left right middle big Big bigg Bigg bigl bigr Bigl Bigr biggl biggr "
    "Biggl Biggr displaystyle textstyle scriptstyle scriptscriptstyle "
    "limits nolimits nonumber notag".split()
)
_MATH_ACCENTS = {
    "hat": "\u0302",
    "widehat": "\u0302",
    "bar": "\u0304",
    "overline": "\u0305",
    "tilde": "\u0303",
    "widetilde": "\u0303",
    "vec": "\u20d7",
    "dot": "\u0307",
    "ddot": "\u0308",
}
_BLACKBOARD = str.maketrans(
    {"R": "ℝ", "N": "ℕ", "Z": "ℤ", "Q": "ℚ", "C": "ℂ", "E": "𝔼", "P": "ℙ"}
)
_SUPERSCRIPTS = dict(zip("0123456789+-−=()niT", "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁻⁼⁽⁾ⁿⁱᵀ"))
_SUBSCRIPTS = dict(
    zip("0123456789+-−=()aeoxijkmnpst", "₀₁₂₃₄₅₆₇₈₉₊₋₋₌₍₎ₐₑₒₓᵢⱼₖₘₙₚₛₜ")
)
_MATH_CHARS = {
    **dict.fromkeys("+-*", "bin"),
    **dict.fromkeys("=<>:", "rel"),
    **dict.fromkeys("([", "open"),
    **dict.fromkeys(")]!", "close"),
    **dict.fromkeys(",;", "punct"),
}


def _script(inner: str, mark: str) -> str:
    if mark == "^" and inner in {"∘", "°"}:
        return "°"
    if mark == "^" and inner and set(inner) == {"′"}:
        return inner
    table = _SUPERSCRIPTS if mark == "^" else _SUBSCRIPTS
    if inner and all(c in table for c in inner):
        return "".join(table[c] for c in inner)
    return f"{mark}{inner}" if len(inner) == 1 else f"{mark}({inner})"


def _wrap(part: str) -> str:
    return part if re.fullmatch(r"[\w.′]*", part) else f"({part})"


def _join(atoms: list[tuple[str, str]]) -> str:
    """Join math atoms, spacing binary operators and relations as TeX does."""
    parts = []
    prev = None
    for i, (text, kind) in enumerate(atoms):
        if kind == "space":
            parts.append(" ")
            continue
        if kind == "bin" and prev in {None, "bin", "rel", "open", "punct"}:
            kind = "ord"  # unary, e.g. -3
        has_next = any(k != "space" for _, k in atoms[i + 1 :])
        if prev == "op" and kind in {"ord", "op", "open"}:
            parts.append(" ")
        if kind in {"bin", "rel"} and prev is not None and has_next:
            parts.append(f" {text} ")
        elif kind == "punct" and has_next:
            parts.append(f"{text} ")
        else:
            parts.append(text)
        prev = kind
    return " ".join("".join(parts).split())


@dataclass
class Renderer:
    """Converts expanded tokens to plain text.

    Attributes:
        math: Convert math to Unicode, or keep it as TeX.
        warnings: Problems found, each reported once.

    """

    math: MathMode = "unicode"
    warnings: list[str] = field(default_factory=list)

    def warn(self, message: str) -> None:
        """Record a warning unless it was already recorded."""
        if message not in self.warnings:
            self.warnings.append(message)

    def render(self, tokens: list[Token]) -> str:
        """Render tokens as paragraphs separated by blank lines."""
        paragraphs = self.text(tokens).split("\n\n")
        text = "\n\n".join(" ".join(p.split()) for p in paragraphs if p.strip())
        return unicodedata.normalize("NFC", text)

    def text(self, tokens: list[Token]) -> str:
        """Render tokens in text mode, without normalizing whitespace."""
        stream = TokenStream(tokens)
        out: list[str] = []
        while stream:
            tok = stream.next()
            if tok.kind == SPACE:
                out.append(" ")
            elif tok.kind == PAR:
                out.append("\n\n")
            elif tok.kind == PARAM:
                out.append("#" + tok.text)
            elif tok.kind == CHAR:
                self._char(tok.text, stream, out)
            elif tok.kind == CS:
                self._command(tok.text, stream, out)
        return "".join(out)

    def _char(self, char: str, stream: TokenStream, out: list[str]) -> None:
        if char == "$":
            display = stream.peek() == Token(CHAR, "$")
            if display:
                stream.next()
            body = _until(stream, lambda t: t == Token(CHAR, "$"))
            if display and stream.peek() == Token(CHAR, "$"):
                stream.next()
            out.append(self.math_text(body, display=display))
        elif char == "~":
            out.append(" ")
        elif char == "-":
            n = 1
            while n < 3 and stream.peek() == Token(CHAR, "-"):
                stream.next()
                n += 1
            out.append({1: "-", 2: "–", 3: "—"}[n])
        elif char in "`'":
            double = stream.peek() == Token(CHAR, char)
            if double:
                stream.next()
            quotes = {"`": ("‘", "“"), "'": ("’", "”")}[char]
            out.append(quotes[double])
        else:
            out.append(char)

    def _command(self, name: str, stream: TokenStream, out: list[str]) -> None:
        if name in ACCENTS:
            arg = stream.arg()
            if arg in ([Token(CS, "i")], [Token(CS, "j")]):
                base = arg[0].text
            else:
                base = self.text(arg)
            if base:
                out.append(base[0] + ACCENTS[name] + base[1:])
        elif name in _TEXT_SYMBOLS:
            out.append(_TEXT_SYMBOLS[name])
        elif name in _SIGNATURES:
            out.append(self._apply(_SIGNATURES[name], stream))
        elif name in _DECLARATIONS:
            pass
        elif name in _SPACES:
            if name == "item":
                stream.optional()
            out.append(" ")
        elif name in _LINEBREAKS:
            stream.star()
            stream.optional()
            out.append(" ")
        elif name == "par":
            out.append("\n\n")
        elif name in _CITES:
            stream.star()
            stream.optional()
            stream.optional()
            keys = detokenize(stream.arg())
            self.warn(f"dropped citation \\{name}{{{keys}}}")
            _rstrip(out)
        elif name in _REFS:
            stream.star()
            label = detokenize(stream.arg())
            self.warn(f"\\{name}{{{label}}} printed as ??")
            out.append("??")
        elif name == "enquote":
            out.append(f"“{self.text(stream.arg())}”")
        elif name == "ensuremath":
            out.append(self.math_text(stream.arg()))
        elif name in {"(", "["}:
            close = Token(CS, ")" if name == "(" else "]")
            body = _until(stream, lambda t: t == close)
            out.append(self.math_text(body, display=name == "["))
        elif name == "begin":
            env = detokenize(stream.arg()).strip()
            if env in _MATH_ENVS:
                body = _environment(stream, env)
                out.append(self.math_text(body, display=env != "math"))
        else:
            self.warn(f"unknown macro \\{name}; dropped")

    def _apply(self, signature: str, stream: TokenStream) -> str:
        parts = []
        for spec in signature:
            if spec == "s":
                stream.star()
            elif spec == "o":
                stream.optional()
            elif spec == "m":
                stream.arg()
            elif spec == "M":
                parts.append(self.text(stream.arg()))
            elif spec == "U":
                parts.append(detokenize(stream.arg()))
        return "".join(parts)

    def math_text(self, tokens: list[Token], *, display: bool = False) -> str:
        """Render math as Unicode, or as TeX between dollar signs."""
        if self.math == "tex":
            body = " ".join(detokenize(tokens).split())
            return f"$${body}$$" if display else f"${body}$"
        return self._math(tokens)

    def _math(self, tokens: list[Token]) -> str:
        stream = TokenStream(tokens)
        atoms: list[tuple[str, str]] = []
        while stream:
            tok = stream.next()
            if tok.kind in {SPACE, PAR, BEGIN, END, PARAM}:
                continue
            if tok.kind == CHAR and tok.text in "^_":
                script = _script(self._math(stream.arg()), tok.text)
                if atoms and atoms[-1][1] != "space":
                    atoms[-1] = (atoms[-1][0] + script, atoms[-1][1])
                else:
                    atoms.append((script, "ord"))
            elif tok.kind == CHAR:
                if tok.text == "~":
                    atoms.append((" ", "space"))
                elif tok.text == "'":
                    atoms.append(("′", "ord"))
                elif tok.text != "&":
                    atoms.append((tok.text, _MATH_CHARS.get(tok.text, "ord")))
            elif atom := self._math_command(tok.text, stream):
                atoms.append(atom)
        return _join(atoms)

    def _math_command(
        self, name: str, stream: TokenStream
    ) -> tuple[str, str] | None:
        if name in _MATH_SYMBOLS:
            return _MATH_SYMBOLS[name]
        if name in _MATH_FUNCTIONS:
            return name, "op"
        if name in _MATH_SPACES:
            return " ", "space"
        if name in _MATH_IGNORED:
            return None
        if name in {"frac", "dfrac", "tfrac"}:
            num = self._math(stream.arg())
            den = self._math(stream.arg())
            return f"{_wrap(num)}/{_wrap(den)}", "ord"
        if name == "sqrt":
            stream.optional()
            return "√" + _wrap(self._math(stream.arg())), "ord"
        if name in _MATH_FONTS:
            inner = self._math(stream.arg())
            if name == "mathbb":
                inner = inner.translate(_BLACKBOARD)
            return inner, "op" if name == "operatorname" else "ord"
        if name in _MATH_TEXT:
            return self.text(stream.arg()), "ord"
        if name in _MATH_ACCENTS:
            inner = self._math(stream.arg())
            if len(inner) == 1:
                inner += _MATH_ACCENTS[name]
            return inner, "ord"
        if name == "label":
            stream.arg()
            return None
        self.warn(f"unknown macro \\{name} in math; dropped")
        return None


def _until(stream: TokenStream, stop) -> list[Token]:
    out = []
    while stream:
        tok = stream.next()
        if stop(tok):
            break
        out.append(tok)
    return out


def _environment(stream: TokenStream, env: str) -> list[Token]:
    r"""Read up to ``\end{env}``."""
    body = []
    while stream:
        tok = stream.next()
        if tok == Token(CS, "end"):
            arg = stream.arg()
            if detokenize(arg).strip() == env:
                break
            body += [tok, Token(BEGIN, "{"), *arg, Token(END, "}")]
        else:
            body.append(tok)
    return body


def _rstrip(out: list[str]) -> None:
    """Remove trailing spaces, e.g. the ``~`` before a dropped citation."""
    while out and not out[-1].strip(" "):
        out.pop()
    if out:
        out[-1] = out[-1].rstrip(" ")


@dataclass
class Abstract:
    """An abstract converted to plain text.

    Attributes:
        text: The abstract.
        main: The main file it was read from.
        warnings: Problems found along the way.

    """

    text: str
    main: Path
    warnings: list[str]


def definitions(prefix: str, styles: list[Path]) -> dict[str, Macro]:
    """Macros in effect at the end of ``prefix``.

    Definitions in the paper override the builtins, which override local
    style files.
    """
    macros: dict[str, Macro] = {}
    for sty in styles:
        collect(tokenize(read(sty)), macros)
    for name in SPECIAL:
        macros.pop(name, None)
    macros.update(builtins())
    collect(tokenize(prefix), macros)
    return macros


def extract(source: Path, *, math: MathMode = "unicode") -> Abstract:
    """Read a paper and convert its abstract to plain text.

    Args:
        source: The main .tex file, or a directory containing it.
        math: Convert math to Unicode, or keep it as TeX.

    """
    papers = [(main, load(main)) for main in find_mains(source)]
    found = [
        (main, doc, abstract)
        for main, doc in papers
        if (abstract := find_abstract(doc.text)) is not None
    ]
    if not found and len(papers) == 1:
        main, doc = papers[0]
        message = f"no abstract found in {main}"
        if doc.missing:
            message += f" (could not find {', '.join(doc.missing)})"
        raise TexAbstractError(message)
    if len(found) != 1:
        names = ", ".join(m.name for m, *_ in found or papers)
        problem = "several papers have" if found else "none has"
        raise TexAbstractError(
            f"{problem} an abstract in {source} ({names}); pass one of them"
        )
    main, doc, (start, body) = found[0]
    macros = definitions(doc.text[:start], doc.styles)
    renderer = Renderer(math)
    text = renderer.render(expand(tokenize(body), macros))
    return Abstract(
        text, main, doc.warnings + swallowed(body, macros) + renderer.warnings
    )


def swallowed(body: str, macros: dict[str, Macro]) -> list[str]:
    r"""Warn about macros followed by a space that TeX swallows.

    ``\name is`` prints as "Nameis"; the paper probably meant ``\name\ is``.
    """
    first: dict[str, str] = {}
    # Spaces don't matter in math.
    body = re.sub(r"(?<!\\)\$[^$]*\$|\\\(.*?\\\)", " ", body, flags=re.S)
    for m in re.finditer(r"\\([A-Za-z@]+)\s+(?=[\w(])", body):
        macro = macros.get(m[1])
        if (
            macro is not None
            and macro.nargs == 0
            and Token(CS, "xspace") not in macro.body
            and "\n\n" not in m[0]
        ):
            first.setdefault(m[1], re.match(r"\(?\w*", body[m.end() :])[0])
    return [
        f"\\{name} swallows the space before '{word}'; "
        f"write \\{name}\\ or \\{name}{{}} to keep it"
        for name, word in first.items()
    ]
