"""Errors that can be presented directly to a command-line user."""


class SuppSplitError(Exception):
    """An expected failure to read or split a paper."""


class UsageError(SuppSplitError):
    """An invalid argument, such as a page outside the PDF."""
