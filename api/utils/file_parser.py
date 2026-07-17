"""
api/utils/file_parser.py
~~~~~~~~~~~~~~~~~~~~~~~
Utility to extract text from uploaded PDF and DOCX files.
"""

import io
import logging

try:
    import fitz  # PyMuPDF
except ImportError:
    fitz = None

try:
    import docx
except ImportError:
    docx = None

logger = logging.getLogger(__name__)


def extract_text_from_bytes(file_bytes: bytes, filename: str) -> str:
    """Extract text from a PDF or DOCX file represented as bytes."""
    filename_lower = filename.lower()
    
    if filename_lower.endswith(".pdf"):
        if fitz is None:
            raise RuntimeError("PyMuPDF (fitz) is not installed. Cannot parse PDF.")
        try:
            doc = fitz.open(stream=file_bytes, filetype="pdf")
            text_blocks = []
            for page in doc:
                text_blocks.append(page.get_text())
            return "\n".join(text_blocks)
        except Exception as e:
            logger.error("Failed to parse PDF", exc_info=True)
            raise ValueError(f"Could not parse PDF file: {e}") from e

    elif filename_lower.endswith(".docx"):
        if docx is None:
            raise RuntimeError("python-docx is not installed. Cannot parse DOCX.")
        try:
            doc_file = io.BytesIO(file_bytes)
            doc = docx.Document(doc_file)
            return "\n".join([para.text for para in doc.paragraphs])
        except Exception as e:
            logger.error("Failed to parse DOCX", exc_info=True)
            raise ValueError(f"Could not parse DOCX file: {e}") from e

    else:
        # Fallback to plain text decoding
        try:
            return file_bytes.decode("utf-8")
        except UnicodeDecodeError:
            try:
                # Fallback encoding if utf-8 fails
                return file_bytes.decode("latin-1")
            except Exception as e:
                raise ValueError("Unsupported file format and could not decode as text.") from e
