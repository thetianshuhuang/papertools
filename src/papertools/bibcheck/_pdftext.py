"""Read a PDF's title and first pages, in a separate, limited process.

Run as ``python -I -m papertools.bibcheck._pdftext`` with the PDF on stdin;
prints JSON ``{"heads": [...], "text": "..."}``. Link checks fetch PDFs
from anywhere, and MuPDF is C code with a history of memory-safety bugs, so
it never parses them inside the checking process.
"""

import json
import sys

MEMORY_LIMIT = 768 * 1024 * 1024
CPU_SECONDS = 15
PAGES = 2


def _limit() -> None:
    try:
        import resource

        resource.setrlimit(resource.RLIMIT_CPU, (CPU_SECONDS, CPU_SECONDS))
        resource.setrlimit(resource.RLIMIT_AS, (MEMORY_LIMIT, MEMORY_LIMIT))
    except (ImportError, ValueError, OSError):
        pass  # Not every platform enforces both; the caller's timeout does.


def main() -> int:
    """Read stdin, print the result; exit 1 if it isn't a readable PDF."""
    _limit()
    import pymupdf

    body = sys.stdin.buffer.read()
    try:
        with pymupdf.open(stream=body, filetype="pdf") as doc:
            title = (doc.metadata or {}).get("title") or ""
            text = " ".join(
                doc[i].get_text() for i in range(min(PAGES, len(doc)))
            )
    except Exception:  # Any failure means: unreadable.
        return 1
    heads = [title] if title.strip() else []
    json.dump({"heads": heads, "text": " ".join(text.split())}, sys.stdout)
    return 0


if __name__ == "__main__":
    sys.exit(main())
