"""
    Renders a tailored resume (contact + selected CV sections) to LaTeX.

    All layout lives here, so the model never writes LaTeX. Every piece of CV text passes through
    latex_escape(); headings are the user's own, and each section's layout is chosen by its kind.
"""

from .schemas import LIST_KINDS, Contact, CVEntry, CVSection

# glyphtounicode + \pdfgentounicode map ligatures ("fi", "fl") to real characters in the PDF's
# text layer, so applicant tracking systems read "profile" rather than "prole". The default OT1
# encoding is kept on purpose: T1 without the cm-super/lmodern packages (not in the image) falls
# back to bitmap fonts.
PREAMBLE = r"""\documentclass[10pt]{article}
\usepackage[utf8]{inputenc}
\input{glyphtounicode}
\pdfgentounicode=1
\usepackage[margin=0.6in]{geometry}
\usepackage{titlesec}
\usepackage{enumitem}
\usepackage[hidelinks]{hyperref}
\pagenumbering{gobble}
\setlength{\parindent}{0pt}
\setlength{\parskip}{0pt}
\titleformat{\section}{\large\bfseries}{}{0em}{}[\titlerule]
\titlespacing*{\section}{0pt}{8pt}{4pt}
\setlist[itemize]{leftmargin=1.3em, topsep=1pt, itemsep=1pt, parsep=0pt, partopsep=0pt}
"""

# Characters with special meaning in LaTeX, and common non-ASCII characters that the T1/utf8
# setup does not map on its own.
_ESCAPES = {
    "\\": r"\textbackslash{}",
    "&": r"\&",
    "%": r"\%",
    "$": r"\$",
    "#": r"\#",
    "_": r"\_",
    "{": r"\{",
    "}": r"\}",
    "~": r"\textasciitilde{}",
    "^": r"\textasciicircum{}",
    "<": r"\textless{}",
    ">": r"\textgreater{}",
    "|": r"\textbar{}",
    "×": r"$\times$",
    "→": r"$\rightarrow$",
    "←": r"$\leftarrow$",
    "≥": r"$\geq$",
    "≤": r"$\leq$",
    "•": r"\textbullet{}",
    "·": r"\textperiodcentered{}",
    "…": r"\ldots{}",
    "€": r"EUR",
    "™": r"\texttrademark{}",
    "©": r"\textcopyright{}",
    "®": r"\textregistered{}",
    "\u00a0": "~",
    "\t": " ",
}

# Typographic punctuation the utf8 input encoding handles natively.
_PASS_THROUGH = set("–—‘’“”")


def latex_escape(text):
    """
        Desc:
            Escapes CV text for LaTeX. Characters pdflatex cannot typeset with the default fonts
            (for example CJK or emoji) are dropped rather than breaking the compile.
    """
    out = []
    for ch in str(text or ""):
        if ch in _ESCAPES:
            out.append(_ESCAPES[ch])
        elif ch in _PASS_THROUGH or ch == "\n" or (" " <= ch and ord(ch) < 0x250):
            out.append(" " if ch == "\n" else ch)
        # anything else is dropped
    return "".join(out)


def render_resume(contact: Contact, sections: list[CVSection]) -> str:
    """
        Desc:
            Builds a complete LaTeX document from the contact block and the sections to print.
        Args:
            contact (Contact): Copied from the master CV.
            sections (list[CVSection]): Output of resume_model.check_selection, in print order.
        Returns:
            str: LaTeX source.
    """
    parts = [PREAMBLE, "\\begin{document}\n", _header(contact)]
    for section in sections:
        body = _section_body(section)
        if body:
            parts.append(f"\\section*{{{latex_escape(section.heading)}}}\n{body}")
    parts.append("\\end{document}\n")
    return "\n".join(parts)


def _header(contact):
    details = [contact.location, contact.phone, contact.email, *contact.links]
    details = [latex_escape(d) for d in details if d and d.strip()]
    lines = ["\\begin{center}"]
    if contact.name.strip():
        lines.append(f"{{\\LARGE\\textbf{{{latex_escape(contact.name)}}}}}\\par\\vspace{{2pt}}")
    if details:
        lines.append("{\\small " + " \\textbar{} ".join(details) + "}\\par")
    lines.append("\\end{center}")
    return "\n".join(lines) + "\n"


def _section_body(section):
    if section.kind == "summary":
        text = " ".join(b.text for e in section.entries for b in e.bullets)
        if not text:
            text = " ".join(e.title for e in section.entries if e.title)
        return f"{latex_escape(text)}\\par\n" if text else ""
    if section.kind in LIST_KINDS:
        return "".join(_list_line(e) for e in section.entries)
    return "\\vspace{2pt}\n".join(_entry_block(e) for e in section.entries)


def _list_line(entry):
    items = ", ".join(latex_escape(b.text) for b in entry.bullets)
    title = latex_escape(entry.title)
    if title and items:
        return f"\\textbf{{{title}:}} {items}\\par\n"
    return f"{title or items}\\par\n" if (title or items) else ""


def _entry_block(entry: CVEntry):
    head = f"\\textbf{{{latex_escape(entry.title)}}}" if entry.title else ""
    if entry.organization:
        org = latex_escape(entry.organization)
        head = f"{head} --- {org}" if head else f"\\textbf{{{org}}}"
    if entry.dates:
        head += f"\\hfill {latex_escape(entry.dates)}"
    lines = [head + "\\par"] if head else []

    sub = f"\\textit{{{latex_escape(entry.tech)}}}" if entry.tech else ""
    if entry.location:
        sub += f"\\hfill {latex_escape(entry.location)}"
    if sub:
        lines.append("{\\small " + sub + "}\\par")

    if entry.bullets:
        items = "\n".join(f"  \\item {latex_escape(b.text)}" for b in entry.bullets)
        lines.append(f"\\begin{{itemize}}\n{items}\n\\end{{itemize}}")
    return "\n".join(lines) + "\n"
