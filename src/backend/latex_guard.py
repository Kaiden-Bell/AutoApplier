"""
    Sanity and denylist checks applied to model-generated LaTeX before it reaches pdflatex.

    This is one layer of defense; compilation also runs with shell escape disabled, paranoid
    file access, a timeout, and a per-request temporary directory (see compile_adapter.py).
"""

import re

# TeX control words end at the first non-letter, so "\read16" is \read followed by 16.
_END = r"(?![A-Za-z])"

MAX_LATEX_CHARS = 40_000

_DENYLIST = [
    (re.compile(r"\\write18"), r"\write18"),
    (re.compile(r"\\immediate" + _END), r"\immediate"),
    (re.compile(r"\\openout" + _END), r"\openout"),
    (re.compile(r"\\openin" + _END), r"\openin"),
    (re.compile(r"\\read" + _END), r"\read"),
    (re.compile(r"\\(input|include)\s*\{?\s*\|"), r"\input{|...}"),
    (re.compile(r"\\special" + _END), r"\special"),
    (re.compile(r"\\catcode" + _END), r"\catcode"),
    (re.compile(r"\\scantokens" + _END), r"\scantokens"),
    (re.compile(r"\\directlua" + _END), r"\directlua"),
    (re.compile(r"\\(usepackage|RequirePackage)\s*(\[[^\]]*\])?\s*\{[^}]*\b(shellesc|verbatimwrite)\b"),
     "shell/file-writing packages"),
]


def check_latex(source):
    """
        Desc:
            Validates model-generated LaTeX source.
        Args:
            source (str): LaTeX document text.
        Returns:
            list[str]: Problems found; an empty list means the source may be compiled.
    """
    problems = []
    if not source or not source.strip():
        return ["LaTeX source is empty."]

    if len(source) > MAX_LATEX_CHARS:
        problems.append(f"LaTeX source is longer than {MAX_LATEX_CHARS} characters.")

    if "\\documentclass" not in source:
        problems.append("Missing \\documentclass.")
    begins = source.count("\\begin{document}")
    ends = source.count("\\end{document}")
    if begins != 1 or ends != 1:
        problems.append("Expected exactly one \\begin{document} and one \\end{document} "
                        "(the output may be truncated).")
    elif source.index("\\begin{document}") > source.index("\\end{document}"):
        problems.append("\\end{document} appears before \\begin{document}.")

    for pattern, label in _DENYLIST:
        if pattern.search(source):
            problems.append(f"Disallowed LaTeX primitive: {label}")

    return problems
