"""End-to-end command-line tests on a small site."""

import json
import zipfile

import pytest

from papertools.anondoc.cli import main


def run(site, config_file, *extra):
    """Run anondoc with the test config."""
    return main([str(site), "--config", str(config_file), *extra])


def test_directory_output(site, config_file, tmp_path, capsys):
    """Write an anonymized directory that passes the check."""
    before = {p: p.read_bytes() for p in site.rglob("*") if p.is_file()}
    out = tmp_path / "out"
    assert run(site, config_file, "--output", str(out)) == 0
    err = capsys.readouterr().err
    assert "Check: no leaks" in err
    assert "https://github.com/Acme/secret/tree/main" in err  # Unruled link.
    assert {p: p.read_bytes() for p in site.rglob("*") if p.is_file()} == before

    files = sorted(p.relative_to(out).as_posix() for p in out.rglob("*"))
    assert files == [
        "guide",
        "guide/index.html",
        "index.html",
        "index.md",
        "search.js",
        "search.json",
    ]

    index = (out / "index.html").read_text()
    assert "md-source" not in index
    assert "canonical" not in index and "llms.txt" not in index
    assert "mkdocstrings-source" not in index
    assert '<svg viewBox="0 0 1 1"><path d="M0"/></svg>' not in index
    assert "Made by Anonymous Lab." in index
    assert '<a href="https://acme.github.io/lib/api/#x">lib</a>' in index
    assert '<a href="https://pypi.org/project/tool/">tool</a>' in index
    assert "\n    the code, and" in index
    assert '<a href="guide/index.html#setup">the guide</a>' in index
    assert "https://github.com/anonymous/other" in index
    assert "<title>Anon docs</title>" in index

    guide = (out / "guide/index.html").read_text()
    assert '<a href="../index.html">Home</a>' in guide
    assert '<a href="../index.html">Up</a>' in guide
    assert "anonymous users" in guide

    md = (out / "index.md").read_text()
    assert md.startswith("# Anon docs\n\nBy anonymous using [lib](<https://")

    search = json.loads((out / "search.json").read_text())
    assert search["items"][0]["title"] == "Anon docs"
    assert search["items"][0]["text"] == "<p>By anonymous.</p> "
    assert search["items"][0]["location"] == "index.html"
    script = (out / "search.js").read_text()
    assert script.startswith("var __index = {") and script.endswith("};")
    assert json.loads(script[len("var __index = ") : -1]) == search


def test_directory_urls_kept(site, config_file, tmp_path):
    """With file_urls off, directory links and search locations stay."""
    config_file.write_text(config_file.read_text() + "file_urls: false\n")
    out = tmp_path / "out"
    assert run(site, config_file, "--output", str(out)) == 0
    assert '<a href="guide/#setup">' in (out / "index.html").read_text()
    assert '<a href="../">Home</a>' in (out / "guide/index.html").read_text()
    search = json.loads((out / "search.json").read_text())
    assert search["items"][0]["location"] == ""


def test_zip_output_and_check(site, config_file, tmp_path, capsys):
    """Zip output nests files under a folder and can be checked."""
    out = tmp_path / "anon.zip"
    assert run(site, config_file, "--output", str(out)) == 0
    with zipfile.ZipFile(out) as archive:
        assert "anon/index.html" in archive.namelist()
    capsys.readouterr()
    assert run(out, config_file, "--check") == 0
    assert "Check: no leaks" in capsys.readouterr().err


def test_default_output_name(site, config_file):
    """The default output is a zip next to the site."""
    assert run(site, config_file) == 0
    assert (site.parent / "site-anon.zip").is_file()


def test_check_reports_leaks(site, config_file, capsys):
    """Checking the original site lists leaks on stdout and fails."""
    assert run(site, config_file, "--check") == 1
    out, err = capsys.readouterr()
    assert "llms.txt: file should have been skipped" in out
    assert "index.html: strip selector still matches: .md-source" in out
    assert "index.md:1: Proj\\*" in out
    assert "leak(s) in" in err
    assert "https://acme.github.io/lib/api" not in out  # Kept URL.


def test_binary_and_file_name_leaks(site, config_file, tmp_path, capsys):
    """Terms in binary files and in file names are reported."""
    (site / "logo.png").write_bytes(b"\x89PNG\0\0ACME\0")
    (site / "acme").mkdir()
    (site / "acme/notes.txt").write_text("hi")
    out = tmp_path / "out"
    assert run(site, config_file, "--output", str(out)) == 1
    assert (out / "anonymous/notes.txt").is_file()
    assert "logo.png: Acme: 1x in binary data" in capsys.readouterr().out


def test_overwrite(site, config_file, tmp_path, capsys):
    """Existing output is kept unless --overwrite is given."""
    out = tmp_path / "out"
    out.mkdir()
    (out / "old").write_text("x")
    assert run(site, config_file, "--output", str(out)) == 1
    assert "pass --overwrite" in capsys.readouterr().err
    assert run(site, config_file, "--output", str(out), "--overwrite") == 0
    assert not (out / "old").exists()


def test_output_inside_site(site, config_file, capsys):
    """Refuse to write the output into the site being read."""
    assert run(site, config_file, "--output", str(site / "anon")) == 1
    assert "outside the site" in capsys.readouterr().err


def test_bad_inputs(site, config_file, tmp_path, capsys):
    """Report missing configs and sites without tracebacks."""
    assert run(site, tmp_path / "missing.yaml") == 1
    assert "Cannot read config" in capsys.readouterr().err
    assert run(tmp_path / "nope", config_file) == 1
    assert "is not a directory" in capsys.readouterr().err
    assert run(site, config_file, "--check", "--output", "x") == 2


def test_help_and_version(capsys):
    """Print help and version without a site."""
    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0
    assert "--check" in capsys.readouterr().out
    assert main(["--version"]) == 0
    assert capsys.readouterr().out.strip() == "anondoc 0.1.0"
