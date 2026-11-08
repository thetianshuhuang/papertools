"""Work out whether a paper's bibliography style sentence-cases titles."""

import re
import shutil
import subprocess
from pathlib import Path

_STYLE_RE = re.compile(r"^[^%\n]*\\bibliographystyle\{([^}]+)\}", re.M)
_BIBLATEX_RE = re.compile(
    r"^[^%\n]*\\usepackage(\[[^\]]*\])?\{biblatex\}", re.M
)
# The title formatting of plain.bst and its descendants:
#   FUNCTION {format.title} { title empty$ ... { title "t" change.case$ } }
_SENTENCE_RE = re.compile(r'\btitle\s+"t"\s+change\.case\$')


def find_style(bib: Path) -> tuple[str, Path | None] | None:
    r"""Find the ``\bibliographystyle`` used by .tex files near ``bib``.

    Returns:
        The style name and its .bst path (None if the file can't be
        found), or ``("biblatex", None)``, or None if nothing names a style.

    """
    root = bib.resolve().parent
    tex_files = sorted(root.glob("*.tex")) + sorted(root.glob("*/*.tex"))
    for tex in tex_files:
        try:
            text = tex.read_text(errors="replace")
        except OSError:
            continue
        if m := _STYLE_RE.search(text):
            name = m[1].strip().removesuffix(".bst")
            return name, _locate(name, root)
        if _BIBLATEX_RE.search(text):
            return "biblatex", None
    return None


def _locate(name: str, root: Path) -> Path | None:
    local = root / f"{name}.bst"
    if local.exists():
        return local
    if kpsewhich := shutil.which("kpsewhich"):
        try:
            out = subprocess.run(
                [kpsewhich, f"{name}.bst"],
                capture_output=True,
                text=True,
                timeout=10,
            ).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return None
        if out:
            return Path(out)
    return None


def sentence_cases(bst: Path) -> bool:
    """Whether a .bst lowercases titles (everything not in braces)."""
    text = bst.read_text(errors="replace")
    code = "\n".join(line.split("%", 1)[0] for line in text.splitlines())
    return bool(_SENTENCE_RE.search(code))


def detect(bib: Path) -> tuple[bool, str]:
    """Return (sentence_case, explanation) for the paper that uses ``bib``."""
    found = find_style(bib)
    if found is None:
        return False, "no \\bibliographystyle found next to the .bib"
    name, bst = found
    if name == "biblatex":
        return False, "biblatex (keeps title case)"
    if bst is None:
        return False, f"{name}.bst not found"
    try:
        lowered = sentence_cases(bst)
    except OSError:
        return False, f"cannot read {bst}"
    return (
        lowered,
        f"{name}.bst ({'sentence-cases' if lowered else 'keeps'} titles)",
    )
