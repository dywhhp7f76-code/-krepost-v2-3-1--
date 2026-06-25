"""
krepost/router/model_router.py
Model Router v2.1 — под архитектуру «Крепость» (Mac Studio + Telegram-алерты).

Изменения v2.0 → v2.1 (свод 4 аудитов + прогон кода):
  C1  is_available=False по умолчанию + авто-health на первом route — нет «слепого» первого запроса
  C2  _persist вынесен в asyncio.to_thread — sync SQLite не блокирует event loop
  C3  report_failure(model) — обратная связь о падении модели в рантайме (health видит только /api/tags)
  C4  DEFAULT_MODELS синхронизированы с «Выбор моделей v1.3»: Qwen3.6-27B dense + Qwen3Guard-Gen-4B;
      guardian-модель помечена is_routable=False — НЕ попадает в генеративный пул роутера
  C5  cooldown на RED all_models_unavailable — нет спама алертов при Ollama down
  C6  transient failure (status!=200 / битый JSON) держит старый кеш, не роняет все модели ложным RED
  C7  query_preview маскируется от PII перед записью в SQLite
  C8  frontmatter-дата документа исправлена (вне кода)
  +   double-checked lock в _fetch_available_models (thundering herd при concurrency)
  +   force_model/force_task валидируются; ROUTING_DECISION эмитится опционально
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import sqlite3
import time
from abc import ABC, abstractmethod
from collections import deque
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable, Dict, List, Optional

import aiohttp
from loguru import logger
from pydantic import BaseModel


# ═══════════════════════════════════════════════════════════════════════════
# Логирование — вызывается явно, защита от дубля handler'а
# ═══════════════════════════════════════════════════════════════════════════

_ROUTER_HANDLER_ID: Optional[int] = None


def init_logging(log_dir: Path = Path("data/logs")) -> None:
    global _ROUTER_HANDLER_ID
    log_dir.mkdir(parents=True, exist_ok=True)
    if _ROUTER_HANDLER_ID is not None:
        try:
            logger.remove(_ROUTER_HANDLER_ID)
        except ValueError:
            pass
    _ROUTER_HANDLER_ID = logger.add(
        log_dir / "model_router.log", rotation="10 MB", level="INFO", enqueue=True)


# ═══════════════════════════════════════════════════════════════════════════
# PII-маскирование query_preview (C7)
# ═══════════════════════════════════════════════════════════════════════════

_PII_PATTERNS = [
    re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"),          # email
    re.compile(r"\b(?:\+?\d[\d\-\s()]{7,}\d)\b"),         # телефон
    re.compile(r"\b[A-Za-z0-9_-]{32,}\b"),               # длинные токены/ключи
]


def _mask_pii(text: str) -> str:
    masked = text
    for pat in _PII_PATTERNS:
        masked = pat.sub("[PII]", masked)
    return masked


# ═══════════════════════════════════════════════════════════════════════════
# Типы задач и события
# ═══════════════════════════════════════════════════════════════════════════

class TaskType(str, Enum):
    CODE      = "code"
    ANALYSIS  = "analysis"
    CHAT      = "chat"
    FAST      = "fast"
    SECURITY  = "security"
    SUMMARIZE = "summarize"
    CREATIVE  = "creative"


TASK_TIE_PRIORITY: Dict[TaskType, int] = {
    TaskType.SECURITY:  0,
    TaskType.CODE:      1,
    TaskType.ANALYSIS:  2,
    TaskType.SUMMARIZE: 3,
    TaskType.CREATIVE:  4,
    TaskType.FAST:      5,
    TaskType.CHAT:      6,
}


class EventLevel(str, Enum):
    GREEN  = "green"
    YELLOW = "yellow"
    RED    = "red"


class EventType(str, Enum):
    ROUTING_DECISION       = "routing_decision"
    FALLBACK_USED          = "fallback_used"
    ALL_MODELS_UNAVAILABLE = "all_models_unavailable"
    FALLBACK_RATE_HIGH     = "fallback_rate_high"
    INFERENCE_FAILURE      = "inference_failure"   # C3


@dataclass
class RouterEvent:
    level: EventLevel
    type: EventType
    message: str
    payload: dict = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)


# ═══════════════════════════════════════════════════════════════════════════
# Pydantic-модели
# ═══════════════════════════════════════════════════════════════════════════

class ModelConfig(BaseModel):
    name: str
    task_types: List[TaskType]
    priority: int = 1
    max_tokens: int = 8192
    temperature: float = 0.7
    timeout: float = 120.0
    is_available: bool = False     # C1: safe default — недоступна до health_check
    is_routable: bool = True       # C4: guardian-модели = False, вне генеративного пула
    avg_latency: float = 0.0


class RouteResult(BaseModel):
    model: str
    task_type: TaskType
    reason: str
    latency: float = 0.0
    fallback_used: bool = False
    auto_routing: bool = False


class RouterStats(BaseModel):
    total_requests: int
    by_model: Dict[str, int]
    by_task: Dict[str, int]
    fallback_count: int
    fallback_rate: float
    avg_latency: float


# ═══════════════════════════════════════════════════════════════════════════
# Task Classifier
# ═══════════════════════════════════════════════════════════════════════════

class TaskClassifier(ABC):
    @abstractmethod
    def classify(self, query: str) -> TaskType:
        ...


class KeywordTaskClassifier(TaskClassifier):
    TASK_PATTERNS: Dict[TaskType, List[str]] = {
        TaskType.CODE: [
            r"\bкод\b", r"\bкода\b", r"\bкоде\b", r"\bкодом\b",
            r"\bфункци\w*\b", r"\bкласс\b",
            r"\bpython\b", r"\bjavascript\b", r"\btypescript\b",
            r"\brust\b", r"\bgolang\b",
            r"\bнапиши код\b", r"\bреализуй\b", r"\bбаг\b",
            r"\bошибка в коде\b", r"\brefactor\b", r"\bимплемент\w*\b",
            r"\bfunction\b", r"\bimplement\b", r"\bdebug\b", r"\bsyntax\b",
        ],
        TaskType.SECURITY: [
            r"\bбезопасност\w*\b", r"\bуязвимост\w*\b", r"\bатак\w*\b",
            r"\bвзлом\w*\b", r"\bjailbreak\b", r"\badversarial\b",
            r"\bпентест\w*\b", r"\bexploit\b", r"\bxss\b", r"\bsql injection\b",
            r"\bsecurity\b", r"\bvulnerability\b", r"\baudit\b",
        ],
        TaskType.SUMMARIZE: [
            r"\bсуммаризируй\b", r"\bкратко\b", r"\bрезюме\b",
            r"\bsummary\b", r"\bsummarize\b",
            r"\bосновные мысли\b", r"\bключевые моменты\b",
            r"\btl;dr\b", r"\bвкратце\b",
        ],
        TaskType.ANALYSIS: [
            r"\bпроанализируй\b", r"\bразбери\b", r"\bобъясни\b",
            r"\bсравни\b", r"\bоцени\b",
            r"\banalyze\b", r"\bexplain\b", r"\bcompare\b",
            r"\bevaluate\b", r"\bresearch\b",
        ],
        TaskType.CREATIVE: [
            r"\bпридумай\b", r"\bсочини\b", r"\bнапиши текст\b",
            r"\bистори\w*\b", r"\bстихотворени\w*\b",
            r"\bcreative\b", r"\bwrite a story\b", r"\bpoem\b", r"\bgenerate\b",
        ],
        TaskType.FAST: [
            r"\bбыстро\b", r"\bкратко ответь\b", r"\bодним словом\b",
            r"\bда или нет\b",
            r"\bquick\b", r"\bbrief\b", r"\bshort answer\b", r"\byes or no\b",
        ],
    }

    def __init__(self):
        self._compiled: Dict[TaskType, List[re.Pattern]] = {
            task: [re.compile(p, re.IGNORECASE) for p in patterns]
            for task, patterns in self.TASK_PATTERNS.items()
        }

    def classify(self, query: str) -> TaskType:
        scores: Dict[TaskType, int] = {}
        for task_type, patterns in self._compiled.items():
            score = sum(1 for p in patterns if p.search(query))
            if score > 0:
                scores[task_type] = score
        if not scores:
            return TaskType.CHAT
        max_score = max(scores.values())
        winners = [task for task, score in scores.items() if score == max_score]
        return min(winners, key=lambda t: TASK_TIE_PRIORITY[t])


# ═══════════════════════════════════════════════════════════════════════════
# SQLite-персистентность
# ═══════════════════════════════════════════════════════════════════════════

class RoutingHistoryDB:
    SCHEMA = """
    CREATE TABLE IF NOT EXISTS routing_decisions (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp     REAL    NOT NULL,
        request_id    TEXT,
        query_hash    TEXT    NOT NULL,
        query_preview TEXT    NOT NULL,
        task_type     TEXT    NOT NULL,
        model_chosen  TEXT    NOT NULL,
        fallback_used INTEGER NOT NULL,
        auto_routing  INTEGER NOT NULL,
        latency_ms    REAL,
        is_correct    INTEGER
    );
    CREATE INDEX IF NOT EXISTS idx_timestamp ON routing_decisions(timestamp);
    CREATE INDEX IF NOT EXISTS idx_unmarked
        ON routing_decisions(is_correct) WHERE is_correct IS NULL;
    """

    def __init__(self, db_path: Path = Path("data/router_history.db")):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db_path = db_path
        with self._connect() as conn:
            conn.executescript(self.SCHEMA)
            conn.execute("PRAGMA journal_mode=WAL")

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(self.db_path, timeout=5.0)
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def log_decision(self, query, task_type, model_chosen, fallback_used,
                     auto_routing, latency_ms, request_id=None) -> None:
        query_hash = hashlib.sha256(query.encode("utf-8")).hexdigest()[:16]
        query_preview = _mask_pii(query[:80])   # C7: маскирование PII
        try:
            with self._connect() as conn:
                conn.execute(
                    """INSERT INTO routing_decisions
                       (timestamp, request_id, query_hash, query_preview, task_type,
                        model_chosen, fallback_used, auto_routing, latency_ms)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (time.time(), request_id, query_hash, query_preview, task_type.value,
                     model_chosen, int(fallback_used), int(auto_routing), latency_ms))
        except sqlite3.Error as e:
            logger.error(f"Failed to log routing decision: {e}")


# ═══════════════════════════════════════════════════════════════════════════
# Router
# ═══════════════════════════════════════════════════════════════════════════

class ModelRouter:
    """
    Маршрутизация запросов к локальным LLM (Ollama) в архитектуре «Крепость».

        User → Охранник → Карантин → [Router + LLM] → Пост-процессор → User

    Контракт: вызвать health_check_all() до первого route(), ЛИБО положиться на
    авто-health при первом запросе (C1). is_available=False по умолчанию.
    """

    # C4: синхронизировано с «Выбор моделей v1.3».
    # Основной мозг — Qwen3.6-27B dense; быстрая — Qwen3 4B.
    # Guardian (Qwen3Guard-Gen-4B) помечен is_routable=False — он НЕ генератор,
    # его вызывает security.py, в генеративный пул роутера он не входит.
    # Имена-теги Ollama — предполагаемые, сверить после `ollama list`.
    DEFAULT_MODELS: List[ModelConfig] = [
        ModelConfig(
            name="qwen3.6:27b",
            task_types=[TaskType.ANALYSIS, TaskType.CHAT, TaskType.CREATIVE,
                        TaskType.SUMMARIZE, TaskType.CODE, TaskType.SECURITY],
            priority=10, max_tokens=32768, temperature=0.7, timeout=180.0),
        ModelConfig(
            name="qwen3:4b",
            task_types=[TaskType.FAST, TaskType.CHAT],
            priority=5, max_tokens=8192, temperature=0.7, timeout=30.0),
        ModelConfig(
            name="qwen3guard-gen:4b",
            task_types=[TaskType.SECURITY],
            priority=1, max_tokens=4096, temperature=0.0, timeout=60.0,
            is_routable=False),   # C4: классификатор, не генератор
    ]

    def __init__(self, ollama_url="http://localhost:11434",
                 default_model="qwen3.6:27b", auto_routing_enabled=False,
                 classifier=None, history_db=None, on_event=None,
                 fallback_rate_threshold=0.30, fallback_alert_window=100,
                 fallback_alert_cooldown_sec=1800, ema_alpha=0.2,
                 health_check_cache_sec=30, all_down_alert_cooldown_sec=300,
                 emit_routing_decisions=False):
        self.ollama_url = ollama_url.rstrip("/")
        self.default_model = default_model
        self.auto_routing_enabled = auto_routing_enabled
        self.classifier = classifier or KeywordTaskClassifier()
        self.history_db = history_db
        self.on_event = on_event
        self.emit_routing_decisions = emit_routing_decisions

        self.fallback_rate_threshold = fallback_rate_threshold
        self.fallback_alert_window = fallback_alert_window
        self.fallback_alert_cooldown_sec = fallback_alert_cooldown_sec
        self._last_fallback_alert_time: float = 0.0

        self.all_down_alert_cooldown_sec = all_down_alert_cooldown_sec  # C5
        self._last_all_down_alert: float = 0.0

        self.ema_alpha = ema_alpha
        self.health_check_cache_sec = health_check_cache_sec
        self._available_models_cache: Optional[List[str]] = None
        self._available_models_cache_time: float = 0.0

        self._session: Optional[aiohttp.ClientSession] = None
        self._session_lock: Optional[asyncio.Lock] = None
        self._cache_lock: Optional[asyncio.Lock] = None
        self._initialized = False   # C1: триггер авто-health на первом route

        self._total = 0
        self._by_model: Dict[str, int] = {}
        self._by_task: Dict[str, int] = {}
        self._fallbacks = 0
        self._latencies: deque = deque(maxlen=1000)
        self._recent_decisions: deque = deque(maxlen=self.fallback_alert_window)

        self.models: Dict[str, ModelConfig] = {}
        self._register_defaults()

    # ── async context manager (S1) ─────────────────────────────────────────

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.close()

    # ── Lifecycle ───────────────────────────────────────────────────────────

    def _lock(self, attr: str) -> asyncio.Lock:
        # lazy-init локов внутри running loop
        lk = getattr(self, attr)
        if lk is None:
            lk = asyncio.Lock()
            setattr(self, attr, lk)
        return lk

    async def _get_session(self) -> aiohttp.ClientSession:
        async with self._lock("_session_lock"):
            if self._session is None or self._session.closed:
                timeout = aiohttp.ClientTimeout(total=10, connect=5)
                self._session = aiohttp.ClientSession(timeout=timeout)
            return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    # ── Регистрация ──────────────────────────────────────────────────────────

    def _register_defaults(self) -> None:
        for model in self.DEFAULT_MODELS:
            self.register_model(model)

    def register_model(self, config: ModelConfig) -> None:
        old = self.models.get(config.name)
        if old is not None:
            logger.warning(f"Model {config.name} re-registered — конфиг перезаписан")
            config.avg_latency = old.avg_latency   # S7: сохранить накопленную EMA
        self.models[config.name] = config
        logger.info(f"Model registered | {config.name} | "
                    f"tasks={[t.value for t in config.task_types]} routable={config.is_routable}")

    # ── Health check (C3/C5/C6 + double-checked lock) ───────────────────────

    async def _fetch_available_models(self) -> Optional[List[str]]:
        now = time.time()
        if (self._available_models_cache is not None
                and now - self._available_models_cache_time < self.health_check_cache_sec):
            return self._available_models_cache

        async with self._lock("_cache_lock"):
            # double-checked: пока ждали лок, другой коротин мог обновить кеш
            now = time.time()
            if (self._available_models_cache is not None
                    and now - self._available_models_cache_time < self.health_check_cache_sec):
                return self._available_models_cache
            try:
                session = await self._get_session()
                async with session.get(f"{self.ollama_url}/api/tags") as resp:
                    if resp.status != 200:
                        logger.warning(f"Ollama API returned {resp.status} — держим старый кеш")
                        return self._available_models_cache   # C6: не ронять модели
                    try:
                        data = await resp.json()
                    except Exception:
                        logger.warning("Ollama вернула не-JSON — держим старый кеш")
                        return self._available_models_cache   # C6
                    available = [m["name"] for m in data.get("models", [])]
                    self._available_models_cache = available
                    self._available_models_cache_time = time.time()
                    return available
            except Exception as e:
                logger.error(f"Failed to fetch available models: {e}")
                return self._available_models_cache   # C6: transient → старый кеш, не None

    async def health_check_all(self) -> Dict[str, bool]:
        available = await self._fetch_available_models()
        if available is None:
            # кеша нет вообще (первый запуск + Ollama недоступна) → честный RED
            status = {name: False for name in self.models}
            for name in self.models:
                self.models[name].is_available = False
            self._emit_all_down("Ollama API unreachable", {"ollama_url": self.ollama_url})
            return status

        status = {}
        for name in self.models:
            is_available = name in available
            self.models[name].is_available = is_available
            status[name] = is_available

        available_count = sum(1 for v in status.values() if v)
        total = len(status)
        logger.info(f"Health check | {available_count}/{total} models available")
        if available_count == 0:
            self._emit_all_down(f"Все {total} моделей недоступны",
                                {"models": list(self.models.keys())})
        return status

    def _emit_all_down(self, message: str, payload: dict) -> None:
        # C5: cooldown на RED, чтобы не спамить при длительном Ollama down
        now = time.time()
        if now - self._last_all_down_alert < self.all_down_alert_cooldown_sec:
            return
        self._last_all_down_alert = now
        self._emit_event(RouterEvent(
            level=EventLevel.RED, type=EventType.ALL_MODELS_UNAVAILABLE,
            message=message, payload=payload))

    def report_failure(self, model_name: str) -> None:
        """
        C3: обратная связь от вызывающего кода о падении модели в рантайме
        (OOM / Ollama 500). health_check видит только наличие в /api/tags, не
        работоспособность — без этого упавшая модель собирала бы весь трафик
        до следующего планового чека.
        """
        if model_name in self.models:
            self.models[model_name].is_available = False
            logger.warning(f"Inference failure reported: {model_name} → is_available=False")
            self._emit_event(RouterEvent(
                level=EventLevel.YELLOW, type=EventType.INFERENCE_FAILURE,
                message=f"Сбой инференса {model_name} — модель отключена до следующего health-check",
                payload={"failed_model": model_name}))

    # ── Routing ───────────────────────────────────────────────────────────

    def _best_model(self, task_type: TaskType) -> Optional[ModelConfig]:
        candidates = [
            m for m in self.models.values()
            if task_type in m.task_types and m.is_available and m.is_routable  # C4
        ]
        if not candidates:
            return None
        # детерминированный tie-break: priority desc, latency asc, имя
        return max(candidates, key=lambda m: (
            m.priority,
            -m.avg_latency if m.avg_latency > 0 else 0.0,
            m.name))

    async def route(self, query, force_task=None, force_model=None,
                    request_id=None) -> RouteResult:
        # C1: первый route триггерит health, если не инициализирован вручную
        if not self._initialized:
            await self.health_check_all()
            self._initialized = True

        start = time.time()
        self._total += 1

        # 1. Ручной выбор — высший приоритет, но валидируем (раньше принимал любую строку)
        if force_model is not None:
            if force_model not in self.models:
                logger.warning(f"force_model '{force_model}' не зарегистрирован — "
                               f"принят как экспертный override без гарантий")
            result = self._make_result(force_model, force_task or TaskType.CHAT,
                                       "forced_model", start, False, False)
            await self._persist(query, result, request_id)
            return result

        # 2. Автороутинг выключен → default_model
        if not self.auto_routing_enabled:
            result = self._make_result(self.default_model, force_task or TaskType.CHAT,
                                       "auto_routing_disabled_default", start, False, False)
            await self._persist(query, result, request_id)
            return result

        # 3. Детекция
        task_type = force_task or self.classifier.classify(query)

        # 4. Лучшая модель
        best = self._best_model(task_type)
        if best is not None:
            result = self._make_result(best.name, task_type, f"best_for_{task_type.value}",
                                       start, False, True)
            if self.emit_routing_decisions:   # M3: опциональный GREEN на happy path
                self._emit_event(RouterEvent(
                    level=EventLevel.GREEN, type=EventType.ROUTING_DECISION,
                    message=f"Routed to {best.name}",
                    payload={"task": task_type.value, "model": best.name}))
            await self._persist(query, result, request_id)
            return result

        # 5. Fallback на любую доступную routable
        available = [m for m in self.models.values() if m.is_available and m.is_routable]
        if available:
            fb = max(available, key=lambda m: (m.priority, m.name))
            self._fallbacks += 1
            self._emit_event(RouterEvent(
                level=EventLevel.GREEN, type=EventType.FALLBACK_USED,
                message=f"Нет специалиста для {task_type.value}, использован {fb.name}",
                payload={"task": task_type.value, "model": fb.name}))
            self._check_fallback_rate_alert()
            result = self._make_result(fb.name, task_type, "fallback_no_specialist",
                                       start, True, True)
            await self._persist(query, result, request_id)
            return result

        # 6. Все недоступны → дефолт вслепую + RED (с cooldown)
        self._fallbacks += 1
        self._emit_all_down("Все модели недоступны, использован default_model вслепую",
                            {"default_model": self.default_model})
        result = self._make_result(self.default_model, task_type,
                                   "fallback_default_no_health", start, True, True)
        await self._persist(query, result, request_id)
        return result

    # ── Helpers ───────────────────────────────────────────────────────────

    def _make_result(self, model, task_type, reason, start, fallback, auto_routing) -> RouteResult:
        latency = round(time.time() - start, 3)
        self._update_stats(model, task_type, fallback)
        return RouteResult(model=model, task_type=task_type, reason=reason,
                           latency=latency, fallback_used=fallback, auto_routing=auto_routing)

    def _update_stats(self, model, task, fallback) -> None:
        self._by_model[model] = self._by_model.get(model, 0) + 1
        self._by_task[task.value] = self._by_task.get(task.value, 0) + 1
        self._recent_decisions.append(fallback)

    async def _persist(self, query, result, request_id=None) -> None:
        # C2: блокирующее дисковое I/O в пул потоков — не вешаем event loop
        if self.history_db is None:
            return
        await asyncio.to_thread(
            self.history_db.log_decision,
            query, result.task_type, result.model,
            result.fallback_used, result.auto_routing,
            result.latency * 1000, request_id)

    def _check_fallback_rate_alert(self) -> None:
        if len(self._recent_decisions) < self.fallback_alert_window:
            return
        rate = sum(self._recent_decisions) / len(self._recent_decisions)
        if rate < self.fallback_rate_threshold:
            return
        now = time.time()
        if now - self._last_fallback_alert_time < self.fallback_alert_cooldown_sec:
            return
        self._last_fallback_alert_time = now
        self._emit_event(RouterEvent(
            level=EventLevel.YELLOW, type=EventType.FALLBACK_RATE_HIGH,
            message=f"Fallback rate {rate:.0%} за последние {len(self._recent_decisions)} запросов",
            payload={"rate": rate, "window": len(self._recent_decisions),
                     "threshold": self.fallback_rate_threshold}))

    def _emit_event(self, event: RouterEvent) -> None:
        log_msg = f"[{event.level.value.upper()}] {event.type.value}: {event.message}"
        if event.level == EventLevel.GREEN:
            logger.info(log_msg)
        elif event.level == EventLevel.YELLOW:
            logger.warning(log_msg)
        else:
            logger.error(log_msg)
        if self.on_event is not None:
            try:
                self.on_event(event)
            except Exception as e:
                logger.error(f"on_event callback failed: {e}")

    # ── Latency ───────────────────────────────────────────────────────────

    def update_latency(self, model: str, latency: float) -> None:
        if model in self.models:
            old = self.models[model].avg_latency
            if old == 0.0:
                self.models[model].avg_latency = round(latency, 2)
            else:
                self.models[model].avg_latency = round(
                    old * (1 - self.ema_alpha) + latency * self.ema_alpha, 2)
        self._latencies.append(latency)

    # ── Stats ─────────────────────────────────────────────────────────────

    def stats(self) -> RouterStats:
        avg_latency = sum(self._latencies) / len(self._latencies) if self._latencies else 0.0
        fallback_rate = self._fallbacks / self._total if self._total else 0.0
        return RouterStats(
            total_requests=self._total, by_model=dict(self._by_model),
            by_task=dict(self._by_task), fallback_count=self._fallbacks,
            fallback_rate=round(fallback_rate, 3), avg_latency=round(avg_latency, 2))

    def model_status(self) -> Dict:
        return {name: {"available": m.is_available, "routable": m.is_routable,
                       "priority": m.priority, "tasks": [t.value for t in m.task_types],
                       "avg_latency": m.avg_latency, "requests": self._by_model.get(name, 0)}
                for name, m in self.models.items()}


# ═══════════════════════════════════════════════════════════════════════════
# Smoke-тест
# ═══════════════════════════════════════════════════════════════════════════

async def _smoke_test():
    init_logging()

    def telegram_stub(event: RouterEvent) -> None:
        if event.level in (EventLevel.YELLOW, EventLevel.RED):
            print(f"  TG push: [{event.level.value}] {event.message}")

    async with ModelRouter(
        auto_routing_enabled=True,
        history_db=RoutingHistoryDB(Path("data/router_history.db")),
        on_event=telegram_stub,
    ) as router:
        # эмулируем доступность (без Ollama)
        await router.health_check_all()
        for m in router.models.values():
            m.is_available = True
        router._initialized = True

        queries = [
            "напиши функцию для сортировки списка",
            "проанализируй этот документ",
            "быстро ответь да или нет",
            "найди уязвимости в коде",
            "расскажи историю про функциональное программирование",
            "придумай стихотворение про робота",
        ]
        print("Routing test:")
        for q in queries:
            r = await router.route(q)
            marker = " (fallback)" if r.fallback_used else ""
            print(f"  [{r.task_type.value:10}] -> {r.model:20}{marker}")

        # C3: report_failure
        print("\nreport_failure test:")
        router.report_failure("qwen3.6:27b")
        print(f"  после report_failure: qwen3.6:27b available="
              f"{router.models['qwen3.6:27b'].is_available}")

        # C4: guardian не маршрутизируется
        print("\nguardian вне пула (security при упавшем мозге):")
        r = await router.route("найди уязвимость", force_task=TaskType.SECURITY)
        print(f"  security-запрос -> {r.model} (qwen3guard НЕ должен быть генератором)")

        s = router.stats()
        print(f"\nStats: total={s.total_requests} fallbacks={s.fallback_count} rate={s.fallback_rate}")


if __name__ == "__main__":
    asyncio.run(_smoke_test())
