"""Gatilhos textuais que ativam a IA em conversas novas.

Matching: case-insensitive, substring. Ex.: o trigger "vim pela consultoria"
casa com "Oi, vim pela consultoria do site!".
"""
from __future__ import annotations

from dataclasses import dataclass

from .db import connect
from .logging_conf import get_logger

logger = get_logger(__name__)


@dataclass
class Trigger:
    id: int
    phrase: str
    active: bool
    created_at: str


def _row(row) -> Trigger:
    return Trigger(
        id=row["id"],
        phrase=row["phrase"],
        active=bool(row["active"]),
        created_at=row["created_at"],
    )


async def list_all() -> list[Trigger]:
    async with connect() as db:
        rows = await (
            await db.execute("SELECT * FROM triggers ORDER BY active DESC, id ASC")
        ).fetchall()
        return [_row(r) for r in rows]


async def list_active_phrases() -> list[str]:
    async with connect() as db:
        rows = await (
            await db.execute("SELECT phrase FROM triggers WHERE active = 1")
        ).fetchall()
        return [r["phrase"] for r in rows]


async def create(phrase: str) -> Trigger:
    phrase = phrase.strip()
    if not phrase:
        raise ValueError("Frase vazia.")
    async with connect() as db:
        cursor = await db.execute(
            "INSERT INTO triggers (phrase, active) VALUES (?, 1)",
            (phrase,),
        )
        await db.commit()
        row = await (
            await db.execute("SELECT * FROM triggers WHERE id = ?", (cursor.lastrowid,))
        ).fetchone()
        return _row(row)


async def toggle(trigger_id: int) -> None:
    async with connect() as db:
        await db.execute(
            "UPDATE triggers SET active = 1 - active WHERE id = ?",
            (trigger_id,),
        )
        await db.commit()


async def delete(trigger_id: int) -> None:
    async with connect() as db:
        await db.execute("DELETE FROM triggers WHERE id = ?", (trigger_id,))
        await db.commit()


async def matches(text: str) -> tuple[bool, str | None]:
    """Verifica se o texto dispara algum trigger ativo.

    Retorna ``(True, phrase_que_casou)`` ou ``(False, None)``.
    """
    if not text:
        return False, None
    low = text.lower()
    for phrase in await list_active_phrases():
        if phrase.lower() in low:
            return True, phrase
    return False, None
