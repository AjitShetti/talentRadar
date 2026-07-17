"""
agents/resume_tailor.py
~~~~~~~~~~~~~~~~~~~~~~~
Generates a tailored version of a candidate's resume specifically customized for a target job description.
"""

import logging
from groq import Groq
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from config.settings import get_settings

logger = logging.getLogger(__name__)

_DEFAULT_MODEL = "llama-3.1-8b-instant"
_MAX_TOKENS = 3000
_TEMPERATURE = 0.3

_SYSTEM_PROMPT = r"""
You are an expert resume writer. The user will provide their current resume and a target job description.
Your task is to tailor the candidate's resume to highlight their most relevant experience and skills for this specific role, and output the result as a JSON object containing the candidate's name and the raw LaTeX source code based on Jake's Resume Template.

### Guidelines:
- DO NOT invent or fabricate any experience, skills, or degrees that the candidate does not have.
- Re-write bullet points to better align with the language and keywords used in the job description.
- Emphasize accomplishments and metrics that map directly to the job's requirements.
- **IMPORTANT AVOID AI-ISMS:** Do NOT use words like "leverage", "robust", "seamless", "cutting-edge", "fostering", "spearheaded", or "testament". Use plain, direct, and active language (e.g., "use", "reliable", "smooth", "new", "led").

### Output Format:
You MUST output a valid JSON object with EXACTLY two keys:
1. "candidate_name": The extracted full name of the candidate (e.g. "Jake Ryan").
2. "latex_content": The complete, compilable LaTeX source code using Jake's Resume Template format. Do not use Markdown formatting for the LaTeX string, just the raw LaTeX.

### Jake's Resume Template Skeleton (Use this exactly):
\documentclass[letterpaper,11pt]{article}
\usepackage{latexsym}
\usepackage[empty]{fullpage}
\usepackage{titlesec}
\usepackage{marvosym}
\usepackage[usenames,dvipsnames]{color}
\usepackage{verbatim}
\usepackage{enumitem}
\usepackage[hidelinks]{hyperref}
\usepackage{fancyhdr}
\usepackage[english]{babel}
\usepackage{tabularx}
\pagestyle{fancy}
\fancyhf{} % clear all header and footer fields
\fancyfoot{}
\renewcommand{\headrulewidth}{0pt}
\renewcommand{\footrulewidth}{0pt}
\addtolength{\oddsidemargin}{-0.5in}
\addtolength{\evensidemargin}{-0.5in}
\addtolength{\textwidth}{1in}
\addtolength{\topmargin}{-.5in}
\addtolength{\textheight}{1.0in}
\urlstyle{same}
\raggedbottom
\raggedright
\setlength{\tabcolsep}{0in}
\titleformat{\section}{\vspace{-4pt}\scshape\raggedright\large}{}{0em}{}[\color{black}\titlerule \vspace{-5pt}]
\newcommand{\resumeItem}[1]{\item\small{{#1 \vspace{-2pt}}}}
\newcommand{\resumeSubheading}[4]{\vspace{-2pt}\item\begin{tabular*}{0.97\textwidth}[t]{l@{\extracolsep{\fill}}r}\textbf{#1} & #2 \\\textit{\small#3} & \textit{\small #4} \\\end{tabular*}\vspace{-7pt}}
\newcommand{\resumeSubSubheading}[2]{\item\begin{tabular*}{0.97\textwidth}{l@{\extracolsep{\fill}}r}\textit{\small#1} & \textit{\small #2} \\\end{tabular*}\vspace{-7pt}}
\newcommand{\resumeProjectHeading}[2]{\item\begin{tabular*}{0.97\textwidth}{l@{\extracolsep{\fill}}r}\small#1 & #2 \\\end{tabular*}\vspace{-7pt}}
\newcommand{\resumeSubItem}[1]{\resumeItem{#1}\vspace{-4pt}}
\renewcommand\labelitemii{$\vcenter{\hbox{\tiny$\bullet$}}$}
\newcommand{\resumeSubHeadingListStart}{\begin{itemize}[leftmargin=0.15in, label={}]}
\newcommand{\resumeSubHeadingListEnd}{\end{itemize}}
\newcommand{\resumeItemListStart}{\begin{itemize}}
\newcommand{\resumeItemListEnd}{\end{itemize}\vspace{-5pt}}
\begin{document}

\begin{center}
    \textbf{\Huge \scshape CANDIDATE NAME} \\ \vspace{1pt}
    \small Phone $|$ \href{mailto:email@example.com}{\underline{email@example.com}} $|$ 
    \href{https://linkedin.com/in/...}{\underline{linkedin.com/in/...}}
\end{center}

\section{Education}
  \resumeSubHeadingListStart
    \resumeSubheading{University}{Location}{Degree}{Dates}
  \resumeSubHeadingListEnd

\section{Experience}
  \resumeSubHeadingListStart
    \resumeSubheading{Title}{Dates}{Company}{Location}
      \resumeItemListStart
        \resumeItem{Bullet point}
      \resumeItemListEnd
  \resumeSubHeadingListEnd

\section{Projects}
    \resumeSubHeadingListStart
      \resumeProjectHeading{\textbf{Project Name} $|$ \emph{Technologies}}{Dates}
          \resumeItemListStart
            \resumeItem{Bullet point}
          \resumeItemListEnd
    \resumeSubHeadingListEnd

\section{Technical Skills}
 \begin{itemize}[leftmargin=0.15in, label={}]
    \small{\item{
     \textbf{Languages}{: Java, Python} \\
     \textbf{Frameworks}{: React, Node.js} \\
    }}
 \end{itemize}

\end{document}
"""

class ResumeTailor:
    def __init__(
        self,
        api_key: str | None = None,
        model: str = _DEFAULT_MODEL,
    ) -> None:
        settings = get_settings()
        _key = api_key or settings.groq_api_key
        if not _key:
            raise ValueError("GROQ_API_KEY is not set.")
        self._client = Groq(api_key=_key)
        self._model = model

    def tailor(self, resume_text: str, jd_text: str) -> dict:
        """
        Generates a tailored resume and extracts the candidate's name.
        Returns a dict with 'candidate_name' and 'latex_content'.
        """
        import json
        user_prompt = f"### TARGET JOB DESCRIPTION:\n{jd_text}\n\n### CURRENT RESUME:\n{resume_text}"
        messages = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ]
        
        response_str = self._call_llm(messages)
        try:
            return json.loads(response_str)
        except json.JSONDecodeError:
            logger.error("Failed to parse LLM JSON response.")
            return {"candidate_name": "Applicant", "latex_content": response_str}

    @retry(
        retry=retry_if_exception_type(Exception),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=20),
        reraise=True,
    )
    def _call_llm(self, messages: list[dict[str, str]]) -> str:
        response = self._client.chat.completions.create(
            model=self._model,
            messages=messages,
            max_tokens=_MAX_TOKENS,
            temperature=_TEMPERATURE,
            response_format={"type": "json_object"}
        )
        return response.choices[0].message.content or "{}"
