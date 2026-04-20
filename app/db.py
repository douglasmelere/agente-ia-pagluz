"""Camada de persistência SQLite para o painel admin.

Tabelas:
    * ``conversations``: estado por remoteJid (ai_active, motivos, timestamps).
    * ``triggers``: frases que ativam a IA em uma nova conversa.
    * ``settings``: chave/valor (ex.: ``default_mode``).
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import AsyncIterator

import aiosqlite

from .config import get_settings
from .logging_conf import get_logger

logger = get_logger(__name__)


_SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    remote_jid           TEXT PRIMARY KEY,
    ai_active            INTEGER NOT NULL DEFAULT 0,
    triggered_at         TEXT,
    triggered_reason     TEXT,
    deactivated_at       TEXT,
    deactivated_reason   TEXT,
    last_inbound_at      TEXT,
    last_inbound_preview TEXT,
    created_at           TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS triggers (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    phrase     TEXT NOT NULL,
    active     INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


_DEFAULT_SETTINGS: dict[str, str] = {
    # 'trigger_required' → IA só atende após match de trigger OU ativação manual
    # 'always_on'        → toda nova conversa já começa com IA ligada
    "default_mode": "trigger_required",
}


_init_lock = asyncio.Lock()
_initialized = False


async def init_db() -> None:
    """Cria o schema e popula settings padrão na primeira execução."""
    global _initialized
    async with _init_lock:
        if _initialized:
            return
        db_file = get_settings().admin_db_file
        logger.info("db.init", db_file=db_file)
        async with aiosqlite.connect(db_file) as db:
            await db.executescript(_SCHEMA)
            for key, value in _DEFAULT_SETTINGS.items():
                await db.execute(
                    "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
                    (key, value),
                )
            await db.commit()
        _initialized = True


@asynccontextmanager
async def connect() -> AsyncIterator[aiosqlite.Connection]:
    """Abre uma conexão com row_factory por dict-like access."""
    db_file = get_settings().admin_db_file
    async with aiosqlite.connect(db_file) as db:
        db.row_factory = aiosqlite.Row
        yield db
