"""
krepost/monitoring.py
Monitoring v2.1 — центральный приёмник событий «Крепости».

Изменения относительно v2.0 (по итогам 4 аудитов):
  C1  очередь ограничена (maxsize) — except queue.Full теперь работает, нет OOM
  C2  db.insert обёрнут в except Exception + json.dumps(default=str) — воркер не умирает
  C3  Telegram-дайджест обрезается до лимита 4096 — нет вечного HTTP 400
  C4  _last_flush = time.time() при старте воркера — первый YELLOW не проскакивает окно
  C5  circuit breaker + экспоненциальный backoff на Telegram
  C6  _send_telegram вынесен в отдельный поток — запись в JSONL/SQLite не блокируется сетью
  C7  numeric_payload через WHITELIST ключей — PII (user_id/phone как int) не утекает
  C8  WAL + synchronous=NORMAL на SQLite
  C9  _coerce_timestamp_iso корректно обрабатывает datetime
  C10 handle_event имеет guard на _running + drain очереди при stop()
  C11 _format_group отдаёт агрегаты (count/sum/max) по группе, не только при count==1

В Telegram уходят ТОЛЬКО разрешённые числовые метрики — сырые сообщения не отправляются.
"""

from __future__ import annotations

import json
import queue
import sqlite3
import threading
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Optional

from loguru import logger


# ═══════════════════════════════════════════════════════════════════════════
# Пути
# ═══════════════════════════════════════════════════════════════════════════

DEFAULT_DB_PATH = Path("data/krepost_analytics.db")
DEFAULT_JSONL_PATH = Path("data/logs/krepost_events.jsonl")


def init_paths(db_path: Path = DEFAULT_DB_PATH,
               jsonl_path: Path = DEFAULT_JSONL_PATH) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    jsonl_path.parent.mkdir(parents=True, exist_ok=True)


# ═══════════════════════════════════════════════════════════════════════════
# Уровни
# ═══════════════════════════════════════════════════════════════════════════

class EventLevel(str, Enum):
    GREEN  = "green"
    YELLOW = "yellow"
    RED    = "red"


_VALID_LEVELS = {e.value for e in EventLevel}   # C: EventLevel теперь используется

_SOURCE_BY_CLASS: Dict[str, str] = {
    "RouterEvent":    "router",
    "CacheEvent":     "cache",
    "IngestEvent":    "ingestion",
    "TriggerEvent":   "triggers",
    "EpisodicEvent":  "memory",
    "AdvEvent":       "adversarial",
}

# ── C7: WHITELIST безопасных числовых ключей для Telegram ──────────────────
# Только эти ключи payload уходят наружу. Всё, чего тут нет (включая user_id,
# phone, любые идентификаторы как числа) — НЕ покидает хост.
TELEGRAM_SAFE_KEYS: frozenset[str] = frozenset({
    "latency_ms", "rate", "window", "count", "models_total",
    "models_available", "fallback_rate", "hit_rate", "miss_rate",
    "queue_size", "dropped", "tokens", "duration_ms", "retries",
    "score", "threshold", "chunks", "size_bytes",
})


# ═══════════════════════════════════════════════════════════════════════════
# Нормализованное событие
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class NormalizedEvent:
    level_value: str
    source: str
    event_type: str
    message: str            # сырое сообщение — НЕ уходит в Telegram
    payload: Dict[str, Any]
    timestamp_iso: str

    def safe_numeric_payload(self) -> Dict[str, Any]:
        """C7: только whitelisted числовые/булевы поля — безопасны для Telegram."""
        out: Dict[str, Any] = {}
        for k, v in self.payload.items():
            if k not in TELEGRAM_SAFE_KEYS:
                continue
            if isinstance(v, bool) or isinstance(v, (int, float)):
                out[k] = v
        return out


def _coerce_timestamp_iso(raw: Any) -> str:
    """C9: поддержка None / epoch / datetime / строки."""
    if raw is None:
        return datetime.now(timezone.utc).isoformat()
    if isinstance(raw, datetime):
        if raw.tzinfo is None:
            raw = raw.replace(tzinfo=timezone.utc)
        return raw.isoformat()
    if isinstance(raw, (int, float)):
        return datetime.fromtimestamp(raw, tz=timezone.utc).isoformat()
    return str(raw)


def normalize_event(event: Any) -> Optional[NormalizedEvent]:
    """Duck-typing нормализация *Event из 6 модулей. Сравнение уровней по .value."""
    level_raw = getattr(event, "level", None)
    level_value = getattr(level_raw, "value", level_raw)
    if not isinstance(level_value, str):
        logger.warning(f"monitoring: событие без распознаваемого level: {type(event).__name__}")
        return None
    level_value = level_value.lower()
    if level_value not in _VALID_LEVELS:
        logger.warning(f"monitoring: неизвестный level={level_value}")
        return None

    type_raw = getattr(event, "type", None)
    event_type = getattr(type_raw, "value", type_raw)
    event_type = str(event_type) if event_type is not None else "event"

    source = _SOURCE_BY_CLASS.get(type(event).__name__, type(event).__name__.lower())

    message = getattr(event, "message", "") or ""
    payload = getattr(event, "payload", {}) or {}
    if not isinstance(payload, dict):
        payload = {"value": str(payload)[:200]}

    return NormalizedEvent(
        level_value=level_value,
        source=source,
        event_type=event_type,
        message=str(message),
        payload=payload,
        timestamp_iso=_coerce_timestamp_iso(getattr(event, "timestamp", None)),
    )


# ═══════════════════════════════════════════════════════════════════════════
# JSONL writer (ротация по размеру, atomic rename — POSIX)
# ═══════════════════════════════════════════════════════════════════════════

class JsonlWriter:
    def __init__(self, path: Path, max_bytes: int = 10 * 1024 * 1024,
                 max_backups: int = 5):
        self.path = path
        self.max_bytes = max_bytes
        self.max_backups = max_backups

    def _rotate_if_needed(self) -> None:
        try:
            if self.path.exists() and self.path.stat().st_size >= self.max_bytes:
                for i in range(self.max_backups - 1, 0, -1):
                    src = self.path.with_suffix(f"{self.path.suffix}.{i}")
                    dst = self.path.with_suffix(f"{self.path.suffix}.{i + 1}")
                    if src.exists():
                        dst.unlink(missing_ok=True)
                        src.rename(dst)
                backup = self.path.with_suffix(self.path.suffix + ".1")
                self.path.rename(backup)
        except OSError:
            logger.exception("JSONL rotation failed")

    def write(self, ev: NormalizedEvent) -> None:
        self._rotate_if_needed()
        record = {
            "ts": ev.timestamp_iso,
            "level": ev.level_value,
            "source": ev.source,
            "type": ev.event_type,
            "message": ev.message,
            "payload": ev.payload,
        }
        with open(self.path, "a", encoding="utf-8") as f:
            # C2: default=str — никакой payload не уронит сериализацию
            f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")


# ═══════════════════════════════════════════════════════════════════════════
# SQLite (connection живёт ТОЛЬКО в потоке-воркере)
# ═══════════════════════════════════════════════════════════════════════════

class MetricsDB:
    SCHEMA = """
    CREATE TABLE IF NOT EXISTS monitoring_events (
        id        INTEGER PRIMARY KEY AUTOINCREMENT,
        ts        TEXT NOT NULL,
        level     TEXT NOT NULL,
        source    TEXT NOT NULL,
        type      TEXT NOT NULL,
        message   TEXT,
        payload   TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_mon_ts ON monitoring_events(ts);
    CREATE INDEX IF NOT EXISTS idx_mon_level ON monitoring_events(level);
    CREATE INDEX IF NOT EXISTS idx_mon_source ON monitoring_events(source);
    """

    def __init__(self, db_path: Path):
        self.conn = sqlite3.connect(db_path)
        # C8: WAL + NORMAL — параллельный читатель не блокирует писателя
        self.conn.execute("PRAGMA journal_mode=WAL;")
        self.conn.execute("PRAGMA synchronous=NORMAL;")
        self.conn.executescript(self.SCHEMA)
        self.conn.commit()

    def insert(self, ev: NormalizedEvent) -> None:
        try:
            payload_json = json.dumps(ev.payload, ensure_ascii=False, default=str)  # C2
            self.conn.execute(
                """INSERT INTO monitoring_events (ts, level, source, type, message, payload)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    ev.timestamp_iso, ev.level_value, ev.source, ev.event_type,
                    ev.message[:500],
                    payload_json,
                ),
            )
            self.conn.commit()
        except Exception:
            # C2: ловим ВСЁ, не только sqlite3.Error — воркер не должен умирать
            logger.exception("monitoring SQLite insert failed")

    def close(self) -> None:
        try:
            self.conn.close()
        except sqlite3.Error:
            pass


# ═══════════════════════════════════════════════════════════════════════════
# Monitor
# ═══════════════════════════════════════════════════════════════════════════

_SENTINEL = object()
_TELEGRAM_MAX_CHARS = 4096
_DIGEST_CAP = 3900   # C3: запас под хвост «… ещё N»


class Monitor:
    def __init__(
        self,
        telegram_bot_token: Optional[str] = None,
        telegram_chat_id: Optional[str] = None,
        db_path: Path = DEFAULT_DB_PATH,
        jsonl_path: Path = DEFAULT_JSONL_PATH,
        digest_window_sec: float = 30.0,
        poll_interval_sec: float = 1.0,
        max_buffer: int = 500,
        max_queue: int = 10_000,            # C1: отдельный лимит ingress-очереди
        telegram_timeout_sec: float = 10.0,
    ):
        init_paths(db_path, jsonl_path)
        self.telegram_token = telegram_bot_token
        self.telegram_chat_id = telegram_chat_id
        self.db_path = db_path
        self.jsonl_path = jsonl_path
        self.digest_window_sec = digest_window_sec
        self.poll_interval_sec = poll_interval_sec
        self.max_buffer = max_buffer
        self.telegram_timeout_sec = telegram_timeout_sec

        # C1: ограниченная очередь — except queue.Full теперь достижим
        self._queue: "queue.Queue[Any]" = queue.Queue(maxsize=max_queue)
        self._tg_buffer: list[NormalizedEvent] = []
        self._last_flush: float = 0.0
        self._worker: Optional[threading.Thread] = None
        self._running = False

        # C6: пул-of-one для неблокирующей отправки Telegram
        self._tg_send_lock = threading.Lock()
        self._tg_inflight = False

        # C5: circuit breaker
        self._tg_fail_streak = 0
        self._tg_cooldown_until = 0.0

        # один раз решаем, активен ли Telegram (C: Major-2/3 из аудитов)
        self._tg_active = bool(telegram_bot_token and telegram_chat_id)

    # ── Публичный sync-вход ────────────────────────────────────────────────

    def handle_event(self, event: Any) -> None:
        if not self._running:        # C10: после stop() события не копятся в мёртвой очереди
            return
        ev = normalize_event(event)
        if ev is None:
            return
        try:
            self._queue.put_nowait(ev)
        except queue.Full:           # C1: теперь реально срабатывает
            logger.warning("monitoring queue full — событие отброшено")

    # ── Lifecycle ───────────────────────────────────────────────────────────

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._worker = threading.Thread(target=self._run, name="monitor-worker", daemon=True)
        self._worker.start()
        logger.info("Monitor worker started")

    def stop(self, timeout: float = 15.0) -> None:
        """
        C6/C10: timeout по умолчанию > telegram_timeout_sec, чтобы воркер успел
        завершить in-flight отправку и сдренить очередь до _SENTINEL.
        """
        if not self._running:
            return
        self._running = False
        self._queue.put(_SENTINEL)   # встаёт после всех уже стоящих событий → drain
        if self._worker is not None:
            self._worker.join(timeout=timeout)
        logger.info("Monitor worker stopped")

    # ── Worker loop ──────────────────────────────────────────────────────────

    def _run(self) -> None:
        db = MetricsDB(self.db_path)
        jsonl = JsonlWriter(self.jsonl_path)
        self._last_flush = time.time()   # C4: окно отсчитывается от старта, не от эпохи
        try:
            while True:
                try:
                    item = self._queue.get(timeout=self.poll_interval_sec)
                except queue.Empty:
                    item = None

                if item is _SENTINEL:
                    break

                if item is not None:
                    self._persist(item, jsonl, db)
                    if item.level_value in ("yellow", "red") and self._tg_active:
                        self._buffer_for_telegram(item)
                        if item.level_value == "red":
                            self._flush_telegram(force=True)

                if (self._tg_buffer
                        and time.time() - self._last_flush >= self.digest_window_sec):
                    self._flush_telegram()

            self._flush_telegram(force=True)   # финальный сброс
        finally:
            db.close()

    def _persist(self, ev: NormalizedEvent, jsonl: JsonlWriter, db: MetricsDB) -> None:
        try:
            jsonl.write(ev)
        except Exception:
            logger.exception("JSONL write failed")
        db.insert(ev)   # C2: внутри insert теперь except Exception — воркер не умрёт

    def _buffer_for_telegram(self, ev: NormalizedEvent) -> None:
        self._tg_buffer.append(ev)
        if len(self._tg_buffer) > self.max_buffer:
            dropped = len(self._tg_buffer) - self.max_buffer
            self._tg_buffer = self._tg_buffer[-self.max_buffer:]
            logger.warning(f"Telegram buffer overflow, dropped {dropped} events")

    # ── Telegram (только разрешённые числа, неблокирующая отправка) ──────────

    def _flush_telegram(self, force: bool = False) -> None:
        if not self._tg_buffer:
            return
        if not self._tg_active:
            self._tg_buffer.clear()
            return
        # C5: в режиме cooldown не дёргаем сеть
        if time.time() < self._tg_cooldown_until:
            return
        # C6: предыдущая отправка ещё в полёте — не плодим потоки
        if self._tg_inflight:
            return

        text = self._build_digest(self._tg_buffer)
        snapshot = list(self._tg_buffer)
        self._tg_buffer.clear()       # оптимистично очищаем; при фейле вернём
        self._tg_inflight = True

        def _worker_send():
            ok = self._send_telegram(text)
            with self._tg_send_lock:
                if ok:
                    self._tg_fail_streak = 0
                    self._last_flush = time.time()
                else:
                    # вернуть события обратно в буфер (с учётом cap)
                    self._tg_buffer = (snapshot + self._tg_buffer)[-self.max_buffer:]
                    self._tg_fail_streak += 1
                    backoff = min(300, 30 * (2 ** min(self._tg_fail_streak, 4)))
                    self._tg_cooldown_until = time.time() + backoff
                    logger.warning(f"Telegram cooldown {backoff}s (fails={self._tg_fail_streak})")
                self._tg_inflight = False

        # C6: сеть в отдельном потоке — запись в JSONL/SQLite не блокируется
        threading.Thread(target=_worker_send, name="tg-send", daemon=True).start()

    @staticmethod
    def _build_digest(events: list[NormalizedEvent]) -> str:
        reds = [e for e in events if e.level_value == "red"]
        yellows = [e for e in events if e.level_value == "yellow"]

        def collapse(items: list[NormalizedEvent]) -> Dict[tuple, list[NormalizedEvent]]:
            groups: Dict[tuple, list[NormalizedEvent]] = {}
            for e in items:
                groups.setdefault((e.source, e.event_type), []).append(e)
            return groups

        lines: list[str] = []
        if reds:
            lines.append("🔴 Крепость — критические события")
            for (src, typ), group in collapse(reds).items():
                lines.append(Monitor._format_group(src, typ, group))
        if yellows:
            lines.append("🟡 Крепость — предупреждения")
            for (src, typ), group in collapse(yellows).items():
                lines.append(Monitor._format_group(src, typ, group))

        body = "\n".join(lines) if lines else "Крепость: события"

        # C3: жёсткий cap под лимит Telegram 4096
        if len(body) > _DIGEST_CAP:
            cut = body[:_DIGEST_CAP].rsplit("\n", 1)[0]
            body = f"{cut}\n… ещё {len(events)} событий (обрезано)"
        return body

    @staticmethod
    def _format_group(source: str, event_type: str, group: list[NormalizedEvent]) -> str:
        count = len(group)
        head = f"  {count}× {source}/{event_type}"
        # C11: агрегаты по группе, а не только при count==1
        agg: Dict[str, list[float]] = {}
        for e in group:
            for k, v in e.safe_numeric_payload().items():
                if isinstance(v, bool):
                    continue
                agg.setdefault(k, []).append(float(v))
        if agg:
            parts = []
            for k, vals in sorted(agg.items())[:5]:   # deterministic: sorted
                if len(vals) == 1:
                    parts.append(f"{k}={Monitor._fmt_num(vals[0])}")
                else:
                    parts.append(
                        f"{k}: max={Monitor._fmt_num(max(vals))} "
                        f"avg={Monitor._fmt_num(sum(vals)/len(vals))}"
                    )
            head += " — " + ", ".join(parts)
        return head

    @staticmethod
    def _fmt_num(x: float) -> str:
        return str(int(x)) if x == int(x) else f"{x:.2f}"

    def _send_telegram(self, text: str) -> bool:
        url = f"https://api.telegram.org/bot{self.telegram_token}/sendMessage"
        data = urllib.parse.urlencode({
            "chat_id": self.telegram_chat_id,
            "text": text[:_TELEGRAM_MAX_CHARS],   # C3: страховка на уровне отправки
        }).encode("utf-8")
        try:
            req = urllib.request.Request(url, data=data, method="POST")
            with urllib.request.urlopen(req, timeout=self.telegram_timeout_sec) as resp:
                if resp.status != 200:
                    logger.error(f"Telegram send failed: HTTP {resp.status}")
                    return False
                return True
        except Exception:
            logger.exception("Telegram send exception (Крепость продолжает работу)")
            return False


# ═══════════════════════════════════════════════════════════════════════════
# Smoke-тест с проверками (C: assert'ы вместо print-only)
# ═══════════════════════════════════════════════════════════════════════════

def _smoke_test():
    import sqlite3 as _sq
    from dataclasses import dataclass as dc
    import tempfile, os

    @dc
    class FakeLevel:
        value: str

    @dc
    class FakeRouterEvent:
        level: Any
        type: Any
        message: str
        payload: dict
        timestamp: float

    tmp = tempfile.mkdtemp()
    db_path = Path(tmp) / "a.db"
    jsonl_path = Path(tmp) / "ev.jsonl"

    monitor = Monitor(telegram_bot_token=None, telegram_chat_id=None,
                      db_path=db_path, jsonl_path=jsonl_path,
                      digest_window_sec=1.0, poll_interval_sec=0.2)
    monitor.start()

    monitor.handle_event(FakeRouterEvent(
        level=FakeLevel("green"), type=FakeLevel("routing_decision"),
        message="routed", payload={"latency_ms": 12.0, "user_id": 79991234567},
        timestamp=time.time(),
    ))
    for _ in range(2):
        monitor.handle_event(FakeRouterEvent(
            level=FakeLevel("yellow"), type=FakeLevel("fallback_rate_high"),
            message="fallback 35%", payload={"rate": 0.35, "window": 100},
            timestamp=time.time(),
        ))
    # payload с несериализуемым объектом — воркер НЕ должен умереть (C2)
    monitor.handle_event(FakeRouterEvent(
        level=FakeLevel("red"), type=FakeLevel("all_models_unavailable"),
        message="ollama down", payload={"models_total": 4, "obj": object()},
        timestamp=datetime.now(timezone.utc),   # datetime, не epoch (C9)
    ))

    time.sleep(1.5)
    monitor.stop()

    # Проверки
    conn = _sq.connect(db_path)
    rows = conn.execute("SELECT level, source, type, payload FROM monitoring_events").fetchall()
    conn.close()
    assert len(rows) == 4, f"ожидалось 4 записи, получено {len(rows)}"
    assert jsonl_path.exists() and jsonl_path.stat().st_size > 0, "JSONL пуст"

    # C7: проверяем, что user_id НЕ попал бы в Telegram-дайджест
    fake_ev = NormalizedEvent("green", "router", "x", "msg",
                              {"latency_ms": 12.0, "user_id": 79991234567}, "ts")
    safe = fake_ev.safe_numeric_payload()
    assert "user_id" not in safe, "PII утёк в safe_numeric_payload!"
    assert "latency_ms" in safe

    print("Smoke-тест ПРОЙДЕН: 4 записи в БД, JSONL не пуст, PII отфильтрован, "
          "воркер пережил несериализуемый payload и datetime-timestamp.")


if __name__ == "__main__":
    _smoke_test()
