"""
krepost/security/trust_registry.py v2.2
Реестр доверенных текстов (whitelist).

Использует canonicalize_for_hash из normalize.py.
Версионирование нормализации для миграций.
"""

import hashlib
import sqlite3
import time
from pathlib import Path
from typing import Optional
import asyncio

from krepost.security.normalize import canonicalize_for_hash, NORMALIZATION_VERSION


class TrustRegistry:
    """
    Реестр доверенных хешей.

    Features:
    - Soft-delete (revoke)
    - Версионирование нормализации
    - Fail-closed (is_trusted -> False при ошибке)
    - Async wrappers через to_thread
    """

    SCHEMA = """
    CREATE TABLE IF NOT EXISTS trusted_sources (
        text_hash TEXT PRIMARY KEY,
        added_at REAL NOT NULL,
        source_name TEXT,
        revoked INTEGER DEFAULT 0,
        normalization_version TEXT DEFAULT 'unknown'
    );
    """

    def __init__(self, db_path: Path = Path("data/trust_registry.db")):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self):
        """Инициализация БД."""
        with sqlite3.connect(self.db_path, timeout=5.0) as conn:
            conn.execute(self.SCHEMA)
            conn.commit()

    def _compute_hash(self, text: str) -> str:
        """Вычислить хеш с версией нормализации."""
        canonical = canonicalize_for_hash(text)
        versioned_text = f"{NORMALIZATION_VERSION}:{canonical}"
        return hashlib.sha256(versioned_text.encode()).hexdigest()

    async def is_trusted(self, text: str) -> bool:
        """
        Проверить, доверен ли текст.

        Returns:
            True если доверен, False если нет или ошибка (fail-closed)
        """
        try:
            text_hash = self._compute_hash(text)
            return await asyncio.to_thread(self._sync_is_trusted, text_hash)
        except Exception:
            return False

    def _sync_is_trusted(self, text_hash: str) -> bool:
        """Синхронная проверка."""
        with sqlite3.connect(self.db_path, timeout=5.0) as conn:
            row = conn.execute(
                "SELECT revoked FROM trusted_sources WHERE text_hash = ?",
                (text_hash,)
            ).fetchone()

            if row is None:
                return False

            return row[0] == 0

    async def add_trusted(self, text: str, source_name: str = "") -> None:
        """Добавить текст в доверенные."""
        text_hash = self._compute_hash(text)
        await asyncio.to_thread(self._sync_add_trusted, text_hash, source_name)

    def _sync_add_trusted(self, text_hash: str, source_name: str):
        """Синхронное добавление (P2 #36: INSERT OR IGNORE + UPDATE)."""
        with sqlite3.connect(self.db_path, timeout=5.0) as conn:
            existing = conn.execute(
                "SELECT revoked FROM trusted_sources WHERE text_hash = ?",
                (text_hash,)
            ).fetchone()

            if existing:
                if existing[0] == 1:
                    conn.execute(
                        "UPDATE trusted_sources SET revoked = 0, source_name = ?, normalization_version = ? WHERE text_hash = ?",
                        (source_name, NORMALIZATION_VERSION, text_hash)
                    )
            else:
                conn.execute(
                    "INSERT INTO trusted_sources (text_hash, added_at, source_name, revoked, normalization_version) VALUES (?, ?, ?, 0, ?)",
                    (text_hash, time.time(), source_name, NORMALIZATION_VERSION)
                )

            conn.commit()

    async def revoke_trusted(self, text: str) -> None:
        """Отозвать доверие (soft-delete)."""
        text_hash = self._compute_hash(text)
        await asyncio.to_thread(self._sync_revoke_trusted, text_hash)

    def _sync_revoke_trusted(self, text_hash: str):
        """Синхронный отзыв."""
        with sqlite3.connect(self.db_path, timeout=5.0) as conn:
            conn.execute(
                "UPDATE trusted_sources SET revoked = 1 WHERE text_hash = ?",
                (text_hash,)
            )
            conn.commit()
