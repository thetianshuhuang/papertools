"""Text and math conversion tests."""

import pytest


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        (r"``quoted'' and `single' don't", "“quoted” and ‘single’ don’t"),
        ("pages 1--2 --- or -1", "pages 1–2 — or -1"),
        (
            r"Caf\'e, na\"{\i}ve, Fran\c cois, \L{}ukasz",
            "Café, naïve, François, Łukasz",
        ),
        (r"50\% \& \$5 \#1 a\_b", "50% & $5 #1 a_b"),
        (r"Fig.~1 and \ldots{} more", "Fig. 1 and … more"),
        (r"\S 3, \LaTeX, 25\textdegree C", "§3, LaTeX, 25°C"),
        (
            r"\textbf{bold} \emph{em} {\bf bf} \textcolor{red}{red}",
            "bold em bf red",
        ),
        (
            r"\href{https://x.org}{site} \url{https://x.org/a_b~c}",
            "site https://x.org/a_b~c",
        ),
        (r"two\\lines\newline here", "two lines here"),
    ],
)
def test_text(convert, body, expected):
    """Convert TeX text conventions to Unicode."""
    assert convert(body) == (expected, [])


def test_footnotes_and_labels_dropped(convert):
    """Drop footnotes, labels, and spacing commands with their arguments."""
    body = r"Data.\footnote{Note.} More\label{x}\vspace*{2pt} text."
    assert convert(body) == ("Data. More text.", [])


def test_comments(convert):
    """Remove comments but keep escaped percent signs."""
    body = "Up 5\\% % a comment\nhere, joined%\n   up."
    assert convert(body)[0] == "Up 5% here, joinedup."


def test_paragraphs(convert):
    """Keep paragraph breaks, joining each paragraph into one line."""
    body = "First\nparagraph.\n\n  \n Second\\par Third."
    assert convert(body)[0] == "First paragraph.\n\nSecond\n\nThird."


def test_citations_and_references_warn(convert):
    """Drop citations with the space before them, and mark references."""
    text, warnings = convert(r"as shown~\cite{a,b}. See Sec.~\ref{sec:x}.")
    assert text == "as shown. See Sec. ??."
    assert warnings == [
        r"dropped citation \cite{a,b}",
        r"\ref{sec:x} printed as ??",
    ]


def test_unknown_macros_warn_once(convert):
    """Drop unknown macros but keep their argument, warning once each."""
    text, warnings = convert(r"\foo{kept} and \foo{again}")
    assert text == "kept and again"
    assert warnings == [r"unknown macro \foo; dropped"]


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        (r"$2\times$ faster", "2× faster"),
        (r"$2 \times 3$", "2 × 3"),
        (r"$\alpha^{2}$ and $x_i$", "α² and xᵢ"),
        (r"$10^{-3}$ s", "10⁻³ s"),
        (r"$90^\circ$ and $90^{\circ}$", "90° and 90°"),
        (r"$x^{a+b}$", "x^(a + b)"),
        (r"$a \le -b$", "a ≤ -b"),
        (r"$\sim$10 and $>$5", "~10 and >5"),
        (r"$\frac{1}{2}$ $\frac{a+b}{c}$", "1/2 (a + b)/c"),
        (r"$\mathbb{R}^3$, $\log n$", "ℝ³, log n"),
        (r"$100\,\mathrm{ms}$, $\text{a b}$", "100 ms, a b"),
        (r"\(x = 1\)", "x = 1"),
        (r"$f(x, y)$", "f(x, y)"),
    ],
)
def test_math_unicode(convert, body, expected):
    """Convert simple math to Unicode."""
    assert convert(body) == (expected, [])


def test_math_tex(convert):
    """Keep math as TeX, with the paper's macros expanded."""
    body = r"$2\times$ and $\ex[x]$ and \(\alpha\)"
    text, warnings = convert(body, r"\newcommand{\ex}{\mathbb{E}}", "tex")
    assert text == r"$2\times$ and $\mathbb{E}[x]$ and $\alpha$"
    assert warnings == []


def test_unknown_math_macro_warns(convert):
    """Warn about math commands with no Unicode equivalent."""
    text, warnings = convert(r"$a \oslash b$")
    assert text == "ab"
    assert warnings == [r"unknown macro \oslash in math; dropped"]
