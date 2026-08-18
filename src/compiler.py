"""
    Orchestrates LaTeX to PDF compilation process. 
"""

import subprocess
import os
import shutil

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def compile_latex_to_pdf(file, log_dir=None, pdf_dir=None):
    """
        Desc: 
            Compile LaTeX file to PDF using pdflatex            
        Args:
            file (str): Path to the LaTeX file.
            log_dir (str): Path to the log directory.
            pdf_dir (str): Path to the PDF directory.
        Returns:
            None
    """
    
    if log_dir is None:
        log_dir = os.path.join(BASE_DIR, "output", "logs", "LaTeX_logs")
    if pdf_dir is None:
        pdf_dir = os.path.join(BASE_DIR, "output", "pdfs")

    os.makedirs(log_dir, exist_ok=True)
    os.makedirs(pdf_dir, exist_ok=True)

    if not os.path.isabs(file):
        file = os.path.join(BASE_DIR, file)

    pdflatex_cmd = shutil.which('pdflatex')
    if not pdflatex_cmd:
        pdflatex_cmd = '/usr/bin/pdflatex' if os.name != 'nt' else 'pdflatex'

    try:
        subprocess.run([pdflatex_cmd, '-interaction=nonstopmode', f'-output-directory={log_dir}', file], 
                      check=True, capture_output=True)
        
        base_name = os.path.splitext(os.path.basename(file))[0]
        generated_pdf = os.path.join(log_dir, f"{base_name}.pdf")
        target_pdf = os.path.join(pdf_dir, f"{base_name}.pdf")

        if os.path.exists(generated_pdf):
            shutil.move(generated_pdf, target_pdf)
            print("Success!")
        else:
            print("Compilation Finished, unable to move file")
    except subprocess.CalledProcessError as e:
        print(f"Error compiling LaTeX: {e}")

if __name__ == "__main__":
    file = 'cv.tex'
    compile_latex_to_pdf(file)





