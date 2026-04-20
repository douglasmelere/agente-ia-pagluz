"""Key/value de configurações dinâmicas persistidas em SQLite."""
from __future__ import annotations

from .db import connect


VALID_DEFAULT_MODES = {"trigger_required", "always_on"}


async def get(key: str, default: str | None = None) -> str | None:
    async with connect() as db:
        row = await (
            await db.execute("SELECT value FROM settings WHERE key = ?", (key,))
        ).fetchone()
        return row["value"] if row else default


async def set(key: str, value: str) -> None:
    async with connect() as db:
        await db.execute(
            """
            INSERT INTO settings (key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (key, value),
        )
        await db.commit()


async def all_items() -> dict[str, str]:
    async with connect() as db:
        rows = await (await db.execute("SELECT key, value FROM settings")).fetchall()
        return {r["key"]: r["value"] for r in rows}
