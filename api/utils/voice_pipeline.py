"""
api/utils/voice_pipeline.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Voice pipeline utilities for the mock interview feature.

Responsibilities
----------------
1. **STT (Speech-to-Text)**: Transcribe audio blobs via Groq Whisper (fast,
   already in the stack).  Falls back to returning a ``provider=browser_fallback``
   marker so the client can switch to the Web Speech API silently.

2. **TTS (Text-to-Speech)**: Convert question text to audio using the
   browser-compatible Web Speech API via a pass-through (the backend just
   returns the text; the frontend calls the browser's SpeechSynthesis API).
   For a future Supertonic 3 integration, only this file needs changing.

Architecture note (from voice-ai-engine-development skill)
----------------------------------------------------------
The pipeline is intentionally lightweight for v1 because:
  * TTS is done client-side (browser SpeechSynthesis / Supertonic JS SDK)
    to avoid streaming audio bytes through the backend.
  * STT is a single-shot REST call (not a WebSocket stream) because interview
    answers are bounded (user presses "Stop Recording" before submitting).
  * This avoids needing a persistent WebSocket connection and keeps the backend
    fully stateless, consistent with the overall interview agent design.

For a full real-time streaming voice session (Phase 5+), the worker pipeline
pattern from the skill would be applied here.
"""

from __future__ import annotations

import logging

import httpx
from groq import AsyncGroq

from config.settings import get_settings

logger = logging.getLogger(__name__)

_settings = get_settings()

# Groq Whisper model — fastest available, very accurate for English.
WHISPER_MODEL = "whisper-large-v3-turbo"

# Maximum audio file size accepted (25 MB — Groq's hard limit).
MAX_AUDIO_BYTES = 25 * 1024 * 1024


class STTError(Exception):
    """Raised when Speech-to-Text transcription fails unrecoverably."""


class VoicePipeline:
    """
    Stateless voice pipeline helper.

    All methods are async and can be called directly from FastAPI route handlers.
    No persistent connections are held — a new Groq client is created per call
    which is fine for the interview's request-response pattern.
    """

    # ------------------------------------------------------------------
    # STT — Groq Whisper
    # ------------------------------------------------------------------

    async def transcribe_audio(
        self,
        audio_bytes: bytes,
        filename: str = "audio.webm",
        language: str = "en",
    ) -> tuple[str, str]:
        """
        Transcribe audio bytes via Groq Whisper.

        Args:
            audio_bytes: Raw audio file bytes (webm, mp4, wav, m4a, etc.)
            filename:    Original filename with extension — Groq uses the
                         extension to detect the audio format.
            language:    ISO-639-1 language code (default "en").

        Returns:
            A tuple of (transcript_text, provider_name).
            provider_name is "groq_whisper" on success or "browser_fallback"
            when the call fails so the client can handle the fallback.

        Raises:
            STTError: Only on unrecoverable errors (not on rate-limit, which
                      returns the fallback marker instead).
        """
        if not audio_bytes:
            raise STTError("Empty audio bytes provided")

        if len(audio_bytes) > MAX_AUDIO_BYTES:
            raise STTError(
                f"Audio file too large: {len(audio_bytes)} bytes "
                f"(max {MAX_AUDIO_BYTES} bytes)"
            )

        try:
            client = AsyncGroq(api_key=_settings.groq_api_key)

            # Groq SDK expects a file-like tuple: (filename, bytes, content_type)
            content_type = self._guess_content_type(filename)
            transcription = await client.audio.transcriptions.create(
                model=WHISPER_MODEL,
                file=(filename, audio_bytes, content_type),
                language=language,
                response_format="json",
            )

            text = transcription.text.strip()
            logger.info(
                "Groq Whisper transcription: %d chars (file=%s, lang=%s)",
                len(text), filename, language,
            )
            return text, "groq_whisper"

        except Exception as exc:
            # Rate limit (429) or transient errors → signal browser fallback
            logger.warning(
                "Groq Whisper transcription failed (%s) — signalling browser fallback",
                type(exc).__name__,
            )
            # Do NOT raise — return the fallback marker so the client degrades
            # gracefully using the Web Speech API.
            return "", "browser_fallback"

    # ------------------------------------------------------------------
    # TTS — client-side pass-through (v1)
    # ------------------------------------------------------------------

    def get_tts_config(self, text: str, voice: str = "default") -> dict:
        """
        Return a TTS configuration dict for the frontend to consume.

        In v1, TTS is performed client-side by the browser's SpeechSynthesis API
        or the Supertonic 3 JS SDK.  The backend simply echoes back the text and
        preferred voice so the frontend can call the right API.

        Args:
            text:  The question or message to be spoken.
            voice: Voice identifier (ignored in v1, reserved for Supertonic).

        Returns:
            A dict the frontend uses to initialise TTS:
            {
                "text":     str,
                "voice":    str,
                "provider": "browser" | "supertonic",
                "rate":     float,   # speech rate multiplier
                "pitch":    float,   # pitch multiplier
            }
        """
        return {
            "text": text,
            "voice": voice,
            "provider": "browser",  # Supertonic 3 in Phase 5
            "rate": 1.0,
            "pitch": 1.0,
        }

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _guess_content_type(filename: str) -> str:
        """Map common audio extensions to MIME types for Groq's API."""
        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        mapping = {
            "webm": "audio/webm",
            "mp4":  "audio/mp4",
            "m4a":  "audio/m4a",
            "wav":  "audio/wav",
            "mp3":  "audio/mpeg",
            "ogg":  "audio/ogg",
            "flac": "audio/flac",
        }
        return mapping.get(ext, "audio/webm")
