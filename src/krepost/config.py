"""
krepost/config.py
Единый конфиг Крепости. Читает .env, даёт дефолты.

Использование:
    from krepost.config import settings
    db = sqlite3.connect(settings.analytics_db)
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings


class KrepostSettings(BaseSettings):
    model_config = {"env_prefix": "KREPOST_", "env_file": ".env", "extra": "ignore"}

    # ── Пути ──────────────────────────────────────────────────────────────
    data_dir: Path = Path("data")
    vault_dir: Path = Path("vault")
    inbox_dir: Path = Path("inbox")
    ingested_subdir: str = "ingested"

    log_dir: Path = Path("data/logs")
    quarantine_dir: Path = Path("data/quarantine")
    cache_dir: Path = Path("data/cache")
    analytics_db: Path = Path("data/krepost_analytics.db")
    router_db: Path = Path("data/router_history.db")
    ingestion_hashes: Path = Path("data/ingestion_hashes.json")
    events_jsonl: Path = Path("data/logs/krepost_events.jsonl")

    # ── Модели (Ollama) ───────────────────────────────────────────────────
    ollama_url: str = "http://localhost:11434"
    brain_model: str = "qwen3.6:27b"
    guard_model: str = "qwen3guard-gen:4b"
    guard_timeout: float = 15.0

    # ── Кэш ───────────────────────────────────────────────────────────────
    l2_similarity_threshold: float = 0.92
    l3_max_entries: int = 2_000
    anomaly_miss_rate_threshold: float = 0.90

    # ── Security ──────────────────────────────────────────────────────────
    circuit_breaker_threshold: int = 5
    circuit_breaker_cooldown: float = 60.0

    # ── Telegram ──────────────────────────────────────────────────────────
    telegram_bot_token: Optional[str] = Field(default=None, alias="TELEGRAM_BOT_TOKEN")
    telegram_chat_id: Optional[str] = Field(default=None, alias="TELEGRAM_CHAT_ID")
    telegram_timeout: float = 10.0

    # ── Monitoring ────────────────────────────────────────────────────────
    monitor_queue_max: int = 10_000
    monitor_flush_interval: float = 30.0

    # ── Router ────────────────────────────────────────────────────────────
    fallback_alert_cooldown: float = 1800.0
    all_down_alert_cooldown: float = 300.0
    health_check_cache_sec: float = 30.0

    # ── Ingestion ─────────────────────────────────────────────────────────
    max_file_size_mb: int = 50
    ingestion_concurrency: int = 4

    # ── Trust ─────────────────────────────────────────────────────────────
    extra_untrusted_dirs: list[str] = Field(default_factory=lambda: ["training"])


settings = KrepostSettings()
