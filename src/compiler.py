"""
    Orchestrates LaTeX to PDF compilation process. 
"""

import subprocess
import os
import shutil

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def compile_latex_to_pdf(
    file, log_dir=None, pdf_dir=None, *, timeout=None, restricted=False,
    raise_on_error=False,
):
    """
        Desc: 
            Compile LaTeX file to PDF using pdflatex            
        Args:
            file (str): Path to the LaTeX file.
            log_dir (str): Path to the log directory.
            pdf_dir (str): Path to the PDF directory.
            timeout (float | None): Optional subprocess time limit, used by the API.
            restricted (bool): Disable shell escape and restrict TeX file access for API inputs.
            raise_on_error (bool): Let the API handle compilation failures instead of printing.
        Returns:
            str | None: Generated PDF path, or None after a reported CLI failure.
    """
    
    if log_dir is None:
        log_dir = os.path.join(BASE_DIR, "output", "logs", "LaTeX_logs")
    if pdf_dir is None:
        pdf_dir = os.path.join(BASE_DIR, "output", "pdfs")

    # Resolve caller-relative output paths before the API changes the subprocess cwd.
    log_dir = os.path.abspath(log_dir)
    pdf_dir = os.path.abspath(pdf_dir)
    os.makedirs(log_dir, exist_ok=True)
    os.makedirs(pdf_dir, exist_ok=True)

    if not os.path.isabs(file):
        file = os.path.join(BASE_DIR, file)
    file = os.path.abspath(file)

    pdflatex_cmd = shutil.which('pdflatex')
    if not pdflatex_cmd:
        pdflatex_cmd = '/usr/bin/pdflatex' if os.name != 'nt' else 'pdflatex'

    cmd = [pdflatex_cmd, '-interaction=nonstopmode', f'-output-directory={log_dir}']
    cwd = None
    env = None
    if restricted:
        # openin_any=p rejects absolute input filenames. The file is already in the
        # API's temporary directory, so select that directory and pass its basename.
        cwd = os.path.dirname(file)
        env = {
            **os.environ,
            "openout_any": "p",
            "openin_any": "p",
            "shell_escape": "f",
            "TEXMFVAR": log_dir,
        }
        cmd.extend(['-no-shell-escape', '-halt-on-error'])
        cmd.append(os.path.basename(file))
    else:
        cmd.append(file)

    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=timeout, cwd=cwd, env=env)
        
        base_name = os.path.splitext(os.path.basename(file))[0]
        generated_pdf = os.path.join(log_dir, f"{base_name}.pdf")
        target_pdf = os.path.join(pdf_dir, f"{base_name}.pdf")

        if os.path.exists(generated_pdf):
            shutil.move(generated_pdf, target_pdf)
            print("Success!")
            return target_pdf
        else:
            if raise_on_error:
                raise RuntimeError("pdflatex did not produce a PDF.")
            print("Compilation Finished, unable to move file")
    except subprocess.CalledProcessError as e:
        if raise_on_error:
            raise
        print(f"Error compiling LaTeX: {e}")

if __name__ == "__main__":
    file = 'cv.tex'
    compile_latex_to_pdf(file)




