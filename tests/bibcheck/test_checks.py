"""Entry-versus-record comparison tests (no network)."""

from papertools.bibcheck.bibtex import parse_entry
from papertools.bibcheck.checks import NOTE, WARN, check
from papertools.bibcheck.resolve import (
    Resolution,
    entry_arxiv_id,
    is_preprint_entry,
)
from papertools.bibcheck.sources import Record

MAE = Record(
    source="crossref",
    id="10.1109/cvpr52688.2022.01553",
    title="Masked Autoencoders Are Scalable Vision Learners",
    authors=["Kaiming He", "Piotr Dollar"],
    venue="2022 IEEE/CVF Conference on Computer Vision and Pattern "
    "Recognition (CVPR)",
    year="2022",
    pages="15979-15988",
    doi="10.1109/cvpr52688.2022.01553",
)
MAE_DBLP = Record(
    source="dblp",
    id="x",
    title=MAE.title,
    authors=["Kaiming He", "Piotr Dollár"],
)


def entry(**fields):
    """Build an inproceedings entry from keyword fields."""
    body = ", ".join(f"{k}={{{v}}}" for k, v in fields.items())
    return parse_entry(f"@inproceedings{{key, {body}}}")


def good(**override):
    """A correct MAE entry, with overrides."""
    fields = {
        "title": "Masked Autoencoders Are Scalable Vision Learners",
        "author": r"He, Kaiming and Doll{\'a}r, Piotr",
        "booktitle": "IEEE Conf. Comput. Vis. Pattern Recog.",
        "pages": "15979--15988",
        "year": "2022",
        "doi": "10.1109/cvpr52688.2022.01553",
    }
    fields.update(override)
    return entry(**fields)


def kinds(issues, level=None):
    """(level, kind) pairs, optionally of one level."""
    return [(i.level, i.kind) for i in issues if level in (None, i.level)]


def resolution(**kwargs):
    """A resolution whose primary is MAE unless overridden."""
    kwargs.setdefault("primary", MAE)
    kwargs.setdefault("published", kwargs["primary"])
    kwargs.setdefault("via", "doi")
    return Resolution(**kwargs)


def test_clean_entry_has_no_issues():
    """Macro venue spelling and the accented name are not flagged."""
    assert check(good(), resolution(references=[MAE_DBLP])) == []


def test_missing_and_bad_doi():
    """Unresolvable DOI and no record at all."""
    issues = check(good(), Resolution(bad_doi=True))
    assert kinds(issues) == [(WARN, "doi"), (WARN, "missing")]


def test_source_errors_are_not_reported_as_missing():
    """A failed lookup is not evidence the paper doesn't exist."""
    assert check(good(), Resolution(errors=["arXiv: HTTP 503"])) == []


def test_title_differs_and_capitalization():
    """Garbled titles and lost capitals are warnings; unbraced is a note."""
    rec = Record(source="crossref", id="d", title="VideoMAE: Masked Models")
    res = resolution(primary=rec)
    assert kinds(check(entry(title="Videomae: masked models"), res), WARN)[
        0
    ] == (WARN, "title")
    issues = check(entry(title="VideoMAE: masked models"), res)
    assert (NOTE, "title") in kinds(issues)
    issues = check(entry(title="{VideoMAE}: masked models"), res)
    assert (NOTE, "title") not in kinds(issues)
    issues = check(entry(title="Masked Models"), res)
    assert issues[0].message.startswith("differs")


def test_unbraced_acronyms_depend_on_style():
    """Unbraced acronyms are notes, or warnings for sentence-case styles."""
    rec = Record(
        source="crossref",
        id="d",
        title="Frequency-domain MLPs",
        authors=["B A"],
    )
    res = resolution(primary=rec)
    e = entry(title="Frequency-domain MLPs", author="A, B")
    assert kinds(check(e, res)) == [(NOTE, "title")]
    issues = check(e, res, sentence_case=True)
    assert kinds(issues) == [(WARN, "title")]
    assert issues[0].message == "'MLPs' will print as 'mlps'; write {MLPs}"


def test_leading_system_name():
    """Capitals in a leading 'Name:' count, even after a hyphen."""
    rec = Record(
        source="crossref",
        id="d",
        title="K-Radar: 4D Radar Detection",
        authors=["B A"],
    )
    issues = check(
        entry(title="K-radar: 4d radar detection", author="A, B"),
        resolution(primary=rec),
    )
    assert [i.message for i in issues] == [
        "capitalization: 'K-radar' should be {K-Radar}",
        "capitalization: '4d' should be {4D}",
    ]
    # Hyphenated words elsewhere are ordinary title case.
    rec = Record(
        source="crossref",
        id="d",
        title="Self-Supervised Learning",
        authors=["B A"],
    )
    assert (
        check(
            entry(title="Self-supervised learning", author="A, B"),
            resolution(primary=rec),
        )
        == []
    )


def test_authors_accent_truncation_and_mismatch():
    """Each author problem is reported with the source that disagrees."""
    issues = check(
        good(author="He, Kaiming and Dollar, Piotr"),
        resolution(references=[MAE_DBLP]),
    )
    assert [i.message for i in issues] == [
        "accent: ours='Piotr Dollar' dblp='Piotr Dollár'"
    ]
    issues = check(good(author="He, Kaiming and others"), resolution())
    assert "truncated" in issues[0].message
    issues = check(good(author="He, Kaiming and Doe, Piotr"), resolution())
    assert kinds(issues) == [(WARN, "author")]
    assert "mismatch" in issues[0].message


def test_reference_only_differences_are_notes():
    """When the primary agrees with ours, arXiv-only differences are notes."""
    arxiv = Record(
        source="arxiv",
        id="1",
        title=MAE.title,
        authors=["Piotr Dollar", "Kaiming He"],
        preprint=True,
    )
    issues = check(
        good(author="He, Kaiming and Dollar, Piotr"),
        resolution(references=[arxiv]),
    )
    assert kinds(issues) == [(NOTE, "author")]


def test_middle_initials_and_nicknames():
    """Initials are fine; different given names are only a note."""
    rec = Record(
        source="crossref", id="d", title="T", authors=["Emmanuel J. Candès"]
    )
    res = resolution(primary=rec)
    assert check(entry(title="T", author=r"Cand\`es, Emmanuel"), res) == []
    issues = check(entry(title="T", author=r"Cand\`es, Manny"), res)
    assert kinds(issues) == [(NOTE, "author")]


def test_metadata_mismatches():
    """Venue, year, pages, and DOI disagreements."""
    issues = check(
        good(
            booktitle="International Conference on Computer Vision",
            year="2021",
            pages="1--10",
            doi="10.1234/other",
        ),
        resolution(),
    )
    assert kinds(issues) == [
        (WARN, "venue"),
        (WARN, "year"),
        (WARN, "pages"),
        (WARN, "doi"),
    ]
    # A missing DOI is not reported: most styles don't print it.
    assert check(good(doi=""), resolution()) == []


def test_scholar_artifacts_on_iclr():
    """volume={2024} and pages on an ICLR paper are flagged."""
    rec = Record(
        source="openreview",
        id="o",
        title="T",
        venue="ICLR 2024 oral",
        year="2024",
    )
    issues = check(
        entry(
            title="T",
            author="A, B",
            booktitle="ICLR",
            volume="2024",
            pages="1--20",
            year="2024",
        ),
        resolution(primary=Record(**{**rec.__dict__, "authors": ["B A"]})),
    )
    assert [i.kind for i in issues] == ["venue", "venue"]


def test_arxiv_entries():
    """Stale arXiv citations, from a record or from Semantic Scholar."""
    e = parse_entry(
        "@article{k, title={Masked Autoencoders Are Scalable Vision Learners},"
        " author={He, Kaiming and Dollár, Piotr},"
        " journal={arXiv preprint arXiv:2111.06377}, year={2021}}"
    )
    assert is_preprint_entry(e)
    assert entry_arxiv_id(e) == "2111.06377"
    preprint = Record(**{**MAE.__dict__, "source": "arxiv", "preprint": True})
    issues = check(e, resolution(primary=preprint, published=MAE))
    assert kinds(issues) == [(WARN, "arxiv")]
    hint = Record(
        source="s2", id="s", title=MAE.title, venue="ICML", year="2020"
    )
    issues = check(e, resolution(primary=preprint, published=None, hint=hint))
    assert "Semantic Scholar" in issues[0].message
    # Year and venue are not compared for preprint entries.
    assert check(e, resolution(primary=preprint, published=None)) == []


def test_arxiv_id_forms():
    """Find arXiv ids in the places Scholar and DBLP put them."""
    for field in [
        "eprint={2301.08243}",
        "journal={arXiv preprint arXiv:2301.08243}",
        "url={https://arxiv.org/abs/2301.08243v2}",
        "volume={abs/2301.08243}",
        "doi={10.48550/arXiv.2301.08243}",
    ]:
        e = parse_entry(f"@misc{{k, {field}}}")
        assert entry_arxiv_id(e) == "2301.08243", field


def test_csl_subtitle_and_print_year():
    """ACM subtitles are joined; the print year wins over online-first."""
    from papertools.bibcheck.sources import Sources

    rec = Sources._csl(
        {
            "DOI": "10.1145/x",
            "title": "SpotFi",
            "subtitle": ["Decimeter Level Localization Using WiFi"],
            "issued": {"date-parts": [[2012, 11, 14]]},
            "published-print": {"date-parts": [[2013, 8]]},
        }
    )
    assert rec.title == "SpotFi: Decimeter Level Localization Using WiFi"
    assert rec.year == "2013"
    assert rec.other_years == {"2012"}
    rec = Sources._csl({"title": ["A: B"], "subtitle": ["B"]})
    assert rec.title == "A: B"


def test_journal_years():
    """Online and print years both pass; early access is only a note."""
    rec = Record(
        source="crossref",
        id="d",
        title="T",
        authors=["B A"],
        venue="IEEE Sensors Journal",
        year="2024",
        other_years={"2023"},
    )
    article = parse_entry(
        "@article{k, title={T}, author={A, B}, journal={IEEE Sensors Journal},"
        " year={2023}}"
    )
    assert check(article, resolution(primary=rec)) == []
    rec.other_years = set()
    assert kinds(check(article, resolution(primary=rec))) == [(NOTE, "year")]
    inproc = entry(title="T", author="A, B", booktitle="CVPR", year="2023")
    assert kinds(check(inproc, resolution(primary=rec))) == [(WARN, "year")]
