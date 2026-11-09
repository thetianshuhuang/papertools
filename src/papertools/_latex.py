"""LaTeX accent and special-letter tables shared by papertools commands."""

import re

ACCENTS = {
    "'": "\u0301",
    "`": "\u0300",
    '"': "\u0308",
    "^": "\u0302",
    "~": "\u0303",
    "=": "\u0304",
    ".": "\u0307",
    "u": "\u0306",
    "v": "\u030c",
    "H": "\u030b",
    "c": "\u0327",
    "k": "\u0328",
    "r": "\u030a",
}
"""LaTeX accent commands and their Unicode combining characters."""

SYMBOLS = {
    "ss": "ß",
    "o": "ø",
    "O": "Ø",
    "l": "ł",
    "L": "Ł",
    "aa": "å",
    "AA": "Å",
    "ae": "æ",
    "AE": "Æ",
    "oe": "œ",
    "OE": "Œ",
    "i": "ı",
    "j": "ȷ",
}
"""LaTeX commands for special letters."""

_ACCENT_RE = re.compile(
    r"\\([" + re.escape("'`\"^~=.") + r"]|[uvHckr](?![A-Za-z]))"
    r"\s*(?:\{\s*(\\[ij]|\w)\s*\}|(\\[ij](?![A-Za-z])|\w))"
)
_SYMBOL_RE = re.compile(r"\\(ss|aa|AA|ae|AE|oe|OE|[oOlLij])(?![A-Za-z])\s*")


def latex_accents(text: str) -> str:
    r"""Convert LaTeX accents (``\'e``) and letters (``\o``) to Unicode.

    Braces and whitespace elsewhere are left alone. Accents become combining
    characters, so NFC-normalize the result to compose them.
    """

    def accent(match: re.Match[str]) -> str:
        base = match[2] or match[3]
        if base in {"\\i", "\\j"}:
            base = base[1]
        return base + ACCENTS[match[1]]

    text = _ACCENT_RE.sub(accent, text)
    return _SYMBOL_RE.sub(lambda m: SYMBOLS[m[1]], text)
