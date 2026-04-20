"""Gerenciador de fila de mensagens por conversa (remoteJid).

Objetivos:
    1. **Agrupar** mensagens que chegam em rajada do mesmo usuário em uma
       única chamada ao Agente (evita responder a cada balãozinho).
    2. **Debounce de 5s** (configurável): a cada nova mensagem, o timer é
       resetado; só quando o timer expira é que a fila é processada.
    3. **Typing awareness**: quando a uazapiGO sinaliza que o usuário está
       `composing` (digitando), o timer é **pausado**. Assim que o usuário
       para de digitar (`paused` / `available`), o timer é re-armado.

Arquitetura:
    - ``_conversations``: ``dict[remoteJid, ConversationState]``.
    - Cada estado carrega a lista de mensagens pendentes, o ``asyncio.Task``
      do timer atual, e um ``asyncio.Lock`` para evitar condições de corrida
      quando múltiplos webhooks do mesmo número chegam em paralelo.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Awaitable, Callable

from .config import get_settings
from .logging_conf import get_logger

logger = get_logger(__name__)


# Assinatura do callback que efetivamente processa a fila consolidada.
FlushHandler = Callable[[str, str], Awaitable[None]]


@dataclass
class ConversationState:
    """Estado em memória de uma conversa (1 por remoteJid)."""

    remote_jid: str
    messages: list[str] = field(default_factory=list)
    timer_task: asyncio.Task | None = None
    is_typing: bool = False
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def cancel_timer(self) -> None:
        if self.timer_task and not self.timer_task.done():
            self.timer_task.cancel()
        self.timer_task = None


class MessageQueueManager:
    """Orquestra filas, debounce e eventos de presença por conversa."""

    def __init__(self, flush_handler: FlushHandler) -> None:
        self._conversations: dict[str, ConversationState] = {}
        self._flush_handler = flush_handler
        self._global_lock = asyncio.Lock()
        self._debounce = get_settings().debounce_seconds

    # ------------------------------------------------------------------
    # Helpers internos
    # ------------------------------------------------------------------
    async def _get_state(self, remote_jid: str) -> ConversationState:
        async with self._global_lock:
            state = self._conversations.get(remote_jid)
            if state is None:
                state = ConversationState(remote_jid=remote_jid)
                self._conversations[remote_jid] = state
            return state

    async def _schedule_flush(self, state: ConversationState) -> None:
        """Cancela timer anterior e agenda um novo flush após o debounce.

        Deve ser chamado DENTRO de ``state.lock``.
        """
        state.cancel_timer()
        if state.is_typing:
            logger.debug(
                "queue.flush.deferred_user_typing",
                remote_jid=state.remote_jid,
            )
            return
        if not state.messages:
            return
        state.timer_task = asyncio.create_task(self._flush_after_delay(state))

    async def _flush_after_delay(self, state: ConversationState) -> None:
        try:
            await asyncio.sleep(self._debounce)
        except asyncio.CancelledError:
            logger.debug("queue.timer.cancelled", remote_jid=state.remote_jid)
            raise
        await self._flush(state)

    async def _flush(self, state: ConversationState) -> None:
        """Junta as mensagens pendentes e chama o handler (o agente)."""
        async with state.lock:
            if not state.messages:
                return
            merged = "\n".join(m.strip() for m in state.messages if m.strip())
            state.messages.clear()
            state.timer_task = None

        logger.info(
            "queue.flush",
            remote_jid=state.remote_jid,
            chars=len(merged),
        )
        try:
            await self._flush_handler(state.remote_jid, merged)
        except Exception:  # noqa: BLE001
            logger.exception("queue.flush.handler_failed", remote_jid=state.remote_jid)

    # ------------------------------------------------------------------
    # API pública (chamada pelos webhooks)
    # ------------------------------------------------------------------
    async def enqueue_message(self, remote_jid: str, text: str) -> None:
        """Adiciona uma mensagem à fila e reinicia o debounce."""
        text = (text or "").strip()
        if not text:
            return
        state = await self._get_state(remote_jid)
        async with state.lock:
            state.messages.append(text)
            logger.info(
                "queue.enqueue",
                remote_jid=remote_jid,
                queue_size=len(state.messages),
                is_typing=state.is_typing,
            )
            await self._schedule_flush(state)

    async def handle_presence(self, remote_jid: str, presence: str) -> None:
        """Pausa/retoma o timer em função do evento de presença.

        ``presence`` é a string bruta vinda da uazapiGO; tratamos como
        "digitando" qualquer valor que contenha ``composing`` ou ``recording``
        (áudio em gravação).
        """
        typing_signals = {"composing", "recording"}
        normalized = (presence or "").lower()
        state = await self._get_state(remote_jid)

        async with state.lock:
            was_typing = state.is_typing
            state.is_typing = normalized in typing_signals

            if state.is_typing and not was_typing:
                logger.info("queue.presence.typing_started", remote_jid=remote_jid)
                state.cancel_timer()
            elif not state.is_typing and was_typing:
                logger.info("queue.presence.typing_stopped", remote_jid=remote_jid)
                # Usuário parou de digitar → re-arma o debounce se há fila.
                await self._schedule_flush(state)

    async def shutdown(self) -> None:
        """Cancela todos os timers pendentes (usado no shutdown do FastAPI)."""
        async with self._global_lock:
            for state in self._conversations.values():
                state.cancel_timer()
