"""anondoc's console entry point."""

import sys
from pathlib import Path
from typing import Annotated

import tyro

from .._cli import run
from .config import load_config
from .errors import AnonDocError
from .site import Leak, Result, anonymize
from .site import check as check_site


def _summary(result: Result, output: Path) -> None:
    def err(*lines: str) -> None:
        for line in lines:
            print(line, file=sys.stderr)

    stats = result.stats
    err(f"Wrote {output}: {result.written} file(s).")
    if result.skipped:
        err(f"  skipped: {', '.join(result.skipped)}")
    err(f"  site url: {result.site_url or 'unknown (no self-links rewritten)'}")
    if stats.terms:
        terms = ", ".join(f"{t} x{n}" for t, n in stats.terms.most_common())
        err(f"  terms: {terms}")
    if stats.links:
        links = ", ".join(f"{a} {n}" for a, n in sorted(stats.links.items()))
        err(f"  links: {links}")
    if stats.stripped:
        stripped = ", ".join(
            f"{r} x{n}" for r, n in stats.stripped.most_common() if n
        )
        if stripped:
            err(f"  stripped: {stripped}")
    if stats.unruled:
        err(
            "  unwrapped links that match no rule (add a 'links' rule to "
            "keep or replace them):",
            *(f"    {url}" for url in sorted(stats.unruled)),
        )


def _report(leaks: list[Leak], path: Path) -> int:
    for leak in leaks:
        print(leak)
    if leaks:
        print(f"Check: {len(leaks)} leak(s) in {path}.", file=sys.stderr)
        return 1
    print(f"Check: no leaks in {path}.", file=sys.stderr)
    return 0


def anondoc(
    path: Path,
    /,
    config: Path = Path("anondoc.yaml"),
    output: Annotated[Path | None, tyro.conf.arg(metavar="PATH")] = None,
    overwrite: bool = False,
    check: bool = False,
) -> int:
    """Anonymize a documentation site (e.g., MkDocs or Zensical) for review.

    Args:
        path: Built site directory; with --check, a directory or zip.
        config: YAML config listing terms, link rules, and files to skip.
        output: Output directory, or a .zip file; defaults to PATH-anon.zip.
        overwrite: Replace an existing output.
        check: Only check PATH for leaks; write nothing.

    """
    if check and (output is not None or overwrite):
        print(
            "anondoc: --output and --overwrite cannot be used with --check.",
            file=sys.stderr,
        )
        return 2
    try:
        cfg = load_config(config)
        if check:
            return _report(check_site(path, cfg), path)
        if output is None:
            site = path.resolve()
            output = site.parent / f"{site.name}-anon.zip"
        result = anonymize(path, output, cfg, overwrite=overwrite)
        _summary(result, output)
        return _report(check_site(output, cfg), output)
    except (AnonDocError, OSError) as exc:
        print(f"anondoc: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("anondoc: cancelled.", file=sys.stderr)
        return 130


def main(argv: list[str] | None = None) -> int:
    """Run the command and return its exit status."""
    return run(anondoc, "anondoc", argv)
