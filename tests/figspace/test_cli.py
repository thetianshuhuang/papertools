"""Command-line behavior and error reporting tests."""

import pymupdf
import pytest

from papertools.figspace.cli import main


@pytest.fixture
def pdf(paper):
    """A two-page paper with a figure on page 1 and a References heading."""
    doc = paper()
    doc.new_page()
    doc.graphic(0, doc.TOP, 200)
    end = doc.caption(0, 210, "Figure 1.")
    doc.text(0, end + 20, 720)
    doc.text(1, doc.TOP, 720)
    doc.new_page()
    doc.text(0, doc.TOP, 300)
    doc.heading(0, 320, "References")
    return doc.save()


def test_cli_report(pdf, capsys):
    """Print each float and the totals."""
    assert main([str(pdf)]) == 0
    out, err = capsys.readouterr()
    assert "Figure 1" in out
    assert 'stopped at "References" on page 2' in out
    assert "Figures: 0.12 pages (10." in out
    assert "Total:" in out
    assert err == ""


def test_cli_annotate(pdf, tmp_path):
    """Write an annotated copy with the same pages."""
    output = tmp_path / "check.pdf"
    assert main([str(pdf), "--annotate", str(output)]) == 0
    with pymupdf.open(output) as document:
        assert document.page_count == 2
        assert document[0].get_drawings()


def test_cli_annotate_refuses_input(pdf, capsys):
    """Do not overwrite the input PDF."""
    assert main([str(pdf), "--annotate", str(pdf)]) == 1
    assert "overwrite" in capsys.readouterr().err


def test_cli_page_limit(pdf, capsys):
    """Stop at --page-limit."""
    assert main([str(pdf), "--page-limit", "1"]) == 0
    assert "stopped at --page-limit" in capsys.readouterr().out


def test_cli_bad_page_limit(pdf):
    """Reject a page limit below 1."""
    assert main([str(pdf), "--page-limit", "0"]) == 2


def test_cli_missing_input(tmp_path, capsys):
    """Report a missing file with a failure exit status."""
    assert main([str(tmp_path / "missing.pdf")]) == 1
    assert capsys.readouterr().err.startswith("figspace: ")


def test_cli_not_a_pdf(tmp_path, capsys):
    """Report a file that is not a PDF."""
    path = tmp_path / "paper.pdf"
    path.write_text("not a pdf")
    assert main([str(path)]) == 1
    assert "Cannot read PDF" in capsys.readouterr().err


def test_cli_no_text(tmp_path, capsys):
    """Report a PDF without text, such as a scan."""
    path = tmp_path / "scan.pdf"
    with pymupdf.open() as document:
        document.new_page().draw_rect((10, 10, 100, 100), fill=(0, 0, 0))
        document.save(path)
    assert main([str(path)]) == 1
    assert "no extractable text" in capsys.readouterr().err


@pytest.mark.parametrize("argv", [[], ["a.pdf", "--page-limit", "two"]])
def test_cli_invalid_arguments(argv):
    """Exit with usage status for unparseable arguments."""
    with pytest.raises(SystemExit) as exc:
        main(argv)
    assert exc.value.code == 2


def test_cli_version(capsys):
    """Print the version."""
    assert main(["--version"]) == 0
    assert capsys.readouterr().out.startswith("figspace ")
