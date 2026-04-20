"""Cliente assíncrono para a API uazapiGO.

A uazapiGO expõe endpoints REST para:
    * Enviar mensagens de texto/mídia.
    * Baixar mídia recebida nos webhooks.
    * Disparar "presence" (digitando...) no WhatsApp.

Docs: https://docs.uazapi.com/
"""
from __future__ import annotations

import asyncio
from typing import Any

import httpx

from .config import get_settings
from .logging_conf import get_logger

logger = get_logger(__name__)


class UazapiClient:
    """Wrapper fino em volta do httpx.AsyncClient com o token da instância."""

    def __init__(self) -> None:
        settings = get_settings()
        self._base_url = settings.uazapi_base_url.rstrip("/")
        self._headers = {
            "token": settings.uazapi_instance_token,
            "Content-Type": "application/json",
        }
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            headers=self._headers,
            timeout=httpx.Timeout(30.0, connect=10.0),
        )

    async def close(self) -> None:
        await self._client.aclose()

    # ------------------------------------------------------------------
    # Envio
    # ------------------------------------------------------------------
    async def send_text(self, number: str, text: str) -> dict[str, Any]:
        """Envia uma mensagem de texto para um número (DDI+DDD+Número)."""
        payload = {"number": number, "text": text, "linkPreview": False}
        logger.info("uazapi.send_text", number=number, chars=len(text))
        resp = await self._client.post("/send/text", json=payload)
        resp.raise_for_status()
        return resp.json()

    async def send_presence(
        self,
        number: str,
        presence: str = "composing",
        delay_ms: int = 2500,
    ) -> dict[str, Any]:
        """Envia "digitando..." para humanizar o bot antes da resposta.

        presence: 'composing' | 'recording' | 'paused' | 'available'
        """
        payload = {"number": number, "presence": presence, "delay": delay_ms}
        try:
            resp = await self._client.post("/message/presence", json=payload)
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:  # noqa: BLE001 - não queremos derrubar o fluxo
            logger.warning("uazapi.presence_failed", err=str(exc))
            return {}

    # ------------------------------------------------------------------
    # Download de mídia
    # ------------------------------------------------------------------
    async def download_media(self, message_id: str) -> bytes:
        """Baixa o binário de uma mídia recebida (áudio, imagem, doc, etc.).

        A uazapiGO fornece um endpoint que reconstrói o arquivo a partir do
        messageId. Caso o payload do webhook já traga uma URL direta, essa
        função pode ser substituída por um simples httpx.get(url).
        """
        payload = {"id": message_id}
        logger.info("uazapi.download_media", message_id=message_id)
        resp = await self._client.post("/message/download", json=payload)
        resp.raise_for_status()

        # O endpoint devolve um JSON com base64 OU uma URL temporária.
        data = resp.json() if resp.headers.get("content-type", "").startswith(
            "application/json"
        ) else None

        if data and "fileBase64" in data:
            import base64

            return base64.b64decode(data["fileBase64"])
        if data and "url" in data:
            async with httpx.AsyncClient(timeout=30.0) as c:
                r = await c.get(data["url"])
                r.raise_for_status()
                return r.content
        # Fallback: o endpoint retornou bytes diretamente.
        return resp.content


_client_lock = asyncio.Lock()
_client: UazapiClient | None = None


async def get_uazapi_client() -> UazapiClient:
    """Singleton assíncrono do cliente uazapiGO."""
    global _client
    async with _client_lock:
        if _client is None:
            _client = UazapiClient()
        return _client


async def shutdown_uazapi_client() -> None:
    global _client
    if _client is not None:
        await _client.close()
        _client = None
