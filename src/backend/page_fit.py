"""
    Enforces the one-page rule deterministically after compilation.

    The model cannot see how long its LaTeX renders, so the prompt's "one page" request is only
    a target. If the PDF is longer, this module recompiles with progressively tighter layout
    (margins, list spacing, font size) injected at the end of the preamble. It never changes
    wording or removes content: no regeneration, per DESIGN.md §8.4.
"""

import re

from . import compile_adapter

_PAGES_RE = re.compile(r"Output written on .*?\((\d+)\s+pages?", re.DOTALL)

# Each level is a complete preamble addition; later levels are tighter. Reloading geometry,
# enumitem or titlesec without options is harmless if the document already loads them.
_LAYOUT_LEVELS = [
    (
        "reduced margins and list spacing",
        r"""
\usepackage{geometry}\geometry{margin=0.5in}
\usepackage{enumitem}\setlist{itemsep=0pt,parsep=0pt,topsep=2pt,partopsep=0pt}\setlist[itemize]{itemsep=0pt,parsep=0pt,topsep=2pt,partopsep=0pt}\setlist[enumerate]{itemsep=0pt,parsep=0pt,topsep=2pt,partopsep=0pt}
\setlength{\parskip}{2pt}
""",
    ),
    (
        "reduced margins, spacing and section gaps; 9.5pt text",
        r"""
\usepackage{geometry}\geometry{margin=0.45in}
\usepackage{enumitem}\setlist{itemsep=0pt,parsep=0pt,topsep=1pt,partopsep=0pt}\setlist[itemize]{itemsep=0pt,parsep=0pt,topsep=1pt,partopsep=0pt}\setlist[enumerate]{itemsep=0pt,parsep=0pt,topsep=1pt,partopsep=0pt}
\setlength{\parskip}{1pt}
\usepackage{titlesec}\titlespacing*{\section}{0pt}{6pt}{3pt}
\makeatletter\renewcommand\normalsize{\@setfontsize\normalsize{9.5}{11.2}}\makeatother
""",
    ),
    (
        "minimum margins and spacing; 9pt text",
        r"""
\usepackage{geometry}\geometry{margin=0.4in}
\usepackage{enumitem}\setlist{itemsep=0pt,parsep=0pt,topsep=0pt,partopsep=0pt}\setlist[itemize]{itemsep=0pt,parsep=0pt,topsep=0pt,partopsep=0pt}\setlist[enumerate]{itemsep=0pt,parsep=0pt,topsep=0pt,partopsep=0pt}
\setlength{\parskip}{0pt}
\usepackage{titlesec}\titlespacing*{\section}{0pt}{4pt}{2pt}
\makeatletter\renewcommand\normalsize{\@setfontsize\normalsize{9}{10.6}}\makeatother
\linespread{0.97}
""",
    ),
]


def count_pages(log):
    """Page count from the pdflatex log, or None if it cannot be determined."""
    match = _PAGES_RE.search(log or "")
    return int(match.group(1)) if match else None


def with_layout(latex_source, preamble_addition):
    marker = "\\begin{document}"
    index = latex_source.rfind(marker)
    if index == -1:
        return latex_source
    block = "% --- AutoApplier one-page layout adjustment ---" + preamble_addition + "\\normalsize\n"
    return latex_source[:index] + block + latex_source[index:]


def compile_one_page(latex_source, timeout=30):
    """
        Desc:
            Compiles the LaTeX and, if it runs past one page, retries with tighter layouts.
        Returns:
            dict: pdf (bytes | None), log (str), pages (int | None), latex (the source that
            produced the returned PDF), adjustment (str, "" if the original layout was kept).
    """
    pdf, log = compile_adapter.compile_latex_source(latex_source, timeout=timeout)
    best = {"pdf": pdf, "log": log, "pages": count_pages(log), "latex": latex_source, "adjustment": ""}
    if pdf is None or best["pages"] is None or best["pages"] <= 1:
        return best

    for description, addition in _LAYOUT_LEVELS:
        candidate = with_layout(latex_source, addition)
        pdf, log = compile_adapter.compile_latex_source(candidate, timeout=timeout)
        pages = count_pages(log)
        if pdf is None or pages is None:
            continue  # this tweak broke the document; keep the last good result
        best = {"pdf": pdf, "log": log, "pages": pages, "latex": candidate, "adjustment": description}
        if pages <= 1:
            break
    return best
