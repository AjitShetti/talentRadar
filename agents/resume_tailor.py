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

_SYSTEM_PROMPT = """\
You are an expert resume writer. The user will provide their current resume and a target job description.
Your task is to tailor the candidate's resume to highlight their most relevant experience and skills for this specific role.

### Guidelines:
- DO NOT invent or fabricate any experience, skills, or degrees that the candidate does not have.
- Re-write bullet points to better align with the language and keywords used in the job description.
- Emphasize accomplishments and metrics that map directly to the job's requirements.
- Output the tailored resume in clean Markdown format, using clear headings (e.g., # Contact, ## Experience, ## Skills).
- DO NOT include any introductory or concluding remarks. Just output the Markdown resume itself.
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

    def tailor(self, resume_text: str, jd_text: str) -> str:
        """
        Generates a tailored resume in Markdown format.
        """
        user_prompt = f"### TARGET JOB DESCRIPTION:\n{jd_text}\n\n### CURRENT RESUME:\n{resume_text}"
        messages = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ]
        
        return self._call_llm(messages)

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
        )
        return response.choices[0].message.content or ""
