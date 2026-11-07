"""Shared command-line plumbing for papertools commands."""

import sys
from collections.abc import Callable

import tyro

from . import __version__


def run(func: Callable[..., int], prog: str, argv: list[str] | None) -> int:
    """Parse arguments with tyro, call ``func``, and return its exit status."""
    args = sys.argv[1:] if argv is None else argv
    # tyro has no built-in version flag.
    if args == ["--version"]:
        print(f"{prog} {__version__}")
        return 0
    return tyro.cli(
        func,
        prog=prog,
        args=args,
        config=(
            tyro.conf.FlagCreatePairsOff,
            tyro.conf.PositionalMetavarFromFieldName,
        ),
    )
