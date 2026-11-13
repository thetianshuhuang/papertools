# papertools

Small command-line tools for preparing papers.

| Command | Description |
| --- | --- |
| [`bibcheck`](#bibcheck) | Check a `.bib` file against Crossref, arXiv, OpenReview, and DBLP. |
| [`gs2pdf`](#gs2pdf) | Download Google Slides diagrams as transparent, tightly cropped PDFs. |
| [`anondoc`](#anondoc) | Anonymize a documentation site (e.g., MkDocs or Zensical) for double-blind review. |
| [`texabstract`](#texabstract) | Print a LaTeX paper's abstract as plain text, with its macros expanded. |
| [`overleaf-push`](#overleaf-push) | Push to Overleaf's git bridge, retrying while coauthors are editing online. |
| [`figspace`](#figspace) | Estimate how much of a paper's main body its figures and tables take up. |

## Install

With [uv](https://docs.astral.sh/uv/getting-started/installation/):

```bash
uv tool install git+https://github.com/thetianshuhuang/papertools
```

This installs every command listed above.

## bibcheck

Look up every entry of a `.bib` file in authoritative sources and report what disagrees. This catches the usual Google Scholar problems: arXiv citations of published papers, lost capitalization, truncated author lists, lost accents, and invented fields.

```bash
bibcheck main.bib                    # report; exits 1 if there are warnings
bibcheck main.bib --keys KEY1,KEY2
bibcheck main.bib --fix              # rewrite flagged entries; original saved as main.bib.bak
```


Each entry is matched by its DOI, then its arXiv id, then an exact title search, and compared with the matching records:

| Kind | Checks |
| --- | --- |
| `missing` | No record with this title exists anywhere: possibly a hallucinated reference. |
| `title` | Title differs from the record, or capitals were lost (e.g., in acronyms or mixed-case names). Unbraced acronyms are warnings if the paper's style lowercases titles, otherwise notes. |
| `author` | Author count, `and others`, order, lost accents, all-caps names, and misspellings, compared against the record, DBLP, and arXiv. Differences that only arXiv or DBLP report (while the publisher agrees with you) are notes, as are nicknames. |
| `arxiv` | The entry cites arXiv, but a published version exists. |
| `venue`, `year`, `pages`, `doi` | Disagreements with the published record, plus Scholar artifacts such as `volume` equal to the year. Venues compare by conference, so an acronym matches the full proceedings name. |
| `key` | Duplicate keys, or two entries for the same paper. |

Only warnings are shown by default; `--notes` adds minor findings that are usually fine. Read each warning before acting on it, since sources are sometimes the ones in error: arXiv has typos, and IEEE drops accents.

Whether the style lowercases titles is read from the `\bibliographystyle` in the `.tex` files next to the `.bib` (most natbib styles do; biblatex doesn't). Override it with `--case sentence` or `--case keep`.

`--fix` replaces each entry that has warnings with the authoritative BibTeX (the published version where one exists), keeping its key and every other byte of the file. Venue names come from the publisher, so `@String` abbreviations in fixed entries are replaced by full names. Entries are left alone when no record matches, and when the only published source is PMLR, which has no BibTeX API; copy those by hand.

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
  MyLab:
  MyProject: ProjectX
links:                      # URL prefix (whole path segments), first match wins
  - match: https://github.com/mylab/published-library
    action: keep            # its URL is not anonymized
  - match: https://github.com/mylab/tool
    replace: https://pypi.org/project/tool/
  - match: https://github.com/mylab/
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

- **Terms** are replaced everywhere: page text, attributes, anchors and ids (consistently, so in-page links keep working), the search index, Markdown copies of pages, and file names. A term also matches its escaped forms in Markdown and HTML, and names broken across lines.
- **Links** with link text (`<a href>`, `[text](url)`) are unwrapped or removed by rule, keeping or dropping their text. An absolute link that contains a term and matches no rule is unwrapped and listed in the summary, so you can add a rule for it. Bare URLs are kept, replaced, or deleted by rule; otherwise the terms in them are replaced. Links to the site's own URL (`site_url`, read from the canonical link or sitemap, or set in the config) are made relative.
- **Skipped** by default: `404.html`, `objects.inv`, `llms.txt`, `llms-full.txt`, `sitemap.xml`, and `sitemap.xml.gz`, since reviewers never need them. Setting `skip` replaces this list, so repeat any of these you still want skipped; `skip: []` keeps every file. Links to skipped files are unwrapped.
- **Stripped** by default: canonical links and `meta[name=author]`. Setting `strip` replaces these, like `skip`. Each rule has a CSS `selector` (applied to HTML pages and to HTML inside JSON strings) or a regex `pattern` (applied to every text file), an optional `files` glob list, and an optional `name` for the summary. For Material and Zensical, also strip `.md-source` (repository button), `.md-content__button` (edit and view-source buttons), `.md-source-file` (git dates and authors), and `.md-social` (footer social links).
- **Opening from disk** (`file_urls: true`, the default): links to a directory, such as `cli/`, point at its `index.html`, because a browser shows a directory listing for those when the site is opened from disk. Set `file_urls: false` to keep directory links. The same applies to the JSON values named in `json_links`, such as a search index's result locations (resolved from the site root). `json_scripts` writes a script copy of a JSON file, with `{json}` standing for its rewritten contents. Material and Zensical load their search index from `search.js` when opened from disk, as in the example. Chrome still blocks their search worker from `file://`, so search may only work when the site is served (`python -m http.server`).
- **HTML** is edited in place: everything outside the removed or unwrapped elements and rewritten links stays byte-for-byte identical.
- **Binary files** such as images and fonts are copied unchanged.

`--check` reports every remaining term outside kept or replaced URLs, including terms in binary files (for example, a name embedded in a PNG) and in file names. It also reports skipped files and strip rules that still match. Each problem is printed as `path:line: term: context`. A normal run checks its own output, and still writes the output if the check fails, so you can inspect it. The check cannot know about identifying content you didn't list, such as URL slugs derived from a term, so list such variants as terms.

Reviewers can open `index.html` from the unzipped folder directly.

## texabstract

Print a paper's abstract as plain text, ready to paste into a submission form. The paper's own macros are expanded, so `\name\ is $2\times$ faster\footnote{...}` becomes `Name is 2× faster`.

```bash
texabstract main.tex          # or a directory containing it
texabstract paper/ --math tex # keep $...$ for arXiv and OpenReview, which render MathJax
texabstract main.tex | pbcopy # or xclip -selection clipboard, wl-copy
```

The abstract is read from `\begin{abstract}...\end{abstract}`, or `\abstract{...}`, after inlining `\input`, `\include`, and `\subfile`. Given a directory, texabstract uses its `.tex` file with a `\documentclass`; if there are several, it uses the one with an abstract. The abstract goes to stdout, one line per paragraph. Warnings and the word and character counts go to stderr.

- **Macros** defined with `\newcommand`, `\renewcommand`, `\providecommand`, `\DeclareRobustCommand`, and `\def` (with undelimited parameters) are expanded, including optional arguments and `\xspace`. Definitions are read in order from the paper and from style files next to it that it loads with `\usepackage`. Definitions inside braces are local and are ignored, as are definitions that use `\@` internals or `\if` conditionals, since those are layout code that texabstract cannot evaluate. Common abbreviations from conference styles, such as `\eg`, are built in. The paper's own definitions override them.
- **Spacing** follows TeX, so a macro swallows the space after it, as in the PDF. texabstract warns about this; write `\name\ is` or `\name{} is` instead.
- **Dropped:** footnotes, labels, spacing and font-size commands, and citations (with a warning). References print as `??` with a warning, since their numbers aren't known.
- **Text:** formatting such as `\textbf` is removed, keeping its text. Accents, quotes, dashes, `~`, and escaped characters become Unicode.
- **Math** (`--math unicode`, the default) becomes Unicode where possible, e.g., `$\alpha \le 10^{-3}$` → `α ≤ 10⁻³`. With `--math tex`, math is kept as `$...$`, with the paper's macros expanded inside it.

Anything else is dropped with an `unknown macro` warning, keeping its argument's text. Check the warnings before pasting.

## overleaf-push

Push to an Overleaf project's git remote while coauthors are editing it. Overleaf turns online edits into commits, so a push made while someone is typing is usually rejected, and the manual fix (commit, `git pull --rebase`, push, repeat) has to be timed to a pause in their typing. `overleaf-push` does that loop for you:

```bash
overleaf-push                        # commit any changes, then rebase and push until it lands
overleaf-push -m "Rewrite intro"     # commit message (default: "Update from <hostname>")
overleaf-push --timeout 1800         # keep trying for up to 30 minutes (default 15)
```

Uncommitted changes are committed first with `git add -A` (which respects `.gitignore`). Each attempt fetches the upstream branch, rebases your commits onto it, and pushes; nothing is ever force-pushed. Attempts come in bursts of `--burst` (default 5), up to a second apart, to catch a pause in someone's typing. After each failed burst it waits about 5, 10, 20, 40, then 60 seconds, so a long editing session is outlasted without hammering the server.

It stops instead of retrying when retrying cannot help:

- **Conflicts**: if your commits and an online edit change the same lines, the rebase is aborted (your commits are left as they were) and the conflicting files are listed. Run `git pull --rebase`, resolve them, `git rebase --continue`, and rerun.
- **Authentication and access errors**, printed as git reported them.
- **Repository state**: a detached HEAD, or a rebase, merge, or cherry-pick in progress (exit status 2).

It exits 0 once pushed (or if there is nothing to push), and 1 on a conflict, an error, or after `--timeout` seconds. On timeout your commits are already rebased, so rerunning is safe.

## figspace

Estimate how much of a paper's main body is taken up by figures and tables, to see where space can be saved before a page limit.

```bash
figspace main.pdf                        # stop at References or the LLM Usage Statement
figspace main.pdf --page-limit 8         # count at most 8 pages
figspace main.pdf --annotate check.pdf   # also write a copy with the measured regions shaded
```

```
page  float       width  pages
   1  Figure 1    full    0.37
   2  Figure 2    column  0.14
   5  Table 1     column  0.11
...
Body:    9.00 pages (pages 1–9; stopped at "LLM Usage Statement" on page 10)
Figures: 2.50 pages (27.8% of the body)
Tables:  0.11 pages (1.2% of the body)
Total:   2.61 pages (29.0% of the body)
```

Space is measured in pages of the text area, the region inside the margins, which is found from where the body text sits on most pages. Running headers, page numbers, and review line numbers are left out.

- **Floats** are found from their captions: lines starting with `Figure 3.`, `Fig. 3:`, `Table 2:`, or `TABLE II`. Each float runs from its caption through the graphics and text next to it, such as subfigure labels and table rows, and ends at body text, headings, or another caption. Figure captions are assumed to be below the figure and table captions above the table, with the other side tried if nothing is found.
- **Width:** a float takes the full width of its column, since text cannot flow beside it, or the full text width if its caption spans both columns. The space between a float and the text around it is not counted.
- **Totals** count overlapping floats, such as side-by-side figures, once.
- **End of the main body:** counting stops at the first heading reading `References`, `Bibliography`, `LLM Usage Statement`, or `The Use of Large Language Models (LLMs)` (optionally numbered), or after `--page-limit` pages, whichever comes first. The part of the stop page before the heading is counted, in reading order: in two columns, a heading in the right column counts all of the left column. If neither is found, every page is counted, with a warning.

The measurements are heuristic. Use `--annotate` to check them: it shades figures blue and tables orange, outlines the counted text area, and marks the end of the main body in red. A caption whose float could not be found is counted alone, with a warning. Floats without a numbered caption, such as algorithms or `wrapfigure`s, are not detected.

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
