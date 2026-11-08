# papertools

Small command-line tools for preparing papers.

| Command | Description |
| --- | --- |
| [`bibcheck`](#bibcheck) | Check a `.bib` file against Crossref, arXiv, OpenReview, and DBLP. |
| [`gs2pdf`](#gs2pdf) | Download Google Slides diagrams as transparent, tightly cropped PDFs. |

## Install

With [uv](https://docs.astral.sh/uv/getting-started/installation/):

```bash
uv tool install git+https://github.com/thetianshuhuang/papertools
```

This installs every command listed above.

## bibcheck

Look up every entry of a `.bib` file in authoritative sources and report what disagrees. This catches the usual Google Scholar problems: arXiv citations of published papers, mangled titles (`Videomae:`), `and others`, lost accents (`Dollar`), and invented fields (`volume={2024}` on an ICLR paper).

```bash
bibcheck main.bib                    # report; exits 1 if there are warnings
bibcheck main.bib --keys he2022masked,oquab2024dinov2
bibcheck main.bib --fix              # rewrite flagged entries; original saved as main.bib.bak
```

```
he2022masked  (doi 10.1109/cvpr52688.2022.01553, via title)
  warn  author  accent: ours='Piotr Dollar' arxiv='Piotr Dollár'
  warn  pages   ours=16000--16009 crossref=15979-15988
oquab2023dinov2  (arXiv 2304.07193, via arXiv id)
  warn  title   capitalization: 'Dinov2' should be {DINOv2}
  warn  author  truncated with 'and others' (full list has 26 authors)
  warn  arxiv   published version exists: TMLR 2024 (openreview a68SUt6zFt)
```

Each entry is matched by its DOI, then its arXiv id, then an exact title search, and compared with the matching records:

| Kind | Checks |
| --- | --- |
| `missing` | No record with this title exists anywhere: possibly a hallucinated reference. |
| `title` | Title differs from the record, or capitals were lost (`Videomae` for `VideoMAE`, `K-radar:` for `K-Radar:`). Unbraced acronyms (`MLPs`) are warnings if the paper's style lowercases titles, otherwise notes. |
| `author` | Author count, `and others`, order, lost accents, all-caps names, and misspellings, compared against the record, DBLP, and arXiv. Differences that only arXiv or DBLP report (while the publisher agrees with you) are notes, as are nicknames (`Mido`/`Mahmoud`). |
| `arxiv` | The entry cites arXiv, but a published version exists. |
| `venue`, `year`, `pages`, `doi` | Disagreements with the published record, plus Scholar artifacts such as `volume` equal to the year or pages on an ICLR paper. Venues compare by conference, so `booktitle=CVPR` matches `2022 IEEE/CVF Conference on ... (CVPR)`. |
| `key` | Duplicate keys, or two entries for the same paper. |

Only warnings are shown by default; `--notes` adds minor findings that are usually fine. Read each warning before acting on it, since sources are sometimes the ones in error: arXiv has typos, and IEEE drops accents.

Whether the style lowercases titles is read from the `\bibliographystyle` in the `.tex` files next to the `.bib` (`plainnat`, `ieeenat_fullname`, and most natbib styles do; `IEEEtran`, `splncs04`, ACM, and biblatex don't). Override it with `--case sentence` or `--case keep`.

`--fix` replaces each entry that has warnings with the authoritative BibTeX (the published version where one exists), keeping its key and every other byte of the file. Venue names come from the publisher, so `@String` abbreviations in fixed entries are replaced by full names. Entries are left alone when no record matches, and when the only published source is PMLR (ICML, AISTATS, CoRL), which has no BibTeX API; copy those from the paper's page on proceedings.mlr.press.

Lookups are cached for 7 days in `~/.cache/papertools/bibcheck` (`--no-cache` bypasses it). The first run takes a few seconds per entry because arXiv limits clients to one request every 3 seconds. Set `CROSSREF_MAILTO=you@example.edu` to use Crossref's faster pool.

## gs2pdf

Download Google Slides diagrams as individual, transparent, tightly cropped PDFs. Shapes stay vector graphics, text stays selectable, and fonts stay embedded.

Share the presentation as **Anyone with the link → Viewer**, then:

```bash
gs2pdf "https://docs.google.com/presentation/d/PRESENTATION_ID/edit"
gs2pdf "SLIDES_URL" --slides 1,3-5 --output-dir figures
gs2pdf presentation.pdf  # an already downloaded PDF
```

Each slide is written to `figures/slide-001.pdf`, `slide-002.pdf`, and so on. Empty slides are skipped, and existing files are kept unless `--overwrite` is given. See `gs2pdf --help` for all options.

Each slide should contain one diagram on a solid background; everything else on the slide, including titles, is kept. Image, gradient, and translucent slide backgrounds are not supported.

## Development

Each tool lives in its own subpackage, `src/papertools/<tool>/`, with its tests in `tests/<tool>/`. To add a tool, create the subpackage with a `cli.py` whose `main(argv=None)` calls `papertools._cli.run(func, "<tool>", argv)` (this provides tyro parsing and `--version`), and register it under `[project.scripts]` in `pyproject.toml`:

```toml
[project.scripts]
gs2pdf = "papertools.gs2pdf.cli:main"
newtool = "papertools.newtool.cli:main"
```

Run the checks with:

```bash
uv run pytest
uv run ruff check
```
