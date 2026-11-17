import pytest

from backend.latex_guard import MAX_LATEX_CHARS, check_latex

VALID = r"""\documentclass{article}
\begin{document}
Hello \textbf{world}.
\end{document}
"""


def test_valid_document_passes():
    assert check_latex(VALID) == []


@pytest.mark.parametrize("source", [
    "",
    r"\begin{document}x\end{document}",                       # no \documentclass
    "\\documentclass{article}\n\\begin{document}\ntruncated",  # no \end{document}
    "\\documentclass{article}\n\\end{document}\\begin{document}",
    VALID + "x" * MAX_LATEX_CHARS,
])
def test_structural_problems(source):
    assert check_latex(source)


@pytest.mark.parametrize("snippet", [
    r"\write18{rm -rf /}",
    r"\immediate\write\out{x}",
    r"\newwrite\f \openout\f=x.txt",
    r"\newread\f \openin\f=/etc/passwd",
    r"\read16 to \x",
    r"\input{|ls}",
    r"\input|ls",
    r"\special{x}",
    r"\catcode`\@=11",
    r"\scantokens{x}",
    r"\directlua{os.execute('x')}",
    r"\usepackage{shellesc}",
])
def test_denylisted_primitives(snippet):
    source = VALID.replace("Hello", snippet + " Hello")
    assert any("Disallowed" in p for p in check_latex(source))
