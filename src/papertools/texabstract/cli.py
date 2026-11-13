"""texabstract's console entry point."""

import sys
from pathlib import Path

from .._cli import run
from .convert import MathMode, extract
from .errors import TexAbstractError


def texabstract(source: Path, /, *, math: MathMode = "unicode") -> int:
    r"""Print the abstract of a LaTeX paper as plain text.

    The paper's own macros (\newcommand, \def) are expanded, footnotes are
    dropped, and accents, quotes, and dashes become Unicode.

    Args:
        source: Main .tex file, or a directory containing it.
        math: Write math as Unicode (2×), or as TeX ($2\times$).

    """
    try:
        result = extract(source, math=math)
    except TexAbstractError as exc:
        print(f"texabstract: {exc}", file=sys.stderr)
        return 1
    for warning in result.warnings:
        print(f"texabstract: warning: {warning}", file=sys.stderr)
    print(result.text)
    print(
        f"{len(result.text.split())} words, {len(result.text)} characters",
        file=sys.stderr,
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    """Run the command and return its exit status."""
    return run(texabstract, "pt texabstract", argv)
