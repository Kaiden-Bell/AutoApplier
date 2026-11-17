"""
    API adapter around src/compiler.py: compiles untrusted LaTeX text in a per-request
    temporary directory and returns PDF bytes plus a log excerpt.
"""

import os
import subprocess
import tempfile

from compiler import compile_latex_to_pdf

LOG_TAIL_CHARS = 4_000


def _read_log_tail(log_path):
    try:
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError:
        return ""
    return text[-LOG_TAIL_CHARS:]


def compile_latex_source(latex_source, timeout=30):
    """
        Desc:
            Compiles LaTeX text to a PDF with restricted pdflatex in a temporary directory.
        Args:
            latex_source (str): Already-validated LaTeX document (see latex_guard.check_latex).
            timeout (float): Hard pdflatex time limit in seconds.
        Returns:
            tuple: (bytes | None, str) the PDF bytes (None on failure) and the compiler log tail.
    """
    with tempfile.TemporaryDirectory(prefix="autoapplier-") as tmp:
        tex_path = os.path.join(tmp, "resume.tex")
        log_dir = os.path.join(tmp, "build")
        pdf_dir = os.path.join(tmp, "pdf")
        with open(tex_path, "w", encoding="utf-8") as f:
            f.write(latex_source)

        log_path = os.path.join(log_dir, "resume.log")
        try:
            pdf_path = compile_latex_to_pdf(
                tex_path, log_dir, pdf_dir,
                timeout=timeout, restricted=True, raise_on_error=True,
            )
        except subprocess.TimeoutExpired:
            return None, (_read_log_tail(log_path) + f"\n\n[pdflatex timed out after {timeout}s]").strip()
        except (subprocess.CalledProcessError, RuntimeError, OSError) as e:
            tail = _read_log_tail(log_path)
            return None, tail or f"pdflatex failed: {e}"

        with open(pdf_path, "rb") as f:
            pdf_bytes = f.read()
        return pdf_bytes, _read_log_tail(log_path)
