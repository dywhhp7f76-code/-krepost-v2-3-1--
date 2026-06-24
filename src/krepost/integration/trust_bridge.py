from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from loguru import logger

# тот же парсер тела, что в ingestion — единый контракт хеширования
from krepost.ingestion.document_ingestion import _parse_existing_frontmatter


class TrustBridge:
    """
    Связывает события vault с реестром доверия.
    Не модель, не агент — тонкая прослойка-маршрутизатор (single responsibility).
    """

    def __init__(self, trust_registry, vault_root: Path,
                 extra_untrusted_dirs: Optional[List[str]] = None):
        self.trust = trust_registry
        self.vault_root = Path(vault_root).resolve()
        # папки внутри vault, которым НЕ доверяем (помимо ingested/).
        # training/ — полигон с заражёнными примерами, его доверять нельзя.
        self.untrusted_dirs = set(extra_untrusted_dirs or [])
        # ingested_subdir TrustRegistry и так отсечёт, но дублируем явно
        self.untrusted_dirs.add(getattr(trust_registry, "ingested_subdir", "ingested"))

    # ── вспомогательное ────────────────────────────────────────────────────

    def _is_untrusted_zone(self, path: Path) -> bool:
        """True, если путь лежит в одной из недоверенных папок."""
        try:
            rel = path.resolve().relative_to(self.vault_root)
        except ValueError:
            # вне vault — не наше дело, считаем недоверенным
            return True
        return len(rel.parts) > 0 and rel.parts[0] in self.untrusted_dirs

    def _read_body(self, path: Path) -> Optional[str]:
        """Прочитать заметку и вернуть ТЕЛО без frontmatter (контракт хеша)."""
        try:
            content = Path(path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            logger.exception(f"TrustBridge: не прочитать {path}")
            return None
        _existing, body = _parse_existing_frontmatter(content)
        return body

    # ── колбэки для VaultWatcher ─────────────────────────────────────────────

    def on_changed(self, path_str: str) -> None:
        """Заметка создана/изменена. Если в доверенной зоне — register по телу."""
        path = Path(path_str).resolve()   # абсолютный — единый с проверкой (фикс двойного vault)
        if self._is_untrusted_zone(path):
            return  # ingested/ или training/ — не доверяем, не регистрируем
        body = self._read_body(path)
        if body is None:
            return
        self.trust.register(str(path), body)
        logger.debug(f"TrustBridge: registered {path.name}")

    def on_deleted(self, path_str: str) -> None:
        """Заметка удалена/перемещена-из. Убрать из реестра, чтобы не мусорить."""
        self.trust.forget(str(Path(path_str).resolve()))   # абсолютный — единый ключ
        logger.debug(f"TrustBridge: forgot {Path(path_str).name}")

    # ── стартовая регистрация ────────────────────────────────────────────────

    def bootstrap(self) -> int:
        """
        Разовая регистрация всех существующих доверенных заметок при старте.
        Без неё на свежем запуске реестр пуст → все твои заметки пойдут через
        Guard, пока ты каждую не тронешь. Хешируем тело без frontmatter.
        Возвращает число зарегистрированных.
        """
        n = 0
        for md in self.vault_root.rglob("*.md"):
            if self._is_untrusted_zone(md):
                continue
            body = self._read_body(md)
            if body is None:
                continue
            self.trust.register(str(md.resolve()), body)
            n += 1
        logger.info(f"TrustBridge.bootstrap: registered {n} trusted notes")
        return n