"""Errors that can be presented directly to a command-line user."""


class BibCheckError(Exception):
    """An expected failure, such as an unreadable .bib file."""
