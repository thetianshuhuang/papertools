r"""Reading a paper's sources: the main file, ``\input`` files, and styles."""

import re
from dataclasses import dataclass, field
from pathlib import Path

from .errors import TexAbstractError

_DOCCLASS_RE = re.compile(r"^[^%\n]*\\documentclass(?![A-Za-z@])", re.M)
_INPUT_RE = re.compile(
    r"\\(input|include|subfile)(?![A-Za-z@])\s*"
    r"(?:\{([^{}]*)\}|([^\s{}\\%]+))"
)
_PACKAGE_RE = re.compile(
    r"\\(?:usepackage|RequirePackage)(?![A-Za-z@])\s*"
    r"(?:\[[^\]]*\])?\s*\{([^{}]*)\}"
)
# An unescaped %, the rest of its line, and the next line's indentation.
_COMMENT_RE = re.compile(r"(?<!\\)((?:\\\\)*)%[^\n]*(?:\n[ \t]*)?")


@dataclass
class Document:
    r"""A paper's text with every ``\input`` inlined and comments removed.

    Attributes:
        text: The document text.
        styles: Style files next to the main file that it loads.
        warnings: Problems found while reading, e.g. missing inputs.
        missing: Names of inputs that could not be found.

    """

    text: str
    styles: list[Path] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)


def strip_comments(text: str) -> str:
    r"""Remove ``%`` comments as TeX does, keeping escaped ``\%``."""
    return _COMMENT_RE.sub(r"\1", text)


def read(path: Path) -> str:
    """Read a TeX file with normalized newlines and no comments."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise TexAbstractError(
            f"cannot read {path}: {exc.strerror or exc}"
        ) from exc
    return strip_comments(text.replace("\r\n", "\n"))


def find_mains(path: Path) -> list[Path]:
    r"""Return ``[path]``, or the files in it with a ``\documentclass``."""
    if not path.is_dir():
        return [path]
    mains = [
        tex
        for tex in sorted(path.glob("*.tex"))
        if _DOCCLASS_RE.search(tex.read_text(errors="replace"))
    ]
    if not mains:
        raise TexAbstractError(f"no .tex file with \\documentclass in {path}")
    return mains


def _resolve(root: Path, name: str) -> Path | None:
    candidates = [root / name]
    if not name.endswith(".tex"):
        candidates.insert(0, root / f"{name}.tex")
    for path in candidates:
        if path.is_file():
            return path.resolve()
    return None


def load(main: Path) -> Document:
    r"""Read ``main``, inlining ``\input``, ``\include``, and ``\subfile``.

    Inputs are resolved relative to the main file's directory, as LaTeX does
    when run from there.
    """
    root = main.parent
    doc = Document("")

    def inline(path: Path, stack: frozenset[Path]) -> str:
        def replace(match: re.Match[str]) -> str:
            name = (match[2] if match[2] is not None else match[3]).strip()
            # Inputs inside macro definitions, e.g. \input{figs/#1}.
            if not name or "#" in name or "\\" in name:
                return match[0]
            target = _resolve(root, name)
            if target is None:
                doc.warnings.append(f"cannot find \\{match[1]}{{{name}}}")
                doc.missing.append(name)
                return ""
            if target in stack:
                doc.warnings.append(f"{name} includes itself; skipped")
                return ""
            return inline(target, stack | {target})

        return _INPUT_RE.sub(replace, read(path))

    resolved = main.resolve()
    doc.text = inline(resolved, frozenset({resolved}))
    for match in _PACKAGE_RE.finditer(doc.text):
        for name in match[1].split(","):
            sty = root / f"{name.strip()}.sty"
            if name.strip() and sty.is_file() and sty not in doc.styles:
                doc.styles.append(sty)
    return doc
