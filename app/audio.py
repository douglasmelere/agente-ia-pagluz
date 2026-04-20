"""Serviço de transcrição de áudio via OpenAI Whisper."""
from __future__ import annotations

import io

from openai import AsyncOpenAI

from .config import get_settings
from .logging_conf import get_logger

logger = get_logger(__name__)


class AudioTranscriber:
    """Transcreve áudios do WhatsApp (OGG/Opus) usando Whisper."""

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
        """Recebe bytes de áudio e devolve a transcrição em texto puro."""
        if not audio_bytes:
            return ""

        buffer = io.BytesIO(audio_bytes)
        buffer.name = filename  # a SDK da OpenAI usa o atributo .name p/ inferir o mime

        logger.info("whisper.transcribe.start", size=len(audio_bytes))
        resp = await self._client.audio.transcriptions.create(
            model=self._model,
            file=buffer,
            language=language,
            response_format="text",
        )
        # response_format="text" devolve a string diretamente
        text = resp if isinstance(resp, str) else getattr(resp, "text", "")
        logger.info("whisper.transcribe.done", chars=len(text))
        return text.strip()


_transcriber: AudioTranscriber | None = None


def get_transcriber() -> AudioTranscriber:
    global _transcriber
    if _transcriber is None:
        _transcriber = AudioTranscriber()
    return _transcriber
