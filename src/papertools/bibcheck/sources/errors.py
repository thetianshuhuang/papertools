"""Errors from sources and the HTTP client."""

from ..errors import BibCheckError


class OperationCancelledError(Exception):
    """A request, or a wait before one, was cancelled (see ``Http.using``).

    Not a SourceError: it must stop the run, not be recorded and skipped.
    """


class SourceError(BibCheckError):
    """A source could not be reached or returned something unreadable."""


class NotFoundError(SourceError):
    """The source answered 404."""
