"""End-to-end tests of resolution, reporting, and --fix on a fake web."""

import pytest

from papertools.bibcheck.cli import main

MAE_ENTRY = r"""@inproceedings{he2022masked,
  title={Masked autoencoders are scalable vision learners},
  author={He, Kaiming and Dollar, Piotr},
  booktitle=CVPR,
  pages={16000--16009},
  year={2022}
}"""
REG_ENTRY = r"""@article{darcet2023vision,
  title={Vision transformers need registers},
  author={Darcet, Timoth{\'e}e and Oquab, Maxime},
  journal={arXiv preprint arXiv:2309.16588},
  year={2023}
}"""
HEADER = (
    "@String(CVPR = {IEEE Conf. Comput. Vis. Pattern Recog.})\n% keep me\n\n"
)


@pytest.fixture
def bib(tmp_path):
    """A small .bib with a header, an IEEE entry, and an arXiv entry."""
    path = tmp_path / "main.bib"
    path.write_text(HEADER + MAE_ENTRY + "\n\n" + REG_ENTRY + "\n")
    return path


def test_report(web, bib, capsys):
    """Report pages, accent, and stale-arXiv problems; exit 1."""
    web.crossref["Masked autoencoders are scalable vision learners"] = []
    assert main([str(bib)]) == 1
    out = capsys.readouterr().out
    assert "he2022masked" in out
    assert "accent: ours='Piotr Dollar' dblp='Piotr Dollár'" in out
    assert "pages   ours=16000--16009 crossref=15979-15988" in out
    # Found by title via DBLP, then upgraded to the publisher's DOI record.
    assert "(doi 10.1109/cvpr52688.2022.01553, via title)" in out
    assert (
        "published version exists: ICLR 2024 oral (openreview 2dnO3LLiJ1)"
        in out
    )
    assert "Checked 2 entries: 2 with warnings" in out


def test_doi_takes_priority_over_title(web, tmp_path, capsys):
    """An entry's DOI is looked up directly instead of searching Crossref."""
    path = tmp_path / "a.bib"
    path.write_text(
        MAE_ENTRY.replace(
            "year={2022}", "year={2022}, doi={10.1109/cvpr52688.2022.01553}"
        )
    )
    main([str(path), "--hide-notes"])
    assert "api.crossref.org" not in web.calls
    assert (
        "(doi 10.1109/cvpr52688.2022.01553, via doi)" in capsys.readouterr().out
    )


def test_rejected_openreview_submission_is_not_a_publication(
    web, tmp_path, capsys
):
    """'Submitted to ICLR' records don't count as published versions."""
    path = tmp_path / "a.bib"
    path.write_text(
        "@article{k, title={Rejected Paper About Things}, author={Author, Ann},"
        " journal={arXiv preprint arXiv:9999.99999}, year={2023}}"
    )
    assert main([str(path)]) == 1
    out = capsys.readouterr().out
    assert "missing" in out
    assert "published version" not in out


def test_fix_rewrites_only_flagged_entries(web, bib, capsys):
    """--fix rebuilds entries, repairs accents, and keeps other bytes."""
    original = bib.read_text()
    web.crossref["Masked autoencoders are scalable vision learners"] = []
    assert main([str(bib), "--fix"]) == 1
    out = capsys.readouterr().out
    assert "Fixed 2: he2022masked, darcet2023vision" in out
    assert bib.with_name("main.bib.bak").read_text() == original
    text = bib.read_text()
    assert text.startswith(HEADER)
    assert "author={He, Kaiming and Dollár, Piotr}" in text
    assert "pages={15979--15988}" in text
    assert "booktitle={The Twelfth International Conference on Learning" in text
    assert "arXiv preprint" not in text
    # A second run finds nothing left to fix.
    assert main([str(bib)]) == 0


def test_source_failure_does_not_abort(web, bib, capsys):
    """One dead source is reported, and the other entries still get checked."""
    web.down.add("export.arxiv.org")
    web.crossref["Masked autoencoders are scalable vision learners"] = []
    assert main([str(bib)]) == 1
    captured = capsys.readouterr()
    assert "export.arxiv.org: HTTP 503" in captured.err
    assert "accent" in captured.out


def test_keys_filter(web, bib, capsys):
    """--keys checks only the named entries and rejects unknown ones."""
    main([str(bib), "--keys", "darcet2023vision"])
    assert "he2022masked" not in capsys.readouterr().out
    assert main([str(bib), "--keys", "nope"]) == 2


def test_duplicates(web, tmp_path, capsys):
    """Duplicate keys and duplicate titles are flagged."""
    path = tmp_path / "a.bib"
    path.write_text(REG_ENTRY + "\n" + REG_ENTRY.replace("darcet2023", "d2"))
    main([str(path)])
    assert "same paper as d2vision" in capsys.readouterr().out


def test_errors_and_version(tmp_path, capsys):
    """Unreadable files exit 1; bad options exit 2; --version works."""
    assert main([str(tmp_path / "missing.bib")]) == 1
    bad = tmp_path / "bad.bib"
    bad.write_text("@misc{k, title={unbalanced}")
    assert main([str(bad)]) == 1
    assert "line 1" in capsys.readouterr().err
    assert main([str(bad), "--timeout", "0"]) == 2
    assert main(["--version"]) == 0
    assert capsys.readouterr().out.strip() == "bibcheck 0.1.0"
    with pytest.raises(SystemExit) as exc:
        main([])
    assert exc.value.code == 2
