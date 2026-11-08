"""Read a built site, write the anonymized copy, and check for leaks."""

import os
import posixpath
import re
import shutil
import tempfile
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

from .config import Config, normalize_site_url
from .errors import AnonDocError
from .formats import HTML_EXTENSIONS, decode, rewrite_text
from .htmledit import parse
from .rewrite import Rewriter, Stats


@dataclass(frozen=True)
class Leak:
    """Something in the output that the config says should not be there.

    Attributes:
        path: Site-relative path of the file.
        line: One-based line number, or ``None`` if not applicable.
        what: The term, or a description of the problem.
        context: The surrounding text.

    """

    path: str
    line: int | None
    what: str
    context: str = ""

    def __str__(self) -> str:
        """Format as ``path:line: what: context``."""
        where = self.path if self.line is None else f"{self.path}:{self.line}"
        context = f": {self.context}" if self.context else ""
        return f"{where}: {self.what}{context}"


@dataclass
class Result:
    """What a write run did.

    Attributes:
        written: Number of files written.
        skipped: Site-relative paths left out.
        site_url: The site URL used for self-links, if any.
        stats: Replacement and link counts.

    """

    written: int = 0
    skipped: list[str] = field(default_factory=list)
    site_url: str | None = None
    stats: Stats = field(default_factory=Stats)


def iter_files(path: Path) -> Iterator[tuple[str, bytes]]:
    """Yield ``(site-relative path, contents)`` from a directory or zip.

    A zip whose entries all sit under one top-level folder is read relative to
    that folder.
    """
    if path.is_dir():
        for root, dirs, files in os.walk(path):
            dirs.sort()
            for name in sorted(files):
                file = Path(root) / name
                yield file.relative_to(path).as_posix(), file.read_bytes()
        return
    if not zipfile.is_zipfile(path):
        raise AnonDocError(f"{path} is not a directory or a zip file.")
    with zipfile.ZipFile(path) as archive:
        names = [n for n in archive.namelist() if not n.endswith("/")]
        tops = {n.split("/", 1)[0] for n in names}
        strip = len(tops) == 1 and all("/" in n for n in names)
        for name in names:
            rel = name.split("/", 1)[1] if strip else name
            yield rel, archive.read(name)


def infer_site_url(site: Path) -> str | None:
    """Read the site URL from the home page's canonical link or the sitemap."""
    index = site / "index.html"
    if index.is_file():
        html = index.read_text(encoding="utf-8", errors="replace")
        for tag in re.findall(r"<link\b[^>]*>", html, re.IGNORECASE):
            if re.search(r"\brel=[\"']?canonical\b", tag, re.IGNORECASE):
                if m := re.search(r"\bhref=[\"']([^\"']+)", tag):
                    return normalize_site_url(m.group(1))
    sitemap = site / "sitemap.xml"
    if sitemap.is_file():
        xml = sitemap.read_text(encoding="utf-8", errors="replace")
        if m := re.search(r"<loc>\s*([^<\s]+)", xml):
            return normalize_site_url(m.group(1))
    return None


def _check_output(output: Path, site: Path, overwrite: bool) -> None:
    site, target = site.resolve(), output.resolve()
    if target == site or target.is_relative_to(site):
        raise AnonDocError("The output must be outside the site directory.")
    if site.is_relative_to(target):
        raise AnonDocError("The output cannot contain the site directory.")
    if (output.exists() or output.is_symlink()) and not overwrite:
        raise AnonDocError(
            f"{output} already exists; pass --overwrite to replace it."
        )


def _remove(output: Path) -> None:
    if output.is_dir() and not output.is_symlink():
        shutil.rmtree(output)
    elif output.exists() or output.is_symlink():
        output.unlink()


def anonymize(
    site: Path, output: Path, config: Config, *, overwrite: bool = False
) -> Result:
    """Write an anonymized copy of ``site`` to a directory or ``.zip``.

    Args:
        site: Built site directory; it is not modified.
        output: Output directory, or a path ending in ``.zip``.
        config: What to anonymize.
        overwrite: Replace an existing output.

    """
    if not site.is_dir():
        raise AnonDocError(f"{site} is not a directory.")
    _check_output(output, site, overwrite)
    site_url = config.site_url or infer_site_url(site)
    rw = Rewriter(config, site_url)
    result = Result(site_url=site_url, stats=rw.stats)
    is_zip = output.suffix.lower() == ".zip"
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(
        tempfile.mkdtemp(prefix=f".{output.name}.", dir=output.parent)
    )
    try:
        inputs = []
        for rel, data in iter_files(site):
            if rw.is_skipped(rel):
                result.skipped.append(rel)
            else:
                inputs.append((rel, data))
        rw.files = {rw.rename(rel) for rel, _ in inputs}
        if len(rw.files) < len(inputs):
            raise AnonDocError("Renaming terms would merge two files.")
        files: dict[str, bytes] = {}
        for rel, data in inputs:
            text = decode(data)
            if text is not None:
                data = rewrite_text(text, rel, rw).encode("utf-8")
            files[rw.rename(rel)] = data
        for script in config.json_scripts:
            source = files.get(rw.rename(script.source))
            if source is not None:
                text = script.template.replace("{json}", source.decode())
                files.setdefault(rw.rename(script.path), text.encode())
        if is_zip:
            staged = staging / output.name
            top = output.stem
            with zipfile.ZipFile(staged, "w", zipfile.ZIP_DEFLATED) as zf:
                for rel, data in files.items():
                    zf.writestr(f"{top}/{rel}", data)
        else:
            staged = staging / "site"
            for rel, data in files.items():
                file = staged / rel
                file.parent.mkdir(parents=True, exist_ok=True)
                file.write_bytes(data)
        _remove(output)
        staged.rename(output)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    result.written = len(files)
    return result


def _excerpt(line: str, start: int, end: int, width: int = 40) -> str:
    left = max(0, start - width)
    right = min(len(line), end + width)
    text = line[left:right].strip()
    return ("…" if left else "") + text + ("…" if right < len(line) else "")


def _check_text(rel: str, text: str, rw: Rewriter) -> Iterator[Leak]:
    for number, line in enumerate(text.splitlines(), 1):
        for m in rw.find_terms(line):
            yield Leak(rel, number, m.group(), _excerpt(line, *m.span()))
    is_html = posixpath.splitext(rel)[1].lower() in HTML_EXTENSIONS
    soup = None
    for rule in rw.config.strip:
        if not rule.applies_to(rel):
            continue
        if rule.pattern is not None and rule.pattern.search(text):
            yield Leak(rel, None, f"strip pattern still matches: {rule.label}")
        if rule.selector is not None and is_html:
            if soup is None:
                soup = parse(text)
            if soup.select_one(rule.selector):
                yield Leak(
                    rel, None, f"strip selector still matches: {rule.label}"
                )


def check(path: Path, config: Config) -> list[Leak]:
    """Find terms, unskipped files, and unstripped blocks in a site.

    Args:
        path: A site directory or zip, usually an anondoc output.
        config: The config the site should satisfy.

    """
    if not path.exists():
        raise AnonDocError(f"{path} does not exist.")
    rw = Rewriter(config)
    leaks: list[Leak] = []
    for rel, data in iter_files(path):
        if rw.is_skipped(rel):
            leaks.append(Leak(rel, None, "file should have been skipped"))
            continue
        for m in rw.term_re.finditer(rel):
            leaks.append(Leak(rel, None, m.group(), "in the file name"))
        text = decode(data)
        if text is not None:
            leaks.extend(_check_text(rel, text, rw))
            continue
        lowered = data.lower()
        for term in config.terms:
            count = lowered.count(term.lower().encode("utf-8"))
            if count:
                leaks.append(Leak(rel, None, term, f"{count}x in binary data"))
    return leaks
