"""supp-split's console entry point."""

import sys
from pathlib import Path
from typing import Annotated

import tyro

from .._cli import run
from .errors import SuppSplitError, UsageError
from .split import split


def _pages(first: int, last: int) -> str:
    return f"page {first}" if first == last else f"pages {first}–{last}"


def supp_split(
    source: Path,
    /,
    *,
    page: Annotated[int | None, tyro.conf.arg(metavar="N")] = None,
    main: Annotated[Path | None, tyro.conf.arg(metavar="PDF")] = None,
    supp: Annotated[Path | None, tyro.conf.arg(metavar="PDF")] = None,
) -> int:
    r"""Split a paper PDF into its main text and supplementary material.

    The supplement starts at the first section after LaTeX's \appendix
    (found from hyperref's anchors) or else at a "Supplementary Material" or
    "Appendix" heading. It must start on a new page. Existing output files
    are replaced.

    Args:
        source: Compiled paper PDF.
        page: One-based page where the supplement starts, instead of finding
            it.
        main: Output PDF for the main text; default SOURCE-main.pdf.
        supp: Output PDF for the supplement; default SOURCE-supp.pdf.

    """
    main = main or source.with_name(f"{source.stem}-main.pdf")
    supp = supp or source.with_name(f"{source.stem}-supp.pdf")
    resolved = [p.resolve() for p in (source, main, supp)]
    if len(set(resolved)) < 3:
        print(
            "supp-split: the source and the two outputs must be different "
            "files.",
            file=sys.stderr,
        )
        return 2
    try:
        result = split(source, main, supp, page=page)
    except UsageError as exc:
        print(f"supp-split: {exc}", file=sys.stderr)
        return 2
    except (SuppSplitError, OSError) as exc:
        print(f"supp-split: {exc}", file=sys.stderr)
        return 1
    for warning in result.warnings:
        print(f"supp-split: warning: {warning}", file=sys.stderr)
    first = result.start.page + 1
    print(f"Supplement starts on page {first} ({result.start.reason}).")
    print(f"Wrote {main} ({_pages(1, first - 1)})")
    print(f"Wrote {supp} ({_pages(first, result.page_count)})")
    return 0


def main(argv: list[str] | None = None) -> int:
    """Run the command and return its exit status."""
    return run(supp_split, "pt supp-split", argv)
