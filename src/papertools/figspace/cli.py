"""figspace's console entry point."""

import sys
from pathlib import Path
from typing import Annotated

import tyro

from .._cli import run
from .annotate import annotate as write_annotated
from .errors import FigSpaceError
from .measure import Report, measure


def _summary(report: Report) -> str:
    counted = sorted(report.counted)
    first, last = counted[0] + 1, counted[-1] + 1
    stop = report.stop
    lines = []
    if report.floats:
        lines.append("page  float       width  pages")
        for item in report.floats:
            width = "full" if item.full_width else "column"
            lines.append(
                f"{item.page + 1:>4}  {item.label:<10}  {width:<6} "
                f"{item.area:>5.2f}"
            )
        lines.append("")
    else:
        lines.append("No figures or tables found.")
    partial = 0.0
    if stop is not None and stop.page + 1 == last:
        partial = report.body_pages - (last - first)
        last -= 1
        reason = f'stopped at "{stop.text}" on page {stop.page + 1}'
    elif last < report.page_count:
        reason = "stopped at --page-limit"
    else:
        reason = "to the end of the PDF"
    parts = []
    if last >= first:
        parts.append(
            f"pages {first}–{last}" if last > first else f"page {first}"
        )
    if partial >= 0.005:
        parts.append(f"{partial:.2f} of page {last + 1}")
    where = " and ".join(parts)
    body = report.body_pages
    lines.append(f"Body:    {body:.2f} pages ({where}; {reason})")
    for name, kind in (
        ("Figures", "figure"),
        ("Tables", "table"),
        ("Total", None),
    ):
        used = report.pages_used(kind)
        share = used / body if body else 0.0
        lines.append(
            f"{name + ':':<8} {used:.2f} pages ({share:.1%} of the body)"
        )
    return "\n".join(lines)


def figspace(
    source: Path,
    /,
    *,
    page_limit: Annotated[int | None, tyro.conf.arg(metavar="N")] = None,
    annotate: Annotated[Path | None, tyro.conf.arg(metavar="PDF")] = None,
) -> int:
    """Estimate how much of a paper's main body its figures and tables use.

    Each figure and table is measured from its graphics through its caption,
    across the full width of its column (or of the page, for floats that
    span both columns). Space is reported in pages of the text area inside
    the margins. Counting stops at the References or LLM Usage Statement
    heading, or after --page-limit pages, whichever comes first.

    Args:
        source: Compiled paper PDF.
        page_limit: Count at most this many pages.
        annotate: Write a copy of the PDF with the measured regions shaded.

    """
    if page_limit is not None and page_limit < 1:
        print("figspace: --page-limit must be at least 1.", file=sys.stderr)
        return 2
    try:
        report = measure(source, page_limit=page_limit)
        if annotate is not None:
            write_annotated(source, report, annotate)
    except (FigSpaceError, OSError) as exc:
        print(f"figspace: {exc}", file=sys.stderr)
        return 1
    for warning in report.warnings:
        print(f"figspace: warning: {warning}", file=sys.stderr)
    print(_summary(report))
    if annotate is not None:
        print(f"Wrote {annotate}", file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    """Run the command and return its exit status."""
    return run(figspace, "pt figspace", argv)
