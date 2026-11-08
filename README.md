# papertools

Small command-line tools for preparing papers.

| Command | Description |
| --- | --- |
| [`bibcheck`](#bibcheck) | Check a `.bib` file against Crossref, arXiv, OpenReview, and DBLP. |
| [`gs2pdf`](#gs2pdf) | Download Google Slides diagrams as transparent, tightly cropped PDFs. |
| [`anondoc`](#anondoc) | Anonymize a documentation site (e.g., MkDocs or Zensical) for double-blind review. |

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

## anondoc

Anonymize a built static documentation site (e.g., from MkDocs or Zensical) so it can be included in double-blind supplementary material. The site directory is not modified. Nothing in anondoc is specific to one site generator or theme: those details go in the config, as in the example below for Zensical with mkdocstrings.

```bash
zensical build                                   # or: mkdocs build
anondoc site --config anondoc.yaml               # writes site-anon.zip, then checks it
anondoc site --output anon/                      # write a directory instead
anondoc site-anon.zip --check                    # only check; exits 1 if anything leaks
```

The config lists the identifying terms, what to do with links that contain them, and theme-specific blocks to remove:

```yaml
placeholder: anonymous      # replacement for terms listed without one
terms:                      # case-insensitive, longest first
  RadarML:
  Tartan*: DatasetX
  Tartan: DatasetX          # heading anchors drop the '*': tartan-technical-documentation
  tartanstar: datasetx
links:                      # URL prefix (whole path segments), first match wins
  - match: https://radarml.github.io/abstract-dataloader/
    action: keep            # published library; its URL is not anonymized
  - match: https://github.com/RadarML/xwr
    replace: https://pypi.org/project/xwr/
  - match: https://github.com/RadarML/
    action: unwrap          # keep | unwrap | remove | replace: URL
skip:                       # files to leave out, gitignore-style globs; replaces the defaults
  - 404.html
  - objects.inv
  - llms.txt
  - llms-full.txt
  - sitemap.xml
  - sitemap.xml.gz
  - "*.md"
strip:                      # blocks to delete; replaces the defaults
  - selector: link[rel=canonical]
  - selector: meta[name=author]
  - name: repository button # Material/Zensical; also calls the GitHub API
    selector: .md-source
  - name: mkdocstrings source
    selector: details.mkdocstrings-source
  - name: mkdocstrings source  # the same block, flattened in the search index
    pattern: '(?s)Source code in <code>[^<]*</code>\s*<pre><code>.*?</code></pre>'
    files: [search.json]
json_links:                 # JSON keys holding root-relative links, by file
  search.json: [location]
json_scripts:               # JavaScript copies of JSON files
  - from: search.json
    to: search.js
    template: "var __index = {json};"
```

What happens to each file:

- **Terms** are replaced everywhere: page text, attributes, anchors and ids (consistently, so in-page links keep working), the search index, Markdown copies of pages, and file names. A term also matches its escaped forms, such as `Tartan\*` in Markdown, `A&amp;M` in HTML, and names broken across lines.
- **Links** with link text (`<a href>`, `[text](url)`) are unwrapped or removed by rule, keeping or dropping their text. An absolute link that contains a term and matches no rule is unwrapped and listed in the summary, so you can add a rule for it. Bare URLs are kept, replaced, or deleted by rule; otherwise the terms in them are replaced. Links to the site's own URL (`site_url`, read from the canonical link or sitemap, or set in the config) are made relative.
- **Skipped** by default: `404.html`, `objects.inv`, `llms.txt`, `llms-full.txt`, `sitemap.xml`, and `sitemap.xml.gz`, since reviewers never need them. Setting `skip` replaces this list, so repeat any of these you still want skipped; `skip: []` keeps every file. Links to skipped files are unwrapped.
- **Stripped** by default: canonical links and `meta[name=author]`. Setting `strip` replaces these, like `skip`. Each rule has a CSS `selector` (applied to HTML pages and to HTML inside JSON strings) or a regex `pattern` (applied to every text file), an optional `files` glob list, and an optional `name` for the summary. For Material and Zensical, also strip `.md-source` (repository button), `.md-content__button` (edit and view-source buttons), `.md-source-file` (git dates and authors), and `.md-social` (footer social links).
- **Opening from disk** (`file_urls: true`, the default): links to a directory, such as `cli/`, point at its `index.html`, because a browser shows a directory listing for those when the site is opened from disk. Set `file_urls: false` to keep directory links. The same applies to the JSON values named in `json_links`, such as a search index's result locations (resolved from the site root). `json_scripts` writes a script copy of a JSON file, with `{json}` standing for its rewritten contents. Material and Zensical load their search index from `search.js` when opened from disk, as in the example. Chrome still blocks their search worker from `file://`, so search may only work when the site is served (`python -m http.server`).
- **HTML** is edited in place: everything outside the removed or unwrapped elements and rewritten links stays byte-for-byte identical.
- **Binary files** such as images and fonts are copied unchanged.

`--check` reports every remaining term outside kept or replaced URLs, including terms in binary files (for example, a name embedded in a PNG) and in file names. It also reports skipped files and strip rules that still match. Each problem is printed as `path:line: term: context`. A normal run checks its own output, and still writes the output if the check fails, so you can inspect it. The check cannot know about identifying content you didn't list, such as a URL slug like `tartan-technical-documentation` derived from `Tartan*`, so list such variants as terms.

Reviewers can open `index.html` from the unzipped folder directly.

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
