"""Entry-versus-record comparison tests (no network)."""

from papertools.bibcheck.bibtex import parse_entry
from papertools.bibcheck.checks import NOTE, WARN, check
from papertools.bibcheck.resolve import (
    Resolution,
    entry_arxiv_id,
    is_preprint_entry,
)
from papertools.bibcheck.sources import Record

PAPER = Record(
    source="crossref",
    id="10.1000/example.2022.00001",
    title="Learning Example Representations at Scale",
    authors=["Alice Smith", "Jan Novak"],
    venue="2022 IEEE/CVF Conference on Computer Vision and Pattern "
    "Recognition (CVPR)",
    year="2022",
    pages="15979-15988",
    doi="10.1000/example.2022.00001",
)
PAPER_DBLP = Record(
    source="dblp",
    id="x",
    title=PAPER.title,
    authors=["Alice Smith", "Jan Novák"],
)


def entry(**fields):
    """Build an inproceedings entry from keyword fields."""
    body = ", ".join(f"{k}={{{v}}}" for k, v in fields.items())
    return parse_entry(f"@inproceedings{{key, {body}}}")


def good(**override):
    """A correct entry, with overrides."""
    fields = {
        "title": "Learning Example Representations at Scale",
        "author": r"Smith, Alice and Nov{\'a}k, Jan",
        "booktitle": "IEEE Conf. Comput. Vis. Pattern Recog.",
        "pages": "15979--15988",
        "year": "2022",
        "doi": "10.1000/example.2022.00001",
    }
    fields.update(override)
    return entry(**fields)


def kinds(issues, level=None):
    """(level, kind) pairs, optionally of one level."""
    return [(i.level, i.kind) for i in issues if level in (None, i.level)]


def resolution(**kwargs):
    """A resolution whose primary is PAPER unless overridden."""
    kwargs.setdefault("primary", PAPER)
    kwargs.setdefault("published", kwargs["primary"])
    kwargs.setdefault("via", "doi")
    return Resolution(**kwargs)


def test_clean_entry_has_no_issues():
    """Macro venue spelling and the accented name are not flagged."""
    assert check(good(), resolution(references=[PAPER_DBLP])) == []


def test_missing_and_bad_doi():
    """Unresolvable DOI and no record at all."""
    issues = check(good(), Resolution(bad_doi=True))
    assert kinds(issues) == [(WARN, "doi"), (WARN, "missing")]


def test_source_errors_are_not_reported_as_missing():
    """A failed lookup is not evidence the paper doesn't exist."""
    assert check(good(), Resolution(errors=["arXiv: HTTP 503"])) == []


def test_title_differs_and_capitalization():
    """Garbled titles and lost capitals are warnings; unbraced is a note."""
    rec = Record(source="crossref", id="d", title="ExampleNet: Masked Models")
    res = resolution(primary=rec)
    assert kinds(check(entry(title="Examplenet: masked models"), res), WARN)[
        0
    ] == (WARN, "title")
    issues = check(entry(title="ExampleNet: masked models"), res)
    assert (NOTE, "title") in kinds(issues)
    issues = check(entry(title="{ExampleNet}: masked models"), res)
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
        title="X-Net: 4D Example Detection",
        authors=["B A"],
    )
    issues = check(
        entry(title="X-net: 4d example detection", author="A, B"),
        resolution(primary=rec),
    )
    assert [i.message for i in issues] == [
        "capitalization: 'X-net' should be {X-Net}",
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
        good(author="Smith, Alice and Novak, Jan"),
        resolution(references=[PAPER_DBLP]),
    )
    assert [i.message for i in issues] == [
        "accent: ours='Jan Novak' dblp='Jan Novák'"
    ]
    issues = check(good(author="Smith, Alice and others"), resolution())
    assert "truncated" in issues[0].message
    issues = check(good(author="Smith, Alice and Doe, Jan"), resolution())
    assert kinds(issues) == [(WARN, "author")]
    assert "mismatch" in issues[0].message


def test_reference_only_differences_are_notes():
    """When the primary agrees with ours, arXiv-only differences are notes."""
    arxiv = Record(
        source="arxiv",
        id="1",
        title=PAPER.title,
        authors=["Jan Novak", "Alice Smith"],
        preprint=True,
    )
    issues = check(
        good(author="Smith, Alice and Novak, Jan"),
        resolution(references=[arxiv]),
    )
    assert kinds(issues) == [(NOTE, "author")]


def test_middle_initials_and_nicknames():
    """Initials are fine; different given names are only a note."""
    rec = Record(
        source="crossref", id="d", title="T", authors=["Émile J. Fabrès"]
    )
    res = resolution(primary=rec)
    assert check(entry(title="T", author=r"Fabr\`es, \'Emile"), res) == []
    issues = check(entry(title="T", author=r"Fabr\`es, Milo"), res)
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
        "@article{k, title={Learning Example Representations at Scale},"
        " author={Smith, Alice and Novák, Jan},"
        " journal={arXiv preprint arXiv:2111.00003}, year={2021}}"
    )
    assert is_preprint_entry(e)
    assert entry_arxiv_id(e) == "2111.00003"
    preprint = Record(**{**PAPER.__dict__, "source": "arxiv", "preprint": True})
    issues = check(e, resolution(primary=preprint, published=PAPER))
    assert kinds(issues) == [(WARN, "arxiv")]
    hint = Record(
        source="s2", id="s", title=PAPER.title, venue="ICML", year="2020"
    )
    issues = check(e, resolution(primary=preprint, published=None, hint=hint))
    assert "Semantic Scholar" in issues[0].message
    # Year and venue are not compared for preprint entries.
    assert check(e, resolution(primary=preprint, published=None)) == []


def test_arxiv_id_forms():
    """Find arXiv ids in the places Scholar and DBLP put them."""
    for field in [
        "eprint={2301.00002}",
        "journal={arXiv preprint arXiv:2301.00002}",
        "url={https://arxiv.org/abs/2301.00002v2}",
        "volume={abs/2301.00002}",
        "doi={10.48550/arXiv.2301.00002}",
    ]:
        e = parse_entry(f"@misc{{k, {field}}}")
        assert entry_arxiv_id(e) == "2301.00002", field


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
