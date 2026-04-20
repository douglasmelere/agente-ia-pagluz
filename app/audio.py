"""Serviço de transcrição de áudio. Suporta Gemini (padrão) e Whisper (OpenAI)."""
from __future__ import annotations

import io
from typing import Protocol

from openai import AsyncOpenAI

from .config import get_settings
from .logging_conf import get_logger

logger = get_logger(__name__)


class _Transcriber(Protocol):
    async def transcribe(
        self,
        audio_bytes: bytes,
        *,
        filename: str = ...,
        language: str = ...,
    ) -> str: ...


class WhisperTranscriber:
    """OpenAI Whisper — fallback quando AI_PROVIDER=openai."""

    def __init__(self) -> None:
        settings = get_settings()
        self._client = AsyncOpenAI(api_key=settings.openai_api_key)
        self._model = settings.openai_whisper_model

    async def transcribe(
        self,
        audio_bytes: bytes,
        *,
        filename: str = "audio.ogg",
        language: str = "pt",
    ) -> str:
        if not audio_bytes:
            return ""

        buffer = io.BytesIO(audio_bytes)
        buffer.name = filename

        logger.info("whisper.transcribe.start", size=len(audio_bytes))
        resp = await self._client.audio.transcriptions.create(
            model=self._model,
            file=buffer,
            language=language,
            response_format="text",
        )
        text = resp if isinstance(resp, str) else getattr(resp, "text", "")
        logger.info("whisper.transcribe.done", chars=len(text))
        return text.strip()


class GeminiTranscriber:
    """Gemini multimodal — envia o áudio inline e pede transcrição em pt-BR."""

    def __init__(self) -> None:
        from google import genai  # lazy: só carrega quando Gemini é o provider

        settings = get_settings()
        self._client = genai.Client(api_key=settings.google_api_key)
        self._model = settings.gemini_audio_model

    async def transcribe(
        self,
        audio_bytes: bytes,
        *,
        filename: str = "audio.ogg",
        language: str = "pt",
    ) -> str:
        from google.genai import types

        if not audio_bytes:
            return ""

        mime = "audio/ogg" if filename.endswith(".ogg") else "audio/mpeg"
        prompt = (
            "Transcreva este áudio em português brasileiro. "
            "Retorne APENAS o texto transcrito, sem aspas, rótulos ou comentários."
        )

        logger.info("gemini.transcribe.start", size=len(audio_bytes))
        resp = await self._client.aio.models.generate_content(
            model=self._model,
            contents=[
                types.Part.from_bytes(data=audio_bytes, mime_type=mime),
                prompt,
            ],
        )
        text = (getattr(resp, "text", None) or "").strip()
        logger.info("gemini.transcribe.done", chars=len(text))
        return text


_transcriber: _Transcriber | None = None


def get_transcriber() -> _Transcriber:
    global _transcriber
    if _transcriber is None:
        provider = get_settings().effective_audio_provider
        _transcriber = GeminiTranscriber() if provider == "gemini" else WhisperTranscriber()
    return _transcriber
