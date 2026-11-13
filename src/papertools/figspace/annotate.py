"""Draw the measured regions onto a copy of the paper, for checking."""

from pathlib import Path

import pymupdf

from .errors import FigSpaceError
from .measure import Report

_COLORS = {"figure": (0.1, 0.4, 0.9), "table": (0.9, 0.5, 0.0)}
_GRAY = (0.5, 0.5, 0.5)
_RED = (0.85, 0.1, 0.1)


def annotate(source: Path, report: Report, destination: Path) -> None:
    """Write ``source`` to ``destination`` with ``report``'s regions drawn.

    Counted parts of the text area are outlined in gray, figures are shaded
    blue and tables orange, and the end of the main body is a red line.
    """
    if destination.resolve() == source.resolve():
        raise FigSpaceError("--annotate would overwrite the input PDF.")
    with pymupdf.open(source) as document:
        for index, regions in report.counted.items():
            page = document[index]
            for rect in regions:
                page.draw_rect(rect, color=_GRAY, width=0.5, dashes="[3] 0")
            for item in report.floats:
                if item.page != index:
                    continue
                color = _COLORS[item.kind]
                page.draw_rect(
                    item.region,
                    color=color,
                    fill=color,
                    fill_opacity=0.15,
                    width=1,
                )
                page.insert_text(
                    (item.region.x0 + 2, item.region.y0 + 8),
                    f"{item.label}: {item.area:.2f} pages",
                    fontsize=7,
                    color=color,
                )
        stop = report.stop
        if stop is not None and stop.page in report.counted:
            span = report.layout.span(stop.rect)
            y = stop.rect.y0
            document[stop.page].draw_line(
                (span.x0, y), (span.x1, y), color=_RED, width=1.5
            )
        document.save(destination, garbage=3, deflate=True)
