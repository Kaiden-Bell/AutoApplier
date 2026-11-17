"""LaTeX documents styled like a real one-page resume, for compile/page-fit tests."""

PREAMBLE = r"""\documentclass[10pt]{article}
\usepackage[margin=0.7in]{geometry}
\usepackage{titlesec}
\usepackage{enumitem}
\usepackage[hidelinks]{hyperref}
\usepackage{parskip}
\pagenumbering{gobble}
\titleformat{\section}{\large\bfseries}{}{0em}{}[\titlerule]
\setlist[itemize]{topsep=2pt, itemsep=3pt, parsep=0pt}
"""

BULLET = (r"\item Built and maintained containerized services with Docker and FastAPI, "
          r"improving reliability and deployment speed across several self-hosted applications.")


def resume(bullets):
    """A resume with `bullets` two-line bullets split across sections."""
    sections = []
    per_section = 6
    for i in range(0, bullets, per_section):
        n = min(per_section, bullets - i)
        items = "\n".join(BULLET for _ in range(n))
        sections.append(f"\\section*{{Section {i // per_section + 1}}}\n"
                        f"\\textbf{{Project}} \\hfill 2025\n\\begin{{itemize}}\n{items}\n\\end{{itemize}}")
    body = "\n\n".join(sections)
    return (PREAMBLE + "\\begin{document}\n\\begin{center}{\\Huge \\textbf{Test Candidate}}\\\\[6pt]"
            "Reno, NV | test@example.com\\end{center}\n" + body + "\n\\end{document}\n")
