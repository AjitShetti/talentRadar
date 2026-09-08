"""
api/utils/latex_compiler.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~
Compile a LaTeX document to PDF bytes.

Security note
-------------
``latex_content`` reaching this module is *LLM output derived from
user-supplied resume text*, so it must be treated as hostile input. A plain
``pdflatex`` invocation would let that input read arbitrary server files
(``\\input{/etc/passwd}``) and embed them in the PDF handed back to the
caller, or hang the container forever with a recursive macro. Three
mitigations, all required:

* ``-no-shell-escape`` — disables ``\\write18`` shell-outs entirely.
* ``openin_any=p`` / ``openout_any=p`` — restrict TeX file IO to the working
  directory, so ``\\input`` cannot escape the temp dir.
* ``timeout`` on the subprocess — a runaway macro is killed rather than
  pinning a CPU for the life of the process.

``compile_latex_to_pdf`` is blocking (it shells out). Call it from async code
via :func:`compile_latex_to_pdf_async`, never directly, or it stalls the whole
event loop for every other user.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile

from fastapi.concurrency import run_in_threadpool

from config.settings import get_settings

logger = logging.getLogger(__name__)

#: Hard ceiling on a single pdflatex pass. Two passes run, so the worst case
#: is roughly double this before the caller gets an error.
COMPILE_TIMEOUT_SECONDS = 20


class LatexCompileError(RuntimeError):
    """Raised when the document could not be turned into a PDF."""


def _restricted_env() -> dict[str, str]:
    """Environment that confines TeX's file IO to the compile directory."""
    env = dict(os.environ)
    # 'p' = paranoid: no absolute paths, no parent-directory traversal, and no
    # dotfiles. This is what stops \input{/etc/passwd} from resolving.
    env["openin_any"] = "p"
    env["openout_any"] = "p"
    # Never let a document pull in a $TEXINPUTS path we did not choose.
    env.pop("TEXINPUTS", None)
    return env


def _render_without_latex(latex_content: str) -> bytes:
    """Render through PyMuPDF, translating its failure into ours.

    Callers only ever catch :class:`LatexCompileError`, and three of the four
    treat it as "return the LaTeX source instead" — so the fallback engine's
    failures have to arrive as the same type or they surface as a 500.
    """
    from api.utils.pdf_renderer import PdfRenderError, render_latex_pdf

    try:
        return render_latex_pdf(latex_content)
    except PdfRenderError as exc:
        raise LatexCompileError(str(exc)) from exc


def compile_latex_to_pdf(latex_content: str) -> bytes:
    """
    Compile ``latex_content`` and return the resulting PDF bytes.

    Which engine runs is decided by ``PDF_ENGINE``:

    ``auto`` (default)
        pdflatex when it is on PATH, otherwise the built-in PyMuPDF renderer
        in :mod:`api.utils.pdf_renderer`. This is what lets the image ship
        without a TeX Live install — over a gigabyte, for three style files —
        while still producing a real PDF on a free host.
    ``latex``
        pdflatex only; fail if it is missing.
    ``builtin``
        the PyMuPDF renderer only, even where TeX exists.

    Raises
    ------
    LatexCompileError
        If the selected engine is unavailable, times out, or produces no PDF.
    """
    if not latex_content or not latex_content.strip():
        raise LatexCompileError("No LaTeX content to compile.")

    engine = get_settings().pdf_engine
    have_pdflatex = shutil.which("pdflatex") is not None

    if engine == "builtin" or (engine == "auto" and not have_pdflatex):
        return _render_without_latex(latex_content)

    if not have_pdflatex:
        # PDF_ENGINE=latex was asked for explicitly and TeX is not here.
        # A deployment-time problem, not a user error — say so plainly so the
        # caller can degrade to returning the .tex source instead of 500-ing.
        raise LatexCompileError(
            "pdflatex is not installed on this server, so PDF export is unavailable."
        )

    with tempfile.TemporaryDirectory() as temp_dir:
        tex_path = os.path.join(temp_dir, "resume.tex")
        pdf_path = os.path.join(temp_dir, "resume.pdf")

        with open(tex_path, "w", encoding="utf-8") as handle:
            handle.write(latex_content)

        env = _restricted_env()

        # Two passes so references/spacing settle on the second run.
        for attempt in range(2):
            try:
                result = subprocess.run(
                    [
                        "pdflatex",
                        "-no-shell-escape",
                        "-interaction=nonstopmode",
                        "-halt-on-error",
                        "-output-directory", temp_dir,
                        "resume.tex",
                    ],
                    cwd=temp_dir,
                    env=env,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    timeout=COMPILE_TIMEOUT_SECONDS,
                    check=False,
                )
            except subprocess.TimeoutExpired as exc:
                logger.error("pdflatex timed out after %ss on pass %d", COMPILE_TIMEOUT_SECONDS, attempt + 1)
                raise LatexCompileError(
                    "The document took too long to render. Simplify it and try again."
                ) from exc

            if result.returncode != 0:
                # The log carries the TeX error; the caller gets a generic
                # message because the log can quote server paths.
                logger.error(
                    "pdflatex failed (pass %d, rc=%d):\n%s",
                    attempt + 1,
                    result.returncode,
                    result.stdout.decode("utf-8", errors="ignore")[-4000:],
                )
                raise LatexCompileError("The resume could not be rendered as a PDF.")

        if not os.path.exists(pdf_path):
            logger.error("pdflatex reported success but produced no PDF")
            raise LatexCompileError("The resume could not be rendered as a PDF.")

        with open(pdf_path, "rb") as handle:
            return handle.read()


async def compile_latex_to_pdf_async(latex_content: str) -> bytes:
    """Async-safe wrapper — runs the blocking compile off the event loop."""
    return await run_in_threadpool(compile_latex_to_pdf, latex_content)
