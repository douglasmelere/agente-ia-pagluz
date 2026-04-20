"""FastAPI app — webhook da uazapiGO + painel admin + orquestração do agente.

Fluxo de ativação (gate):
    1. Mensagem chega → ``record_inbound`` (salva preview para o painel).
    2. Se conversa JÁ tem ai_active=True → enfileira.
    3. Se inativa → tenta match de trigger. Match → ativa + enfileira.
    4. Se inativa e sem match → verifica ``default_mode`` em settings.
        - ``trigger_required`` → ignora silenciosamente.
        - ``always_on`` → ativa + enfileira.
    5. Quando o debounce expira, o agente roda. Se ele chamar a tool
       ``encerrar_atendimento``, desativamos a conversa após enviar a
       resposta final.

Segurança:
    - Webhook: header ``X-Webhook-Secret`` (opcional).
    - Admin: HTTP Basic Auth.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Request, status

from . import conversations as conv_store
from . import settings_store
from . import triggers as trig_store
from .admin.router import router as admin_router
from .agent import run_agent
from .audio import get_transcriber
from .config import get_settings
from .db import init_db
from .logging_conf import get_logger, setup_logging
from .queue_manager import MessageQueueManager
from .schemas import UazapiWebhookPayload
from .uazapi import get_uazapi_client, shutdown_uazapi_client

setup_logging()
logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Handler de flush (disparado após o debounce)
# ---------------------------------------------------------------------------
async def _flush_handler(remote_jid: str, merged_text: str) -> None:
    """Processa a fila: roda o agente, responde, e trata auto-desativação."""
    # Segurança extra: se a conversa foi desativada no painel entre o
    # enfileiramento e o flush, abortamos o turn.
    conv = await conv_store.get_or_create(remote_jid)
    if not conv.ai_active:
        logger.info("flush.skipped_ai_inactive", remote_jid=remote_jid)
        return

    number = _jid_to_number(remote_jid)
    client = await get_uazapi_client()

    # Humaniza: "digitando..." enquanto o agente pensa.
    asyncio.create_task(client.send_presence(number, "composing", delay_ms=1500))

    try:
        reply, deactivation_reason = await run_agent(
            session_id=remote_jid, user_message=merged_text
        )
    except Exception:  # noqa: BLE001
        logger.exception("agent.failed", remote_jid=remote_jid)
        reply = (
            "Opa, tive um problema rapidinho aqui do meu lado 🙈 "
            "Pode repetir sua última mensagem, por favor?"
        )
        deactivation_reason = None

    if reply:
        try:
            await client.send_text(number, reply)
        except Exception:  # noqa: BLE001
            logger.exception("uazapi.send_failed", remote_jid=remote_jid)

    # Auto-desativação via tool do agente → só acontece APÓS enviar a resposta.
    if deactivation_reason:
        await conv_store.deactivate(remote_jid, reason=f"auto:{deactivation_reason}")


queue_manager = MessageQueueManager(flush_handler=_flush_handler)


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(_: FastAPI):
    logger.info("app.startup")
    await init_db()
    await get_uazapi_client()
    yield
    logger.info("app.shutdown")
    await queue_manager.shutdown()
    await shutdown_uazapi_client()


app = FastAPI(
    title="PagLuz — Agente de Atendimento (Luz)",
    description="Webhook uazapiGO + Agente Agno + Painel Admin.",
    version="1.1.0",
    lifespan=lifespan,
)

app.include_router(admin_router)


# ---------------------------------------------------------------------------
# Webhook auth
# ---------------------------------------------------------------------------
async def verify_webhook_secret(
    x_webhook_secret: str | None = Header(default=None, alias="X-Webhook-Secret"),
) -> None:
    expected = get_settings().uazapi_webhook_secret
    if expected and x_webhook_secret != expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Webhook secret inválido.",
        )


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------
@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Webhook principal
# ---------------------------------------------------------------------------
@app.post("/webhook/uazapi", dependencies=[Depends(verify_webhook_secret)])
async def uazapi_webhook(request: Request) -> dict[str, Any]:
    raw = await request.json()
    payload = UazapiWebhookPayload.model_validate(raw)
    event = payload.normalized_event()
    data = payload.data or {}

    # --- Mensagens recebidas -----------------------------------------------
    if _is_message_event(event):
        key = data.get("key") or {}
        if key.get("fromMe") is True:
            return {"ok": True, "skipped": "fromMe"}

        remote_jid = key.get("remoteJid") or data.get("chatid") or data.get("from")
        if not remote_jid:
            logger.warning("webhook.no_remote_jid", raw=raw)
            return {"ok": False, "reason": "missing remoteJid"}

        msg_type, text, audio_id = _extract_message(data)

        if msg_type == "text" and text:
            await _gate_and_enqueue(remote_jid, text)
            return {"ok": True, "handled": "text"}

        if msg_type == "audio" and audio_id:
            asyncio.create_task(_handle_audio(remote_jid, audio_id))
            return {"ok": True, "handled": "audio-async"}

        logger.info("webhook.unsupported_message_type", event=event, keys=list(data.keys()))
        return {"ok": True, "ignored": "unsupported-message-type"}

    # --- Presença (digitando / parou de digitar) ---------------------------
    if _is_presence_event(event):
        remote_jid = (
            data.get("id")
            or data.get("remoteJid")
            or data.get("chatid")
            or data.get("from")
        )
        presence = (
            data.get("presence")
            or data.get("lastKnownPresence")
            or data.get("status")
            or ""
        )
        if remote_jid:
            # Só processa presença se houver conversa com IA ativa — evita
            # criar estado em memória para números sem relevância.
            conv = await conv_store.get_or_create(remote_jid)
            if conv.ai_active:
                await queue_manager.handle_presence(remote_jid, str(presence))
        return {"ok": True, "presence": presence}

    logger.info("webhook.ignored", event=event)
    return {"ok": True, "ignored": event}


# ---------------------------------------------------------------------------
# Gate de ativação
# ---------------------------------------------------------------------------
async def _gate_and_enqueue(remote_jid: str, text: str) -> None:
    """Decide se a mensagem deve alimentar o agente, conforme ai_active +
    triggers + default_mode."""
    await conv_store.record_inbound(remote_jid, preview=text)
    conv = await conv_store.get_or_create(remote_jid)

    if conv.ai_active:
        await queue_manager.enqueue_message(remote_jid, text)
        return

    matched, phrase = await trig_store.matches(text)
    if matched:
        await conv_store.activate(remote_jid, reason=f"trigger:{phrase}")
        await queue_manager.enqueue_message(remote_jid, text)
        return

    mode = await settings_store.get("default_mode", "trigger_required")
    if mode == "always_on":
        await conv_store.activate(remote_jid, reason="default_always_on")
        await queue_manager.enqueue_message(remote_jid, text)
        return

    logger.info(
        "gate.blocked",
        remote_jid=remote_jid,
        reason="no_trigger_and_trigger_required",
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _is_message_event(event: str) -> bool:
    return event in {"messages.upsert", "message", "messages", "message.received"}


def _is_presence_event(event: str) -> bool:
    return event in {"presence.update", "presence", "chats.update"}


def _jid_to_number(remote_jid: str) -> str:
    return remote_jid.split("@", 1)[0].split(":", 1)[0]


def _extract_message(data: dict[str, Any]) -> tuple[str, str | None, str | None]:
    """Retorna (tipo, texto, audio_id). Tipo: 'text' | 'audio' | 'other'."""
    message = data.get("message") or {}
    msg_type = (data.get("messageType") or "").lower()

    if "conversation" in message and message["conversation"]:
        return "text", message["conversation"], None
    if "extendedTextMessage" in message:
        text = (message["extendedTextMessage"] or {}).get("text")
        if text:
            return "text", text, None

    flat_text = data.get("text") or data.get("body")
    if flat_text and msg_type in {"", "text", "conversation"}:
        return "text", str(flat_text), None

    if "audioMessage" in message or msg_type in {"audiomessage", "audio", "ptt"}:
        key = data.get("key") or {}
        audio_id = key.get("id") or data.get("messageId") or data.get("id")
        return "audio", None, audio_id

    return "other", None, None


async def _handle_audio(remote_jid: str, audio_id: str) -> None:
    """Baixa → Whisper → passa pelo gate como texto."""
    client = await get_uazapi_client()
    try:
        audio_bytes = await client.download_media(audio_id)
    except Exception:  # noqa: BLE001
        logger.exception("audio.download_failed", audio_id=audio_id)
        return

    try:
        text = await get_transcriber().transcribe(audio_bytes)
    except Exception:  # noqa: BLE001
        logger.exception("audio.transcribe_failed", audio_id=audio_id)
        return

    if not text:
        logger.info("audio.empty_transcription", remote_jid=remote_jid)
        return

    await _gate_and_enqueue(remote_jid, f"[áudio transcrito] {text}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=False,
        log_level=settings.log_level.lower(),
    )
