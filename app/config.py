"""Configurações globais carregadas de variáveis de ambiente (.env)."""
from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Seleção de provedor de IA (chat + áudio). OpenAI permanece como fallback.
    ai_provider: Literal["gemini", "openai"] = Field("gemini", alias="AI_PROVIDER")
    # Provedor separado para transcrição de áudio.
    # Por padrão segue ai_provider, mas pode ser forçado para "gemini" mesmo
    # quando ai_provider=openai (evita pagar Whisper ou contornar restrições).
    audio_provider: Literal["gemini", "openai"] | None = Field(None, alias="AUDIO_PROVIDER")

    # OpenAI
    openai_api_key: str | None = Field(None, alias="OPENAI_API_KEY")
    openai_model: str = Field("gpt-4.1-mini", alias="OPENAI_MODEL")
    openai_whisper_model: str = Field("whisper-1", alias="OPENAI_WHISPER_MODEL")

    # Google (Gemini)
    google_api_key: str | None = Field(None, alias="GOOGLE_API_KEY")
    gemini_model: str = Field("gemini-2.0-flash", alias="GEMINI_MODEL")
    gemini_audio_model: str = Field("gemini-2.0-flash", alias="GEMINI_AUDIO_MODEL")

    # uazapiGO
    uazapi_base_url: str = Field(..., alias="UAZAPI_BASE_URL")
    uazapi_instance_token: str = Field(..., alias="UAZAPI_INSTANCE_TOKEN")
    uazapi_webhook_secret: str | None = Field(None, alias="UAZAPI_WEBHOOK_SECRET")

    # Debounce / Fila
    debounce_seconds: float = Field(5.0, alias="DEBOUNCE_SECONDS")

    # Persistência Agno
    agent_db_file: str = Field("sessions.db", alias="AGENT_DB_FILE")

    # Painel Admin
    admin_user: str = Field("admin", alias="ADMIN_USER")
    admin_pass: str = Field(..., alias="ADMIN_PASS")
    admin_db_file: str = Field("admin.db", alias="ADMIN_DB_FILE")

    # Server
    log_level: str = Field("INFO", alias="LOG_LEVEL")
    host: str = Field("0.0.0.0", alias="HOST")
    port: int = Field(8000, alias="PORT")

    @property
    def effective_audio_provider(self) -> str:
        """Provedor efetivo para transcrição: AUDIO_PROVIDER > AI_PROVIDER."""
        return self.audio_provider or self.ai_provider

    @model_validator(mode="after")
    def _check_provider_key(self) -> "Settings":
        if self.ai_provider == "gemini" and not self.google_api_key:
            raise ValueError("AI_PROVIDER=gemini exige GOOGLE_API_KEY.")
        if self.ai_provider == "openai" and not self.openai_api_key:
            raise ValueError("AI_PROVIDER=openai exige OPENAI_API_KEY.")
        # Valida chave do provider de áudio, se explicitamente definido.
        if self.audio_provider == "gemini" and not self.google_api_key:
            raise ValueError("AUDIO_PROVIDER=gemini exige GOOGLE_API_KEY.")
        if self.audio_provider == "openai" and not self.openai_api_key:
            raise ValueError("AUDIO_PROVIDER=openai exige OPENAI_API_KEY.")
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
