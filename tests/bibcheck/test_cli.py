"""End-to-end tests of resolution, reporting, and --fix on a fake web."""

import pytest

from papertools.bibcheck.cli import main

PAPER_ENTRY = r"""@inproceedings{smith2022learning,
  title={Learning example representations at scale},
  author={Smith, Alice and Novak, Jan},
  booktitle=CVPR,
  pages={16000--16009},
  year={2022}
}"""
PREPRINT_ENTRY = r"""@article{moreau2023example,
  title={Example networks need placeholders},
  author={Moreau, L{\'e}a and Garcia, Bruno},
  journal={arXiv preprint arXiv:2309.00001},
  year={2023}
}"""
HEADER = (
    "@String(CVPR = {IEEE Conf. Comput. Vis. Pattern Recog.})\n% keep me\n\n"
)


@pytest.fixture
def bib(tmp_path):
    """A small .bib with a header, an IEEE entry, and an arXiv entry."""
    path = tmp_path / "main.bib"
    path.write_text(HEADER + PAPER_ENTRY + "\n\n" + PREPRINT_ENTRY + "\n")
    return path


def test_report(web, bib, capsys):
    """Report pages, accent, and stale-arXiv problems; exit 1."""
    web.crossref["Learning example representations at scale"] = []
    assert main([str(bib)]) == 1
    out = capsys.readouterr().out
    assert "smith2022learning" in out
    assert "accent: ours='Jan Novak' dblp='Jan Novák'" in out
    assert "pages   ours=16000--16009 crossref=15979-15988" in out
    # Found by title via DBLP, then upgraded to the publisher's DOI record.
    assert "(doi 10.1000/example.2022.00001, via title)" in out
    assert (
        "published version exists: ICLR 2024 oral (openreview abcdEFGH12)"
        in out
    )
    assert "Checked 2 entries: 2 with warnings" in out


def test_doi_takes_priority_over_title(web, tmp_path, capsys):
    """An entry's DOI is looked up directly instead of searching Crossref."""
    path = tmp_path / "a.bib"
    path.write_text(
        PAPER_ENTRY.replace(
            "year={2022}", "year={2022}, doi={10.1000/example.2022.00001}"
        )
    )
    main([str(path)])
    assert "api.crossref.org" not in web.calls
    assert (
        "(doi 10.1000/example.2022.00001, via doi)" in capsys.readouterr().out
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
    web.crossref["Learning example representations at scale"] = []
    assert main([str(bib), "--fix"]) == 1
    out = capsys.readouterr().out
    assert "Fixed 2: smith2022learning, moreau2023example" in out
    assert bib.with_name("main.bib.bak").read_text() == original
    text = bib.read_text()
    assert text.startswith(HEADER)
    assert "author={Smith, Alice and Novák, Jan}" in text
    assert "pages={15979--15988}" in text
    assert "booktitle={The Twelfth International Conference on Learning" in text
    assert "arXiv preprint" not in text
    # A second run finds nothing left to fix.
    assert main([str(bib)]) == 0


def test_source_failure_does_not_abort(web, bib, capsys):
    """One dead source is reported, and the other entries still get checked."""
    web.down.add("export.arxiv.org")
    web.crossref["Learning example representations at scale"] = []
    assert main([str(bib)]) == 1
    captured = capsys.readouterr()
    assert "export.arxiv.org: HTTP 503" in captured.err
    assert "accent" in captured.out


def test_keys_filter(web, bib, capsys):
    """--keys checks only the named entries and rejects unknown ones."""
    main([str(bib), "--keys", "moreau2023example"])
    assert "smith2022learning" not in capsys.readouterr().out
    assert main([str(bib), "--keys", "nope"]) == 2


def test_duplicates(web, tmp_path, capsys):
    """Duplicate keys and duplicate titles are flagged."""
    path = tmp_path / "a.bib"
    path.write_text(
        PREPRINT_ENTRY + "\n" + PREPRINT_ENTRY.replace("moreau2023", "d2")
    )
    main([str(path)])
    assert "same paper as d2example" in capsys.readouterr().out


def test_errors_and_version(tmp_path, capsys):
    """Unreadable files exit 1; bad options exit 2; --version works."""
    assert main([str(tmp_path / "missing.bib")]) == 1
    bad = tmp_path / "bad.bib"
    bad.write_text("@misc{k, title={unbalanced}")
    assert main([str(bad)]) == 1
    assert "line 1" in capsys.readouterr().err
    assert main([str(bad), "--timeout", "0"]) == 2
    assert main(["--version"]) == 0
    assert capsys.readouterr().out.strip() == "pt bibcheck 0.1.0"
    with pytest.raises(SystemExit) as exc:
        main([])
    assert exc.value.code == 2


VIT_NOTE = {
    "id": "YicbFdNTTy",
    "pdate": 1610474034431,
    "content": {
        "title": "An Image is Worth 16x16 Words: Transformers for Image "
        "Recognition at Scale",
        "venue": "ICLR 2021 Oral",
        "venueid": "ICLR.cc/2021/Conference",
        "authors": ["Alexey Dosovitskiy", "Lucas Beyer"],
        "_bibtex": "@inproceedings{dosovitskiy2021an,\ntitle={An Image is "
        "Worth 16x16 Words: Transformers for Image Recognition at Scale},\n"
        "author={Alexey Dosovitskiy and Lucas Beyer},\nbooktitle={"
        "International Conference on Learning Representations},\n"
        "year={2021}\n}",
    },
}


def test_openreview_v1_records_are_found(web, tmp_path, capsys):
    """Pre-2023 venues (API v1, plain content values) count as published."""
    web.openreview_v1[VIT_NOTE["content"]["title"]] = [VIT_NOTE]
    path = tmp_path / "a.bib"
    path.write_text(
        "@article{dosovitskiy2020image, title={An image is worth 16x16 words: "
        "Transformers for image recognition at scale},"
        " author={Dosovitskiy, Alexey},"
        " journal={arXiv preprint arXiv:2010.11929},"
        " year={2020}}"
    )
    assert main([str(path), "--fix"]) == 1
    out = capsys.readouterr().out
    assert (
        "published version exists: ICLR 2021 Oral (openreview YicbFdNTTy)"
        in out
    )
    assert "count: ours=1 openreview=2" in out
    assert (
        "booktitle={International Conference on Learning Representations}"
        in (path.read_text())
    )


def test_quiet_output(web, bib, capsys):
    """No per-entry progress off a terminal; notes only with --notes."""
    web.crossref["Learning example representations at scale"] = []
    main([str(bib), "--keys", "moreau2023example"])
    captured = capsys.readouterr()
    assert "[1/1]" not in captured.err
    assert "note" not in captured.out
    main(
        [
            str(bib),
            "--keys",
            "moreau2023example",
            "--notes",
            "--case",
            "sentence",
        ]
    )
    assert "Style:" not in capsys.readouterr().err
