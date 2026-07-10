"""
Date: 07/09
Author: Kaiden Bell

Description: Complies LaTex to PDF using Python processes. 
"""

import subprocess
import os
import shutil

def compile_latex_to_pdf(file, log_dir="output/logs/LaTeX_logs", pdf_dir="output/pdfs"):
    """Compile LaTeX file to PDF using pdflatex"""

    os.makedirs(log_dir, exist_ok=True)
    os.makedirs(pdf_dir, exist_ok=True)

    try:
        subprocess.run(['/usr/bin/pdflatex', '-interaction=nonstopmode',f'-output-directory={log_dir}', file], 
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





