"""Command-line behavior and error reporting tests."""

import pymupdf

from papertools.supp_split.cli import main


def test_default_outputs(paper, capsys):
    """Write SOURCE-main.pdf and SOURCE-supp.pdf next to the source."""
    path = paper()
    assert main([str(path)]) == 0
    out, err = capsys.readouterr()
    main_pdf = path.with_name("paper-main.pdf")
    supp_pdf = path.with_name("paper-supp.pdf")
    assert out.splitlines() == [
        'Supplement starts on page 5 (heading "Supplementary Material").',
        f"Wrote {main_pdf} (pages 1–4)",
        f"Wrote {supp_pdf} (pages 5–6)",
    ]
    assert err == ""
    with pymupdf.open(main_pdf) as document:
        assert document.page_count == 4
    with pymupdf.open(supp_pdf) as document:
        assert document.page_count == 2


def test_custom_outputs(paper, outputs, capsys):
    """Write to --main and --supp, replacing existing files."""
    main_pdf, supp_pdf = outputs
    main_pdf.write_text("old")
    args = [str(paper()), "--main", str(main_pdf), "--supp", str(supp_pdf)]
    assert main([*args, "--page", "6"]) == 0
    out, _ = capsys.readouterr()
    assert "page 6 (--page)" in out
    assert "(page 6)" in out
    with pymupdf.open(main_pdf) as document:
        assert document.page_count == 5


def test_page_out_of_range(paper, capsys):
    """Exit 2 for a --page outside the PDF."""
    assert main([str(paper()), "--page", "9"]) == 2
    assert "between 2 and 6" in capsys.readouterr().err


def test_output_is_source(paper, capsys):
    """Exit 2 rather than overwrite the source."""
    path = paper()
    assert main([str(path), "--main", str(path)]) == 2
    assert "must be different" in capsys.readouterr().err


def test_not_found(paper, capsys):
    """Exit 1 when the supplement cannot be found."""
    assert main([str(paper(heading=None))]) == 1
    assert "pass --page N" in capsys.readouterr().err


def test_warning(paper, capsys):
    """Print warnings to stderr."""
    path = paper(anchors={"appendix.A": 4, "cite.smith": 4})
    assert main([str(path)]) == 0
    err = capsys.readouterr().err
    assert err.startswith("supp-split: warning: page 5 also holds")
