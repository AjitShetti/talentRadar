import os
import tempfile
import subprocess
import logging

logger = logging.getLogger(__name__)

def compile_latex_to_pdf(latex_content: str) -> bytes:
    """
    Takes a LaTeX string, writes it to a temporary file,
    compiles it using pdflatex, and returns the PDF bytes.
    """
    with tempfile.TemporaryDirectory() as temp_dir:
        tex_path = os.path.join(temp_dir, "resume.tex")
        pdf_path = os.path.join(temp_dir, "resume.pdf")

        with open(tex_path, "w", encoding="utf-8") as f:
            f.write(latex_content)

        try:
            # Run pdflatex twice to resolve references/formatting properly
            for _ in range(2):
                result = subprocess.run(
                    ["pdflatex", "-interaction=nonstopmode", "-halt-on-error", "resume.tex"],
                    cwd=temp_dir,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    check=False
                )
                if result.returncode != 0:
                    logger.error("pdflatex compilation failed")
                    logger.error(result.stdout.decode('utf-8', errors='ignore'))
                    logger.error(result.stderr.decode('utf-8', errors='ignore'))
                    raise RuntimeError("Failed to compile LaTeX to PDF")
            
            with open(pdf_path, "rb") as f:
                pdf_bytes = f.read()
                return pdf_bytes

        except Exception as e:
            logger.exception("Error during LaTeX compilation")
            raise e
