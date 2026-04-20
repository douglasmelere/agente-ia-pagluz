"""Configurações globais carregadas de variáveis de ambiente (.env)."""
from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # OpenAI
    openai_api_key: str = Field(..., alias="OPENAI_API_KEY")
    openai_model: str = Field("gpt-4o", alias="OPENAI_MODEL")
    openai_whisper_model: str = Field("whisper-1", alias="OPENAI_WHISPER_MODEL")

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


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
