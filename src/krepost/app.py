"""
krepost/app.py
Точка входа Крепости — поднимает модули, связывает, управляет shutdown.

Запуск:
    python -m krepost.app
"""
from __future__ import annotations

import asyncio
import signal
import sys
from pathlib import Path

from loguru import logger

from krepost.config import settings
from krepost.security import TrustRegistry, SecurityPipeline
from krepost.monitoring import MonitoringService
from krepost.integration.trust_bridge import TrustBridge
from krepost.ingestion.document_ingestion import DocumentIngestion


def _init_dirs() -> None:
    for d in (settings.data_dir, settings.log_dir, settings.quarantine_dir,
              settings.cache_dir, settings.vault_dir, settings.inbox_dir):
        d.mkdir(parents=True, exist_ok=True)


async def run() -> None:
    _init_dirs()
    logger.info("Крепость: старт")

    monitor = MonitoringService(
        db_path=settings.analytics_db,
        jsonl_path=settings.events_jsonl,
        telegram_bot_token=settings.telegram_bot_token,
        telegram_chat_id=settings.telegram_chat_id,
        telegram_timeout_sec=settings.telegram_timeout,
        max_queue=settings.monitor_queue_max,
    )
    monitor.start()

    trust = TrustRegistry(
        vault_root=settings.vault_dir,
        ingested_subdir=settings.ingested_subdir,
    )

    bridge = TrustBridge(
        trust_registry=trust,
        vault_root=settings.vault_dir,
        extra_untrusted_dirs=settings.extra_untrusted_dirs,
    )
    registered = bridge.bootstrap()
    logger.info(f"TrustBridge: {registered} заметок зарегистрировано при старте")

    ingestion = DocumentIngestion(
        base_dir=settings.inbox_dir,
        vault_dir=settings.vault_dir,
        hashes_path=settings.ingestion_hashes,
        max_file_size_mb=settings.max_file_size_mb,
        on_event=monitor.handle_event,
        on_note_changed=bridge.on_changed,
    )

    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    def _signal_handler() -> None:
        logger.info("Крепость: получен сигнал остановки")
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _signal_handler)

    logger.info("Крепость: готова, ожидание событий (Ctrl+C для остановки)")
    await stop_event.wait()

    logger.info("Крепость: graceful shutdown...")
    monitor.stop()
    logger.info("Крепость: остановлена")


def main() -> None:
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
