"""URL checks and suggestions, author truncation, and --json output."""

import json

import pytest

from papertools.bibcheck.bibtex import parse_entry
from papertools.bibcheck.cli import main
from papertools.bibcheck.links import (
    MISMATCH,
    OK,
    UNVERIFIED,
    eu_celex,
    judge_page,
)
from papertools.bibcheck.sources import (
    Page,
    _UnsafeURLError,
    check_public_url,
)

BLOG = "https://blog.example.com/good-threat-model"
BLOG_HTML = (
    "<html><head><title>What makes a good threat model? | Example Blog"
    "</title></head><body><h1>What makes a good threat model?</h1></body>"
    "</html>"
)


def run(tmp_path, capsys, bib: str, *args) -> tuple[int, str]:
    """Run bibcheck on ``bib``; return (exit status, stdout)."""
    path = tmp_path / "refs.bib"
    path.write_text(bib)
    code = main([str(path), "--case", "keep", "--notes", *args])
    return code, capsys.readouterr().out


def test_working_url_counts_as_found(web, tmp_path, capsys):
    """An unindexed web page whose URL shows the title is not missing."""
    web.pages[BLOG] = (200, BLOG_HTML)
    code, out = run(
        tmp_path,
        capsys,
        "@misc{blog, title={What makes a good threat model?},"
        f" author={{Shostack, A.}}, url={{{BLOG}}}, year={{2024}}}}",
    )
    assert code == 1  # A warning: a person has to confirm it.
    assert "missing" not in out
    assert (
        "the link shows the title (page title matches), but check it by "
        f"hand: open {BLOG}" in out
    )
    assert "title words in the page title or headings: 5/5" in out


def test_url_in_howpublished(web, tmp_path, capsys):
    r"""``\url{}`` in howpublished is checked like a url field."""
    web.pages["https://example.org/a_b"] = (200, BLOG_HTML)
    _, out = run(
        tmp_path,
        capsys,
        "@misc{blog, title={What makes a good threat model?},"
        r" howpublished={\url{https://example.org/a\_b}}}",
    )
    assert "the link shows the title" in out


def test_broken_url_suggests_archived_copy(web, tmp_path, capsys):
    """A dead host is a warning, with the Wayback copy suggested."""
    dead = "https://community.example.com/"
    snap = "https://web.archive.org/web/20250101000000/" + dead
    web.wayback_snapshots[dead] = (snap, "20250101000000")
    web.pages[snap] = (200, BLOG_HTML)
    code, out = run(
        tmp_path,
        capsys,
        "@misc{x, title={What makes a good threat model?},"
        f" url={{{dead}}}, year={{2025}}}}",
    )
    assert code == 1
    assert "link is broken (host community.example.com does not exist)" in out
    assert (
        "suggested URL (archived copy from 2025-01-01 (Wayback Machine)): "
        + snap
    ) in out
    assert "missing" not in out


def test_page_about_something_else(web, tmp_path, capsys):
    """A URL that now shows another page is reported, with workings."""
    url = "https://cloud.example.com/vertex"
    web.pages[url] = (200, "<title>Gemini platform | Example Cloud</title>")
    code, out = run(
        tmp_path,
        capsys,
        "@misc{v, title={Vertex AI: Enterprise-ready Generative AI},"
        f" url={{{url}}}}}",
    )
    assert code == 1
    assert "link doesn't show this reference" in out
    assert "'Gemini platform | Example Cloud'" in out
    assert (
        f"check by hand: archived copies: https://web.archive.org/web/*/{url}"
        in out
    )


def test_bot_check_is_unverified_not_missing(web, tmp_path, capsys):
    """A bot challenge can't be judged; the user gets links to check."""
    url = "http://hdl.handle.net/10400.22/30350"
    web.pages[url] = (200, "<title>Making sure you're not a bot!</title>")
    _, out = run(
        tmp_path,
        capsys,
        "@mastersthesis{t, title={Assessing Things}, author={Batista, A.},"
        f" url={{{url}}}}}",
    )
    assert "link could not be checked, blocked (bot check)" in out
    assert "check by hand: web search: https://duckduckgo.com/?q=" in out
    assert "missing" not in out


def test_eu_act_suggested_from_its_number(web, tmp_path, capsys):
    """EU legislation without a URL gets its EUR-Lex page."""
    web.eu_acts["32024R2847"] = (
        "Regulation (EU) 2024/2847 of the European Parliament and of the "
        "Council of 23 October 2024 on horizontal cybersecurity requirements "
        "for products with digital elements (Cyber Resilience Act)"
    )
    _, out = run(
        tmp_path,
        capsys,
        "@misc{cra, title={Regulation ({EU}) 2024/2847 on Horizontal "
        "Cybersecurity Requirements for Products with Digital Elements "
        "({Cyber Resilience Act})}, year={2024}}",
    )
    assert (
        "suggested URL (EUR-Lex, from the act's number): https://eur-lex."
        "europa.eu/legal-content/EN/TXT/?uri=CELEX:32024R2847"
    ) in out


def test_eur_lex_url_verified_through_publications_office(
    web, tmp_path, capsys
):
    """EUR-Lex blocks scripts, so its links are checked by CELEX number."""
    web.eu_acts["32022L2555"] = (
        "Directive (EU) 2022/2555 on measures for a high common level of "
        "cybersecurity across the Union (NIS 2 Directive)"
    )
    code, out = run(
        tmp_path,
        capsys,
        "@misc{nis2, title={Directive (EU) 2022/2555 on measures for a high"
        " common level of cybersecurity}, url={https://eur-lex.europa.eu/eli/"
        "dir/2022/2555/oj}}",
    )
    assert code == 1
    assert "EU Publications Office, CELEX 32022L2555" in out
    assert "eur-lex.europa.eu" not in web.calls


def test_openalex_suggests_thesis(web, tmp_path, capsys):
    """Theses are found on OpenAlex and their repository page suggested."""
    title = "Assessing the Effectiveness of LLMs in Threat Modeling"
    web.openalex[title] = [
        {
            "display_name": title,
            "type": "dissertation",
            "publication_year": 2025,
            "doi": None,
            "primary_location": {
                "landing_page_url": "https://repo.example.pt/handle/1/2"
            },
            "authorships": [{"author": {"display_name": "Ana Batista"}}],
        }
    ]
    _, out = run(
        tmp_path,
        capsys,
        f"@mastersthesis{{t, title={{{title}}}, author={{Batista, A.}}}}",
    )
    assert (
        "not in the indexes and no URL given; suggested URL (OpenAlex "
        "dissertation): https://repo.example.pt/handle/1/2"
    ) in out


THESIS = (
    "Assessing the effectiveness of Large Language Models in automated "
    "threat modeling"
)


def test_repository_verifies_blocked_handle_link(web, tmp_path, capsys):
    """RECIPP's API confirms a handle link that its website won't show."""
    url = "http://hdl.handle.net/10400.22/30350"
    web.pages[url] = (200, "<title>Making sure you're not a bot!</title>")
    web.dspace[THESIS] = (
        "10400.22/30350",
        ["BATISTA, ANA ISABEL MOURA"],
        "master thesis",
        "2025",
    )
    code, out = run(
        tmp_path,
        capsys,
        f"@mastersthesis{{t, title={{{THESIS}}}, author={{Batista, A.}},"
        f" url={{{url}}}}}",
    )
    assert code == 1
    assert (
        "the link shows the title (RECIPP (P.PORTO) record 10400.22/30350 "
        "has this title)"
    ) in out
    assert "bot check" in out  # The workings show why the API was used.


def test_repository_suggests_thesis_link(web, tmp_path, capsys):
    """A thesis without a URL gets its RECIPP handle suggested."""
    web.dspace[THESIS] = (
        "10400.22/30350",
        ["BATISTA, ANA ISABEL MOURA"],
        "master thesis",
        "2025",
    )
    _, out = run(
        tmp_path,
        capsys,
        f"@mastersthesis{{t, title={{{THESIS}}}, author={{Batista, A.}}}}",
    )
    assert (
        "suggested URL (RECIPP (P.PORTO) master thesis): "
        "http://hdl.handle.net/10400.22/30350"
    ) in out


def test_repository_ignores_other_authors(web, tmp_path, capsys):
    """Same title by someone else is not a match."""
    web.dspace[THESIS] = ("10400.22/1", ["SMITH, JOHN"], "master thesis", "")
    _, out = run(
        tmp_path,
        capsys,
        f"@mastersthesis{{t, title={{{THESIS}}}, author={{Batista, A.}}}}",
    )
    assert "missing" in out


def test_missing_only_without_any_url(web, tmp_path, capsys):
    """No index, no URL, nothing found: missing, with search links."""
    code, out = run(
        tmp_path,
        capsys,
        "@misc{ms, title={The STRIDE Threat Model}, author={{Microsoft}}}",
    )
    assert code == 1
    assert "missing" in out
    assert "and no URL was found" in out
    assert "OpenAlex: no work titled 'The STRIDE Threat Model'" in out
    assert "check by hand: Google Scholar: https://scholar.google.com/" in out


def test_no_links_skips_fetching(web, tmp_path, capsys):
    """--no-links neither fetches pages nor searches for URLs."""
    _, out = run(
        tmp_path,
        capsys,
        f"@misc{{b, title={{Something}}, url={{{BLOG}}}}}",
        "--no-links",
    )
    assert "missing" in out
    assert "blog.example.com" not in web.calls
    assert "api.openalex.org" not in web.calls


# --- Units -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("title", "celex"),
    [
        ("Regulation (EU) 2024/2847 on things", "32024R2847"),
        ("Directive ({EU}) 2022/2555 on things", "32022L2555"),
        ("Regulation (EU) No 1025/2012 on standardisation", "32012R1025"),
        (
            "Regulation (EU) 2016/679 (General Data Protection Regulation)",
            "32016R0679",
        ),
        ("Directive 95/46/EC on data protection", "31995L0046"),
        ("Regulation (EU, Euratom) 2018/1046", "32018R1046"),
        ("Attack Trees", ""),
    ],
)
def test_eu_celex(title, celex):
    """CELEX numbers come from titles in the usual citation forms."""
    assert eu_celex(parse_entry(f"@misc{{k, title={{{title}}}}}"), []) == celex


def test_eu_celex_from_url():
    """ELI and CELEX URLs give the number directly."""
    e = parse_entry("@misc{k, title={The Act}}")
    assert eu_celex(e, ["https://eur-lex.europa.eu/eli/reg/2016/679/oj"]) == (
        "32016R0679"
    )
    assert eu_celex(e, ["https://x/?uri=CELEX:32024R2847"]) == "32024R2847"


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/",
        "http://localhost:8000/admin",
        "http://169.254.169.254/latest/meta-data/",
        "http://10.0.0.1/",
        "http://[::1]/",
        "file:///etc/passwd",
        "ftp://example.com/x",
    ],
)
def test_private_urls_are_refused(url):
    """Pages are fetched for website visitors: no internal addresses."""
    with pytest.raises(_UnsafeURLError) as exc:
        check_public_url(url)
    assert exc.value.kind in {"private", "url"}


def _page(html, status=200):
    return Page("https://x/", "https://x/", status, "text/html", html.encode())


def test_judge_page():
    """Headings decide; page text is the fallback; 403 is unverified."""
    title = "Threat Modeling Connect Hackathon 2024: Judging Notes"
    heading = (
        "<title>Brook's Hackathon Judging Notes, 2024 - Threat Modeling "
        "Connect Forum</title>"
    )
    assert judge_page(title, _page(heading))[0].status == OK
    body = f"<title>Post</title><p>{title} ... and more</p>"
    assert judge_page(title, _page(body))[0].status == OK
    assert judge_page(title, _page("<title>Home</title>"))[0].status == MISMATCH
    assert judge_page(title, _page("", 403))[0].status == UNVERIFIED


# --- Authors -----------------------------------------------------------------

SURNAMES = (
    "Adams Brown Clark Davis Evans Fisher Green Harris Irving Jones King"
    " Lewis Moore"
).split()
MANY = [f"Ann {s}" for s in SURNAMES]
MANY_CSL = {
    "DOI": "10.1000/many",
    "type": "paper-conference",
    "title": "A Paper With Many Authors",
    "container-title": "Proceedings of Things",
    "issued": {"date-parts": [[2024]]},
    "author": [{"given": n.split()[0], "family": n.split()[1]} for n in MANY],
}
MANY_BIBTEX = (
    "@inproceedings{x, title={A Paper With Many Authors}, "
    "booktitle={Proceedings of Things}, year={2024}, doi={10.1000/many}, "
    "author={"
    + " and ".join(f"{n.split()[1]}, {n.split()[0]}" for n in MANY)
    + "}}"
)
SHORT_LIST = (
    "@inproceedings{many, title={A Paper With Many Authors},"
    " author={Adams, Ann and Brown, Ann and Clark, Ann},"
    " booktitle={Proceedings of Things}, year={2024}, doi={10.1000/many}}"
)


@pytest.fixture
def many(web):
    """A 13-author paper on the fake web."""
    web.dois["10.1000/many"] = (MANY_CSL, MANY_BIBTEX)
    return web


def test_fix_truncates_long_author_lists(many, tmp_path, capsys):
    """--fix writes the first 10 of 13 and 'others'; a rerun accepts it."""
    path = tmp_path / "refs.bib"
    path.write_text(SHORT_LIST)
    assert main([str(path), "--fix"]) == 1
    text = path.read_text()
    assert "Jones, Ann and others}" in text
    assert "King" not in text
    capsys.readouterr()
    assert main([str(path), "--notes"]) == 0
    assert "truncated with 'and others' (full list has 13 authors)" in (
        capsys.readouterr().out
    )


def test_fix_writes_every_author_below_the_limit(many, tmp_path):
    """With --max-authors 20 (or 0), all 13 authors are written."""
    for limit in ("20", "0"):
        path = tmp_path / f"refs{limit}.bib"
        path.write_text(SHORT_LIST)
        main([str(path), "--fix", "--max-authors", limit])
        text = path.read_text()
        assert "Moore, Ann}" in text
        assert "others" not in text


def test_truncation_must_keep_the_first_names(many, tmp_path, capsys):
    """'and others' after the wrong names is still a warning."""
    path = tmp_path / "refs.bib"
    path.write_text(SHORT_LIST.replace("Clark, Ann", "Evans, Ann and others"))
    assert main([str(path)]) == 1
    assert "aren't the first 3 of the 13" in capsys.readouterr().out


# --- JSON --------------------------------------------------------------------


def test_json_output(web, tmp_path, capsys):
    """--json prints one document with entries, workings, and fixes."""
    web.pages[BLOG] = (200, BLOG_HTML)
    code, out = run(
        tmp_path,
        capsys,
        "@misc{blog, title={What makes a good threat model?},"
        f" url={{{BLOG}}}}}\n"
        "@misc{ms, title={The STRIDE Threat Model}}",
        "--json",
        "--fix",
    )
    report = json.loads(out)
    assert code == 1
    assert report["version"] == 1
    assert report["style"] == {"sentence_case": False, "why": "--case keep"}
    assert report["summary"] == {
        "checked": 2,
        "warnings": 2,
        "missing": 1,
        "notes_only": 0,
        "unchecked": 0,
        "syntax_errors": 0,
        "by_status": {
            "found": 0,
            "link": 1,
            "suggested": 0,
            "link-problem": 0,
            "not-found": 1,
            "unchecked": 0,
        },
    }
    assert report["syntax_errors"] == []
    assert report["entries"][0]["match"]["tone"] == "warn"
    assert report["entries"][1]["match"]["label"] == "Not found"
    assert report["entries"][1]["match"]["group"] == "missing"
    blog, ms = report["entries"]
    assert blog["match"]["status"] == "link"
    assert ms["match"]["status"] == "not-found"
    assert ms["bibtex"] == "@misc{ms, title={The STRIDE Threat Model}}"
    assert ms["fix"] == {"skipped": "no matching record"}
    assert blog["fix"] is None
    assert blog["level"] == "warn"
    assert blog["review_links"] == [
        {
            "role": "given",
            "label": "Link in the entry",
            "url": BLOG,
            "status": "ok",
            "status_label": "title found: check",
            "tone": "warn",
            "result": "page title matches",
            "advice": "The page shows the title, but only you can confirm "
            "it: open it and check it's the source you cited",
        }
    ]
    searches = [x["label"] for x in ms["review_links"]]
    assert searches == ["Web search", "Google Scholar"]
    assert ms["review_links"][0]["url"].startswith("https://duckduckgo.com/")
    assert blog["link"]["given"]["status"] == "ok"
    assert blog["issues"][0]["kind"] == "url"
    assert ms["issues"][0]["kind"] == "missing"
    assert ms["issues"][0]["details"]
    assert report["fix"] == {
        "fixed": [],
        "skipped": [{"key": "ms", "reason": "no matching record"}],
        "backup": None,
    }


def test_fix_skips_a_namesake_found_by_title(web, tmp_path, capsys):
    """A title match years away, or without authors, is not rewritten."""
    web.crossref["Attack Trees"] = [
        {
            "DOI": "10.1007/encyclopedia",
            "type": "reference-entry",
            "title": "Attack Trees",
            "container-title": "Encyclopedia of Biometrics",
            "issued": {"date-parts": [[2009]]},
            "author": [{"given": "Bruce", "family": "Schneier"}],
        }
    ]
    path = tmp_path / "refs.bib"
    original = (
        "@article{s, title={Attack Trees}, author={Schneier, B.},"
        " journal={Dr. Dobb's Journal}, year={1999}}"
    )
    path.write_text(original)
    assert main([str(path), "--fix"]) == 1
    out = capsys.readouterr().out
    assert "Not fixed: s (matched by title only, and the record" in out
    assert "is from 2009, not 1999" in out
    assert path.read_text() == original


def test_openalex_names_in_last_first_order(web, tmp_path, capsys):
    """'BATISTA, ANA' on OpenAlex is matched by surname Batista."""
    web.openalex[THESIS] = [
        {
            "display_name": THESIS,
            "type": "dissertation",
            "publication_year": 2025,
            "primary_location": {
                "landing_page_url": "https://recipp.ipp.pt/handle/10400.22/1"
            },
            "authorships": [
                {"author": {"display_name": "BATISTA, ANA ISABEL MOURA"}}
            ],
        }
    ]
    _, out = run(
        tmp_path,
        capsys,
        f"@mastersthesis{{t, title={{{THESIS}}}, author={{Batista, A.}}}}",
    )
    assert "suggested URL (OpenAlex dissertation)" in out


# --- Adding suggested URLs with --fix ----------------------------------------

CRA_TITLE = (
    "Regulation (EU) 2024/2847 of the European Parliament and of the Council "
    "of 23 October 2024 on horizontal cybersecurity requirements for products "
    "with digital elements (Cyber Resilience Act)"
)


def test_fix_adds_suggested_url(web, tmp_path, capsys):
    """A verified suggestion is written in; the rerun then verifies it."""
    web.eu_acts["32024R2847"] = CRA_TITLE
    path = tmp_path / "refs.bib"
    path.write_text(
        "% header\n@misc{cra,\n  title = {Regulation ({EU}) 2024/2847 on "
        "Horizontal Cybersecurity Requirements for Products with Digital "
        "Elements},\n  year = {2024}\n}\n"
    )
    main([str(path), "--case", "keep", "--fix"])
    out = capsys.readouterr().out
    assert "add url={https://eur-lex.europa.eu/" in out
    assert "(--fix does this)" in out
    assert "  cra: added url={https://eur-lex.europa.eu/" in out
    assert path.read_text() == (
        "% header\n@misc{cra,\n  title = {Regulation ({EU}) 2024/2847 on "
        "Horizontal Cybersecurity Requirements for Products with Digital "
        "Elements},\n  year = {2024},\n  url = {https://eur-lex.europa.eu/"
        "legal-content/EN/TXT/?uri=CELEX:32024R2847}\n}\n"
    )
    assert (
        "the link shows the title" in run(tmp_path, capsys, path.read_text())[1]
    )


def test_fix_adds_doi_rather_than_url(web, tmp_path, capsys):
    """When the suggestion has a DOI, the DOI is what's added."""
    web.openalex["A Report on Things"] = [
        {
            "display_name": "A Report on Things",
            "type": "report",
            "doi": "https://doi.org/10.5555/report",
            "primary_location": {"landing_page_url": "https://x.org/r"},
            "authorships": [],
        }
    ]
    path = tmp_path / "refs.bib"
    path.write_text("@techreport{r, title={A Report on Things}, year={2024}}")
    main([str(path), "--fix"])
    assert path.read_text() == (
        "@techreport{r, title={A Report on Things}, year={2024},"
        " doi={10.5555/report} }"
    )
    assert "add doi={10.5555/report}" in capsys.readouterr().out


def test_fix_replaces_dead_link_in_howpublished(web, tmp_path, capsys):
    r"""A dead \url{} is swapped for the verified archived copy, in place."""
    dead = "https://old.example.com/a_b"
    snap = "https://web.archive.org/web/20240101000000/" + dead
    web.wayback_snapshots[dead] = (snap, "20240101000000")
    web.pages[snap] = (200, BLOG_HTML)
    path = tmp_path / "refs.bib"
    path.write_text(
        "@misc{x, title={What makes a good threat model?},"
        r" howpublished={Blog, \url{https://old.example.com/a\_b}}}"
    )
    main([str(path), "--fix"])
    assert path.read_text() == (
        "@misc{x, title={What makes a good threat model?}, howpublished={Blog,"
        r" \url{https://web.archive.org/web/20240101000000/https://old."
        r"example.com/a\_b}}}"
    )
    assert "x: replaced https://old.example.com/a_b with" in (
        capsys.readouterr().out
    )


def test_fix_leaves_unverified_suggestion(web, tmp_path, capsys):
    """An archived copy that can't be checked is reported, not written."""
    dead = "https://old.example.com/"
    snap = "https://web.archive.org/web/2024/" + dead
    web.wayback_snapshots[dead] = (snap, "20240101000000")
    web.pages[snap] = (403, "")
    path = tmp_path / "refs.bib"
    original = f"@misc{{x, title={{Some Page}}, url={{{dead}}}}}"
    path.write_text(original)
    main([str(path), "--fix"])
    out = capsys.readouterr().out
    assert "Not fixed: x (the suggested URL's page couldn't be checked" in out
    assert path.read_text() == original


@pytest.mark.parametrize(
    ("bib", "advice"),
    [
        (
            "@misc{m, title={The STRIDE Threat Model}}",
            "add a url= for the page or document you cited",
        ),
        (
            "@article{a, title={An Unknown Article}, author={Doe, J.}}",
            "add its doi= (or arXiv id) if it has one, otherwise a url=",
        ),
        (
            "@misc{d, title={Gone}, url={https://gone.example.com/}}",
            "replace the url with a working or archived link",
        ),
    ],
)
def test_advice_asks_for_a_url(web, tmp_path, capsys, bib, advice):
    """Entries that can't be checked say what to add."""
    _, out = run(tmp_path, capsys, bib)
    assert advice in out


def test_set_field_replaces_existing_value():
    """Replacing keeps the rest of the entry byte for byte."""
    from papertools.bibcheck.fix import set_field

    text = '@misc{k, title = "A", url = {http://a/{b}}, note={n}}'
    assert set_field(text, "url", "https://c/") == (
        '@misc{k, title = "A", url = {https://c/}, note={n}}'
    )


def test_search_uses_whole_organization_name():
    """A braced organization author is searched whole, not by last word."""
    from papertools.bibcheck.links import manual_links

    e = parse_entry(
        "@misc{k, title={Cost of a Data Breach}, author={{IBM Security}}}"
    )
    assert manual_links(e, None)[0].url.endswith(
        "q=%22Cost+of+a+Data+Breach%22+IBM+Security"
    )
    e = parse_entry("@misc{k, title={T}, author={Shostack, Adam and Doe, J.}}")
    assert manual_links(e, None)[0].url.endswith("q=%22T%22+Shostack")


# --- DOIs added by --fix ------------------------------------------------------


def test_fix_adds_missing_doi_to_correct_entry(web, tmp_path, capsys):
    """A correct entry without a DOI gets the record's, and nothing else."""
    web.crossref["Learning example representations at scale"] = []
    path = tmp_path / "refs.bib"
    original = (
        "@inproceedings{p,\n  title={Learning Example Representations at "
        "Scale},\n  author={Smith, Alice and Nov{\\'a}k, Jan},\n"
        "  booktitle={CVPR},\n  pages={15979--15988},\n  year={2022}\n}"
    )
    path.write_text(original)
    assert main([str(path), "--notes"]) == 0
    assert "--fix adds doi={10.1000/example.2022.00001}" in (
        capsys.readouterr().out
    )
    main([str(path), "--fix"])
    assert path.read_text() == original.replace(
        "year={2022}\n}", "year={2022},\n  doi={10.1000/example.2022.00001}\n}"
    )
    assert "p: added doi={10.1000/example.2022.00001}" in (
        capsys.readouterr().out
    )


def test_no_doi_from_a_doubtful_match(web, tmp_path, capsys):
    """A title match years off gets no DOI added."""
    web.crossref["Attack Trees"] = [
        {
            "DOI": "10.1007/encyclopedia",
            "title": "Attack Trees",
            "issued": {"date-parts": [[2009]]},
            "author": [{"given": "Bruce", "family": "Schneier"}],
        }
    ]
    path = tmp_path / "refs.bib"
    original = (
        "@misc{s, title={Attack Trees}, author={Schneier, B.}, year={1999}}"
    )
    path.write_text(original)
    main([str(path), "--fix", "--notes"])
    out = capsys.readouterr().out
    assert "--fix adds doi" not in out
    assert path.read_text() == original


# --- Duplicates --------------------------------------------------------------


def test_duplicate_by_doi_with_different_titles(web, tmp_path, capsys):
    """Two entries with the same DOI are the same work."""
    _, out = run(
        tmp_path,
        capsys,
        "@misc{a, title={One Title}, doi={10.9999/X}}\n"
        "@misc{b, title={Another Title}, doi={10.9999/x}}",
        "--no-links",
    )
    assert "b\n" in out or "b  (" in out
    assert "same work as a (same DOI 10.9999/x)" in out


def test_duplicate_by_arxiv_id(web, tmp_path, capsys):
    """Same arXiv id, cited two ways, is one work."""
    _, out = run(
        tmp_path,
        capsys,
        "@article{a, title={Example networks need placeholders},"
        " author={Moreau, L{\\'e}a and Garcia, Bruno},"
        " journal={arXiv preprint arXiv:2309.00001}, year={2023}}\n"
        "@misc{b, title={Example nets need placeholders (preprint)},"
        " eprint={2309.00001}, year={2023}}",
        "--no-links",
    )
    assert "same work as a (" in out


def test_duplicate_eu_act_with_different_titles(web, tmp_path, capsys):
    """Two citations of one regulation, worded differently, are flagged."""
    web.eu_acts["32024R2847"] = CRA_TITLE
    _, out = run(
        tmp_path,
        capsys,
        "@misc{cra, title={Regulation ({EU}) 2024/2847 on Horizontal "
        "Cybersecurity Requirements (Cyber Resilience Act)}, year={2024}}\n"
        "@misc{cra2, title={" + CRA_TITLE + "}, url={https://eur-lex.europa.eu"
        "/legal-content/EN/TXT/?uri=CELEX:32024R2847}}",
    )
    assert "same work as cra (" in out


def test_duplicate_by_link(web, tmp_path, capsys):
    """Same page (ignoring www. and a trailing slash) is one work."""
    web.pages["https://www.example.org/page/"] = (200, BLOG_HTML)
    web.pages["https://example.org/page"] = (200, BLOG_HTML)
    _, out = run(
        tmp_path,
        capsys,
        "@misc{a, title={What makes a good threat model?},"
        " url={https://www.example.org/page/}}\n"
        "@misc{b, title={What makes a good threat model, really?},"
        " url={https://example.org/page}}",
    )
    assert "same work as a (same link https://example.org/page)" in out


def test_same_title_is_not_reported_twice(web, tmp_path, capsys):
    """A same-title pair keeps its one 'same paper as' warning."""
    _, out = run(
        tmp_path,
        capsys,
        "@misc{a, title={X Y Z}, doi={10.9999/q}}\n"
        "@misc{b, title={X Y Z}, doi={10.9999/q}}",
        "--no-links",
    )
    assert "same paper as b" in out
    assert "same work as" not in out
