"""Shared helpers for converting LaTeX snippets."""

import pytest

from papertools.texabstract.convert import Renderer, definitions
from papertools.texabstract.macros import expand, tokenize
from papertools.texabstract.source import strip_comments


def _convert(body: str, preamble: str = "", math: str = "unicode"):
    macros = definitions(strip_comments(preamble), [])
    renderer = Renderer(math)
    text = renderer.render(expand(tokenize(strip_comments(body)), macros))
    return text, renderer.warnings


@pytest.fixture
def convert():
    """Convert a body with the macros in a preamble to (text, warnings)."""
    return _convert


@pytest.fixture
def paper(tmp_path):
    """Write files into a paper directory and return its path."""

    def make(**files: str):
        for name, content in files.items():
            path = tmp_path / name.replace("__", "/")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
        return tmp_path

    return make
