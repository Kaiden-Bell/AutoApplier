"""
Date: 07/09
Author: Kaiden Bell

Description: Complies LaTex to PDF using Python processes. 
"""

import subprocess
import os


def compile_latex_to_pdf(file):
    """Compile LaTeX file to PDF using pdflatex command."""
    try:
        subprocess.run(['pdflatex', '-interaction=nonstopmode', file], 
                      check=True, capture_output=True)
    except subprocess.CalledProcessError as e:
        print(f"Error compiling LaTeX: {e}")


if __name__ == "__main__":
    file = 'cv.tex'
    compile_latex_to_pdf(file)





