"""Schemas (Pydantic) para o payload do webhook da uazapiGO.

A uazapiGO reenvia eventos do WhatsApp Web em um envelope padronizado.
Como o payload evolui, mantemos os modelos permissivos (``extra="allow"``).
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class UazapiWebhookPayload(BaseModel):
    """Envelope genérico enviado pela uazapiGO para o seu webhook."""

    model_config = ConfigDict(extra="allow")

    event: str | None = Field(None, description="Tipo do evento, ex.: 'messages.upsert'.")
    EventType: str | None = None  # variação usada em algumas versões
    instance: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)

    def normalized_event(self) -> str:
        return (self.event or self.EventType or "").lower()
