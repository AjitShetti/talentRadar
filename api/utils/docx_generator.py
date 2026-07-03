"""
api/utils/docx_generator.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~
Utility to convert structured/markdown resume content into a .docx file.
"""

from io import BytesIO
from docx import Document
from docx.shared import Pt, Inches

def generate_resume_docx(tailored_resume_text: str) -> BytesIO:
    """
    Generates an in-memory .docx file from the tailored resume text.
    For simplicity, this treats the text as plain text with line breaks,
    but it can be expanded to parse Markdown and apply proper formatting.
    """
    document = Document()

    # Set up basic styles
    style = document.styles['Normal']
    font = style.font
    font.name = 'Calibri'
    font.size = Pt(11)

    # Add content
    # A simple approach: split by double newlines to form paragraphs
    paragraphs = tailored_resume_text.split('\n\n')
    
    for p_text in paragraphs:
        p_text = p_text.strip()
        if not p_text:
            continue
            
        # Basic markdown-ish parsing for bold headers (e.g., "## Education")
        if p_text.startswith('#'):
            clean_text = p_text.lstrip('#').strip()
            heading = document.add_heading(clean_text, level=2)
            heading.style.font.name = 'Calibri'
            heading.style.font.size = Pt(14)
        else:
            # Handle bullet points
            lines = p_text.split('\n')
            for line in lines:
                line = line.strip()
                if line.startswith('- ') or line.startswith('* '):
                    clean_line = line[2:].strip()
                    document.add_paragraph(clean_line, style='List Bullet')
                else:
                    if line:
                        document.add_paragraph(line)

    # Adjust margins to fit more content (typical for resumes)
    sections = document.sections
    for section in sections:
        section.top_margin = Inches(0.5)
        section.bottom_margin = Inches(0.5)
        section.left_margin = Inches(0.5)
        section.right_margin = Inches(0.5)

    # Save to in-memory buffer
    file_stream = BytesIO()
    document.save(file_stream)
    file_stream.seek(0)
    
    return file_stream
