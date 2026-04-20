"""Estado por conversa (ai_active, timestamps, motivos) em SQLite."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from .db import connect
from .logging_conf import get_logger

logger = get_logger(__name__)


@dataclass
class Conversation:
    remote_jid: str
    ai_active: bool
    triggered_at: str | None
    triggered_reason: str | None
    deactivated_at: str | None
    deactivated_reason: str | None
    last_inbound_at: str | None
    last_inbound_preview: str | None
    created_at: str


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _row_to_conv(row) -> Conversation:
    return Conversation(
        remote_jid=row["remote_jid"],
        ai_active=bool(row["ai_active"]),
        triggered_at=row["triggered_at"],
        triggered_reason=row["triggered_reason"],
        deactivated_at=row["deactivated_at"],
        deactivated_reason=row["deactivated_reason"],
        last_inbound_at=row["last_inbound_at"],
        last_inbound_preview=row["last_inbound_preview"],
        created_at=row["created_at"],
    )


async def get_or_create(remote_jid: str) -> Conversation:
    async with connect() as db:
        row = await (
            await db.execute(
                "SELECT * FROM conversations WHERE remote_jid = ?",
                (remote_jid,),
            )
        ).fetchone()
        if row:
            return _row_to_conv(row)
        await db.execute(
            "INSERT INTO conversations (remote_jid, ai_active) VALUES (?, 0)",
            (remote_jid,),
        )
        await db.commit()
        row = await (
            await db.execute(
                "SELECT * FROM conversations WHERE remote_jid = ?",
                (remote_jid,),
            )
        ).fetchone()
        return _row_to_conv(row)


async def record_inbound(remote_jid: str, preview: str) -> None:
    """Atualiza último recebido (timestamp + preview) para listagem no painel."""
    trimmed = (preview or "").strip().replace("\n", " ")
    if len(trimmed) > 140:
        trimmed = trimmed[:137] + "..."
    async with connect() as db:
        await db.execute(
            """
            UPDATE conversations
               SET last_inbound_at = ?, last_inbound_preview = ?
             WHERE remote_jid = ?
            """,
            (_now_iso(), trimmed, remote_jid),
        )
        await db.commit()


async def activate(remote_jid: str, reason: str) -> None:
    async with connect() as db:
        await db.execute(
            """
            UPDATE conversations
               SET ai_active = 1,
                   triggered_at = ?,
                   triggered_reason = ?,
                   deactivated_at = NULL,
                   deactivated_reason = NULL
             WHERE remote_jid = ?
            """,
            (_now_iso(), reason, remote_jid),
        )
        await db.commit()
    logger.info("conversation.activated", remote_jid=remote_jid, reason=reason)


async def deactivate(remote_jid: str, reason: str) -> None:
    async with connect() as db:
        await db.execute(
            """
            UPDATE conversations
               SET ai_active = 0,
                   deactivated_at = ?,
                   deactivated_reason = ?
             WHERE remote_jid = ?
            """,
            (_now_iso(), reason, remote_jid),
        )
        await db.commit()
    logger.info("conversation.deactivated", remote_jid=remote_jid, reason=reason)


async def list_all(
    only_active: bool = False,
    limit: int = 200,
) -> list[Conversation]:
    q = "SELECT * FROM conversations"
    if only_active:
        q += " WHERE ai_active = 1"
    q += " ORDER BY COALESCE(last_inbound_at, created_at) DESC LIMIT ?"
    async with connect() as db:
        rows = await (await db.execute(q, (limit,))).fetchall()
        return [_row_to_conv(r) for r in rows]
