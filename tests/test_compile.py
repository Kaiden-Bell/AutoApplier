"""Real pdflatex tests. Skipped when pdflatex is not installed; CI and the Docker image have it."""

import shutil

import pytest

from backend.compile_adapter import compile_latex_source

pytestmark = pytest.mark.skipif(shutil.which("pdflatex") is None, reason="pdflatex not installed")

MINIMAL = r"""\documentclass[10pt]{article}
\usepackage[margin=0.7in]{geometry}
\usepackage{titlesec}
\usepackage{enumitem}
\usepackage[hidelinks]{hyperref}
\begin{document}
\section*{Experience}
\begin{itemize}
  \item Built a \textbf{FastAPI} proxy.
\end{itemize}
\end{document}
"""


def test_minimal_document_compiles():
    pdf, log = compile_latex_source(MINIMAL, timeout=60)
    assert pdf is not None, log
    assert pdf.startswith(b"%PDF")


def test_broken_document_returns_log():
    pdf, log = compile_latex_source(MINIMAL.replace(r"\textbf{FastAPI}", r"\notacommand"), timeout=60)
    assert pdf is None
    assert "Undefined control sequence" in log


def test_absolute_input_paths_are_blocked():
    # /etc/hostname exists and is readable; unrestricted pdflatex would include it.
    # openin_any=p makes kpathsea refuse absolute paths, so LaTeX reports it as missing.
    source = MINIMAL.replace(r"\section*{Experience}", r"\input{/etc/hostname}")
    pdf, log = compile_latex_source(source, timeout=60)
    assert pdf is None
    assert "/etc/hostname.tex' not found" in log
