"""Normalization of names, titles, venues, and page ranges for comparison."""

import re
import unicodedata

from .._latex import latex_accents


def delatex(text: str) -> str:
    """Convert common LaTeX accents and symbols to Unicode, dropping braces."""
    text = latex_accents(text)
    text = re.sub(r"\\([&%$#_])", r"\1", text)
    text = text.replace("{", "").replace("}", "").replace("~", " ")
    text = text.replace("---", "—").replace("--", "–")
    return unicodedata.normalize("NFC", " ".join(text.split()))


_LETTERS = str.maketrans(
    {
        "ø": "o",
        "Ø": "O",
        "ł": "l",
        "Ł": "L",
        "ı": "i",
        "ȷ": "j",
        "ß": "ss",
        "æ": "ae",
        "Æ": "AE",
        "œ": "oe",
        "Œ": "OE",
        "đ": "d",
        "Đ": "D",
    }
)


def shouting(name: str) -> bool:
    """Whether a name has an all-caps word, e.g. ``Alice SMITH``."""
    return any(len(w) > 2 and w.isalpha() and w.isupper() for w in name.split())


def strip_accents(text: str) -> str:
    """Lowercase ``text`` and drop diacritics."""
    decomposed = unicodedata.normalize("NFD", text)
    plain = "".join(c for c in decomposed if not unicodedata.combining(c))
    return plain.translate(_LETTERS).lower()


def count_accents(text: str) -> int:
    """Number of diacritics and special letters (ø, ł, ...) in ``text``."""
    decomposed = unicodedata.normalize("NFD", text)
    return sum(
        unicodedata.combining(c) > 0 or ord(c) in _LETTERS for c in decomposed
    )


def norm_title(title: str) -> str:
    """Reduce a title to lowercase ASCII letters and digits."""
    return re.sub(r"[^a-z0-9]", "", strip_accents(delatex(title)))


def first_last(name: str) -> str:
    """Convert ``Last, First`` to ``First Last`` and drop DBLP's suffixes."""
    name = re.sub(r"\s+\d{4}$", "", " ".join(name.split()))
    if "," in name:
        parts = [p.strip() for p in name.split(",")]
        # "von Last, Jr, First" -> "First von Last Jr"
        if len(parts) == 3:
            return f"{parts[2]} {parts[0]} {parts[1]}"
        return f"{parts[1]} {parts[0]}".strip()
    return name


def split_authors(field: str) -> list[str]:
    """Split a BibTeX author field into ``First Last`` names."""
    names = re.split(r"\s+and\s+", " ".join(field.split()))
    return [first_last(delatex(n)) for n in names if n]


def name_key(name: str) -> str:
    """Comparison key for an author: accentless, no punctuation or spaces."""
    return re.sub(r"[^a-z]", "", strip_accents(name))


def norm_pages(pages: str) -> str:
    """Normalize page-range dashes to ``--``."""
    return re.sub(r"\s*[-‐‑‒–—―]+\s*", "--", pages.strip())


def protect(title: str, proper: tuple[str, ...] = ()) -> str:
    """Brace words that a sentence-casing ``.bst`` would mangle.

    That is, words with internal capitals (ExampleNet), letter-digit mixes
    (ABCv2), a leading name before a colon (X-Net:), and the given proper
    nouns.
    """

    def word(i: int, w: str) -> str:
        core = w.strip("{}")
        if w.startswith("{") or not core:
            return w
        if (
            needs_protection(core, leading=i == 0)
            or (re.search(r"\d", core) and re.search(r"[A-Za-z]", core))
            or core.rstrip(":,.?!") in proper
        ):
            return "{" + w + "}"
        return w

    if title.startswith("{") and title.endswith("}") and title.count("{") == 1:
        return title
    return " ".join(word(i, w) for i, w in enumerate(title.split(" ")))


def needs_protection(word: str, *, leading: bool = False) -> bool:
    """Whether ``word`` has capitals a sentence-casing style would lose.

    With ``leading``, ``word`` is the title's first word; if it ends with a
    colon it names the paper's system (X-Net:, ExampleNet:), so every capital
    after the first letter counts, including after hyphens.
    """
    core = word.strip(",.?!;()")
    if leading and core.endswith(":") and re.search(r"[A-Z]", core[1:]):
        return True
    core = core.strip(":")
    return bool(core) and (
        any(re.search(r"[A-Z]", part[1:]) for part in core.split("-"))
        or bool(re.search(r"\d", core) and re.search(r"[A-Z]", core))
    )


# Ordered: more specific patterns first (workshops before main conferences).
_VENUES = [
    ("CVPRW", r"\bCVPRW\b|CVPR\s*Workshops?|Pattern Recog(nition)?\.? Worksh"),
    ("ICCVW", r"\bICCVW\b|ICCV\s*Workshops?|Computer Vision Workshops?"),
    ("ICCVW", r"Int\.? Conf\.? Comput\.? Vis\.? Worksh"),
    ("arXiv", r"\barxiv\b|\bcorr\b"),
    ("CVPR", r"\bCVPR\b|Computer Vision and Pattern Recognition"),
    ("CVPR", r"Conf\.? Comput\.? Vis\.? Pattern Recog"),
    ("ICCV", r"\bICCV\b|International Conference on Computer Vision"),
    ("ICCV", r"Int\.? Conf\.? Comput\.? Vis\b"),
    ("ECCV", r"\bECCV\b|European Conference on Computer Vision"),
    ("ECCV", r"Eur\.? Conf\.? Comput\.? Vis"),
    ("WACV", r"\bWACV\b|Winter Conference on Applications of Computer Vis"),
    ("NeurIPS", r"\bNeurIPS\b|\bNIPS\b|Neural Information Processing Systems"),
    ("NeurIPS", r"Adv\.? Neural Inform"),
    ("ICLR", r"\bICLR\b|International Conference on Learning Representations"),
    ("ICLR", r"Int\.? Conf\.? Learn\.? Represent"),
    ("ICML", r"\bICML\b|International Conference on Machine Learning"),
    ("AISTATS", r"\bAISTATS\b|Artificial Intelligence and Statistics"),
    ("CoRL", r"\bCoRL\b|Conference on Robot Learning"),
    ("TMLR", r"\bTMLR\b|Trans(actions|\.)? (on )?Mach(ine|\.)? Learn"),
    ("TPAMI", r"\b(TPAMI|PAMI)\b|Pattern Analysis and Machine Intelligence"),
    ("TPAMI", r"Trans\.? Pattern Anal"),
    ("IJCV", r"\bIJCV\b|International Journal of Computer Vision"),
    ("IJCV", r"Int\.? J\.? Comput\.? Vis"),
    ("ICRA", r"\bICRA\b|International Conference on Robotics and Automation"),
    ("IROS", r"\bIROS\b|Intelligent Robots and Systems"),
    ("RSS", r"\bRSS\b|Robotics: Science and Systems"),
    ("AAAI", r"\bAAAI\b"),
    ("IJCAI", r"\bIJCAI\b"),
    ("ACL", r"\bACL\b|Association for Computational Linguistics"),
    ("EMNLP", r"\bEMNLP\b|Empirical Methods in Natural Language"),
    ("MobiCom", r"\bMobiCom\b|Mobile Computing and Networking"),
    ("SenSys", r"\bSenSys\b|Embedded Networked Sensor Systems"),
]
_VENUE_RES = [(name, re.compile(p, re.I)) for name, p in _VENUES]


def venue_family(venue: str) -> str | None:
    """Map a venue string to a canonical acronym, or None if unknown."""
    venue = delatex(venue)
    for name, pattern in _VENUE_RES:
        if pattern.search(venue):
            return name
    return None
