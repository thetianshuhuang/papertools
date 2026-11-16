"""Whether a record is the work an entry cites, and what DOI it can get."""

from .bibtex import Entry
from .resolve import Resolution, entry_doi, is_preprint_entry
from .sources import Record


def doubtful_match(entry: Entry, res: Resolution, record: Record) -> str:
    """Why ``record`` may be a different work than ``entry``, or ''.

    Title searches can land on a namesake (an encyclopedia chapter called
    "Attack Trees" for Schneier's 1999 article); rewriting the entry from
    it would replace the reference with another one.
    """
    if not record.title or not record.authors:
        return (
            f"the matching record ({record.label}) has no "
            f"{'title' if not record.title else 'authors'}; it may be a "
            "different work"
        )
    year = entry.fields.get("year", "")
    years = {record.year} | record.other_years
    if (
        res.via == "title"
        # A preprint's published version is often a year or more later.
        and record is res.primary
        and year.isdigit()
        and record.year.isdigit()
        and min(abs(int(year) - int(y)) for y in years if y.isdigit()) > 1
    ):
        return (
            f"matched by title only, and the record ({record.label}) is from "
            f"{record.year}, not {year}; it may be a different work"
        )
    return ""


def known_doi(entry: Entry, res: Resolution) -> str:
    """The DOI ``--fix`` can add to an entry without one, or ''.

    Only from a record the entry matched with confidence, and never a
    preprint's DOI for an entry that cites a publication.
    """
    record = res.primary
    if entry_doi(entry) or record is None or record.hint_only:
        return ""
    if not record.doi or doubtful_match(entry, res, record):
        return ""
    if record.preprint and not is_preprint_entry(entry):
        return ""
    if is_preprint_entry(entry) and (res.published or res.hint):
        return ""  # Cite the published version instead (an arxiv warning).
    return record.doi
