
&quot;&quot;&quot;
krepost/integration/trust_bridge.py
Мост: VaultWatcher (ingestion) ↔ TrustRegistry (security).

ЗАЧЕМ. security.py доверяет заметке, только если её хеш есть в TrustRegistry.
Но кто-то должен этот реестр наполнять. Это делает мост: следит за ТВОИМИ
заметками в vault (через VaultWatcher) и регистрирует их как доверенные.

ЗОНЫ ДОВЕРИЯ (трёхзонная схема):
  - trusted (весь vault ВНЕ ingested/) — твои заметки → register → мимо Guard
  - ingested/ — внешние документы → НЕ регистрируются → всегда через Guard
  - training/ — полигон (заражённые примеры) → НЕ регистрируется, изолирован

КОНТРАКТ ХЕШИРОВАНИЯ (зафиксирован в security v1.3):
  register получает ТЕЛО ЗАМЕТКИ БЕЗ frontmatter — иначе re-ingest меняет
  date в frontmatter и хеш не сходится. Тело извлекаем тем же парсером, что
  ingestion (_parse_existing_frontmatter).

ПОДКЛЮЧЕНИЕ (в app.py):
    from security import TrustRegistry
    from document_ingestion import VaultWatcher
    from trust_bridge import TrustBridge

    trust = TrustRegistry(vault_root=Path(&quot;vault&quot;), ingested_subdir=&quot;ingested&quot;)
    bridge = TrustBridge(trust, vault_root=Path(&quot;vault&quot;),
                         extra_untrusted_dirs=[&quot;training&quot;])

    # стартовая регистрация существующих заметок (иначе они пойдут через Guard)
    bridge.bootstrap()

    # watcher зовёт мост на изменение/удаление
    watcher = VaultWatcher(
        vault_dir=Path(&quot;vault&quot;),
        on_note_changed=bridge.on_changed,   # создал/изменил → register
        on_note_deleted=bridge.on_deleted,   # удалил/переместил → forget
    )
    watcher.start(loop)
&quot;&quot;&quot;

from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from loguru import logger

# тот же парсер тела, что в ingestion — единый контракт хеширования
from document_ingestion import _parse_existing_frontmatter


class TrustBridge:
    &quot;&quot;&quot;
    Связывает события vault с реестром доверия.
    Не модель, не агент — тонкая прослойка-маршрутизатор (single responsibility).
    &quot;&quot;&quot;

    def __init__(self, trust_registry, vault_root: Path,
                 extra_untrusted_dirs: Optional[List[str]] = None):
        self.trust = trust_registry
        self.vault_root = Path(vault_root).resolve()
        # папки внутри vault, которым НЕ доверяем (помимо ingested/).
        # training/ — полигон с заражёнными примерами, его доверять нельзя.
        self.untrusted_dirs = set(extra_untrusted_dirs or [])
        # ingested_subdir TrustRegistry и так отсечёт, но дублируем явно
        self.untrusted_dirs.add(getattr(trust_registry, &quot;ingested_subdir&quot;, &quot;ingested&quot;))

    # ── вспомогательное ────────────────────────────────────────────────────

    def _is_untrusted_zone(self, path: Path) -&gt; bool:
        &quot;&quot;&quot;True, если путь лежит в одной из недоверенных папок.&quot;&quot;&quot;
        try:
            rel = path.resolve().relative_to(self.vault_root)
        except ValueError:
            # вне vault — не наше дело, считаем недоверенным
            return True
        return len(rel.parts) &gt; 0 and rel.parts[0] in self.untrusted_dirs

    def _read_body(self, path: Path) -&gt; Optional[str]:
        &quot;&quot;&quot;Прочитать заметку и вернуть ТЕЛО без frontmatter (контракт хеша).&quot;&quot;&quot;
        try:
            content = Path(path).read_text(encoding=&quot;utf-8&quot;, errors=&quot;replace&quot;)
        except OSError:
            logger.exception(f&quot;TrustBridge: не прочитать {path}&quot;)
            return None
        _existing, body = _parse_existing_frontmatter(content)
        return body

    # ── колбэки для VaultWatcher ─────────────────────────────────────────────

    def on_changed(self, path_str: str) -&gt; None:
        &quot;&quot;&quot;Заметка создана/изменена. Если в доверенной зоне — register по телу.&quot;&quot;&quot;
        path = Path(path_str).resolve()   # абсолютный — единый с проверкой (фикс двойного vault)
        if self._is_untrusted_zone(path):
            return  # ingested/ или training/ — не доверяем, не регистрируем
        body = self._read_body(path)
        if body is None:
            return
        self.trust.register(str(path), body)
        logger.debug(f&quot;TrustBridge: registered {path.name}&quot;)

    def on_deleted(self, path_str: str) -&gt; None:
        &quot;&quot;&quot;Заметка удалена/перемещена-из. Убрать из реестра, чтобы не мусорить.&quot;&quot;&quot;
        self.trust.forget(str(Path(path_str).resolve()))   # абсолютный — единый ключ
        logger.debug(f&quot;TrustBridge: forgot {Path(path_str).name}&quot;)

    # ── стартовая регистрация ────────────────────────────────────────────────

    def bootstrap(self) -&gt; int:
        &quot;&quot;&quot;
        Разовая регистрация всех существующих доверенных заметок при старте.
        Без неё на свежем запуске реестр пуст → все твои заметки пойдут через
        Guard, пока ты каждую не тронешь. Хешируем тело без frontmatter.
        Возвращает число зарегистрированных.
        &quot;&quot;&quot;
        n = 0
        for md in self.vault_root.rglob(&quot;*.md&quot;):
            if self._is_untrusted_zone(md):
                continue
            body = self._read_body(md)
            if body is None:
                continue
            self.trust.register(str(md.resolve()), body)
            n += 1
        logger.info(f&quot;TrustBridge.bootstrap: registered {n} trusted notes&quot;)
        return n
