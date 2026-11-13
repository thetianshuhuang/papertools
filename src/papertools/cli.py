"""The ``pt`` command, which dispatches to each tool's own command line."""

import difflib
import importlib
import sys

from . import __version__

# Subcommand name -> (module with a ``main(argv)``, one-line description).
COMMANDS: dict[str, tuple[str, str]] = {
    "bibcheck": (
        "papertools.bibcheck.cli",
        "Check a .bib file against Crossref, arXiv, OpenReview, and DBLP.",
    ),
    "gs2pdf": (
        "papertools.gs2pdf.cli",
        "Download Google Slides diagrams as transparent, cropped PDFs.",
    ),
    "anondoc": (
        "papertools.anondoc.cli",
        "Anonymize a documentation site for double-blind review.",
    ),
    "texabstract": (
        "papertools.texabstract.cli",
        "Print a LaTeX paper's abstract as plain text.",
    ),
    "overleaf-push": (
        "papertools.overleaf_push.cli",
        "Push to Overleaf's git bridge, retrying while coauthors edit.",
    ),
    "figspace": (
        "papertools.figspace.cli",
        "Estimate how much of a paper its figures and tables take up.",
    ),
}


def _usage() -> str:
    width = max(len(name) for name in COMMANDS)
    lines = ["usage: pt <command> [args...]", "", "Commands:"]
    lines += [
        f"  {name:<{width}}  {description}"
        for name, (_, description) in COMMANDS.items()
    ]
    lines += ["", 'Run "pt <command> --help" for a command\'s options.']
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """Run a papertools command and return its exit status."""
    args = sys.argv[1:] if argv is None else argv
    if not args:
        print(_usage(), file=sys.stderr)
        return 2
    name, rest = args[0], args[1:]
    if name in ("-h", "--help"):
        print(_usage())
        return 0
    if name == "--version":
        print(f"pt {__version__}")
        return 0
    if name not in COMMANDS:
        message = f"pt: unknown command {name!r}"
        close = difflib.get_close_matches(name, COMMANDS, n=1)
        if close:
            message += f"; did you mean {close[0]!r}?"
        print(f"{message}\n\n{_usage()}", file=sys.stderr)
        return 2
    module = importlib.import_module(COMMANDS[name][0])
    return module.main(rest)
