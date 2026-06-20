&quot;&quot;&quot;
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
&quot;&quot;&quot;

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
# Логирование — вызывается явно, защита от дубля handler&#x27;а
# ═══════════════════════════════════════════════════════════════════════════

_ROUTER_HANDLER_ID: Optional[int] = None


def init_logging(log_dir: Path = Path(&quot;data/logs&quot;)) -&gt; None:
    global _ROUTER_HANDLER_ID
    log_dir.mkdir(parents=True, exist_ok=True)
    if _ROUTER_HANDLER_ID is not None:
        try:
            logger.remove(_ROUTER_HANDLER_ID)
        except ValueError:
            pass
    _ROUTER_HANDLER_ID = logger.add(
        log_dir / &quot;model_router.log&quot;, rotation=&quot;10 MB&quot;, level=&quot;INFO&quot;, enqueue=True)


# ═══════════════════════════════════════════════════════════════════════════
# PII-маскирование query_preview (C7)
# ═══════════════════════════════════════════════════════════════════════════

_PII_PATTERNS = [
    re.compile(r&quot;\b[\w.+-]+@[\w-]+\.[\w.-]+\b&quot;),          # email
    re.compile(r&quot;\b(?:\+?\d[\d\-\s()]{7,}\d)\b&quot;),         # телефон
    re.compile(r&quot;\b[A-Za-z0-9_-]{32,}\b&quot;),               # длинные токены/ключи
]


def _mask_pii(text: str) -&gt; str:
    masked = text
    for pat in _PII_PATTERNS:
        masked = pat.sub(&quot;[PII]&quot;, masked)
    return masked


# ═══════════════════════════════════════════════════════════════════════════
# Типы задач и события
# ═══════════════════════════════════════════════════════════════════════════

class TaskType(str, Enum):
    CODE      = &quot;code&quot;
    ANALYSIS  = &quot;analysis&quot;
    CHAT      = &quot;chat&quot;
    FAST      = &quot;fast&quot;
    SECURITY  = &quot;security&quot;
    SUMMARIZE = &quot;summarize&quot;
    CREATIVE  = &quot;creative&quot;


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
    GREEN  = &quot;green&quot;
    YELLOW = &quot;yellow&quot;
    RED    = &quot;red&quot;


class EventType(str, Enum):
    ROUTING_DECISION       = &quot;routing_decision&quot;
    FALLBACK_USED          = &quot;fallback_used&quot;
    ALL_MODELS_UNAVAILABLE = &quot;all_models_unavailable&quot;
    FALLBACK_RATE_HIGH     = &quot;fallback_rate_high&quot;
    INFERENCE_FAILURE      = &quot;inference_failure&quot;   # C3


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
    def classify(self, query: str) -&gt; TaskType:
        ...


class KeywordTaskClassifier(TaskClassifier):
    TASK_PATTERNS: Dict[TaskType, List[str]] = {
        TaskType.CODE: [
            r&quot;\bкод\b&quot;, r&quot;\bкода\b&quot;, r&quot;\bкоде\b&quot;, r&quot;\bкодом\b&quot;,
            r&quot;\bфункци\w*\b&quot;, r&quot;\bкласс\b&quot;,
            r&quot;\bpython\b&quot;, r&quot;\bjavascript\b&quot;, r&quot;\btypescript\b&quot;,
            r&quot;\brust\b&quot;, r&quot;\bgolang\b&quot;,
            r&quot;\bнапиши код\b&quot;, r&quot;\bреализуй\b&quot;, r&quot;\bбаг\b&quot;,
            r&quot;\bошибка в коде\b&quot;, r&quot;\brefactor\b&quot;, r&quot;\bимплемент\w*\b&quot;,
            r&quot;\bfunction\b&quot;, r&quot;\bimplement\b&quot;, r&quot;\bdebug\b&quot;, r&quot;\bsyntax\b&quot;,
        ],
        TaskType.SECURITY: [
            r&quot;\bбезопасност\w*\b&quot;, r&quot;\bуязвимост\w*\b&quot;, r&quot;\bатак\w*\b&quot;,
            r&quot;\bвзлом\w*\b&quot;, r&quot;\bjailbreak\b&quot;, r&quot;\badversarial\b&quot;,
            r&quot;\bпентест\w*\b&quot;, r&quot;\bexploit\b&quot;, r&quot;\bxss\b&quot;, r&quot;\bsql injection\b&quot;,
            r&quot;\bsecurity\b&quot;, r&quot;\bvulnerability\b&quot;, r&quot;\baudit\b&quot;,
        ],
        TaskType.SUMMARIZE: [
            r&quot;\bсуммаризируй\b&quot;, r&quot;\bкратко\b&quot;, r&quot;\bрезюме\b&quot;,
            r&quot;\bsummary\b&quot;, r&quot;\bsummarize\b&quot;,
            r&quot;\bосновные мысли\b&quot;, r&quot;\bключевые моменты\b&quot;,
            r&quot;\btl;dr\b&quot;, r&quot;\bвкратце\b&quot;,
        ],
        TaskType.ANALYSIS: [
            r&quot;\bпроанализируй\b&quot;, r&quot;\bразбери\b&quot;, r&quot;\bобъясни\b&quot;,
            r&quot;\bсравни\b&quot;, r&quot;\bоцени\b&quot;,
            r&quot;\banalyze\b&quot;, r&quot;\bexplain\b&quot;, r&quot;\bcompare\b&quot;,
            r&quot;\bevaluate\b&quot;, r&quot;\bresearch\b&quot;,
        ],
        TaskType.CREATIVE: [
            r&quot;\bпридумай\b&quot;, r&quot;\bсочини\b&quot;, r&quot;\bнапиши текст\b&quot;,
            r&quot;\bистори\w*\b&quot;, r&quot;\bстихотворени\w*\b&quot;,
            r&quot;\bcreative\b&quot;, r&quot;\bwrite a story\b&quot;, r&quot;\bpoem\b&quot;, r&quot;\bgenerate\b&quot;,
        ],
        TaskType.FAST: [
            r&quot;\bбыстро\b&quot;, r&quot;\bкратко ответь\b&quot;, r&quot;\bодним словом\b&quot;,
            r&quot;\bда или нет\b&quot;,
            r&quot;\bquick\b&quot;, r&quot;\bbrief\b&quot;, r&quot;\bshort answer\b&quot;, r&quot;\byes or no\b&quot;,
        ],
    }

    def __init__(self):
        self._compiled: Dict[TaskType, List[re.Pattern]] = {
            task: [re.compile(p, re.IGNORECASE) for p in patterns]
            for task, patterns in self.TASK_PATTERNS.items()
        }

    def classify(self, query: str) -&gt; TaskType:
        scores: Dict[TaskType, int] = {}
        for task_type, patterns in self._compiled.items():
            score = sum(1 for p in patterns if p.search(query))
            if score &gt; 0:
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
    SCHEMA = &quot;&quot;&quot;
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
    &quot;&quot;&quot;

    def __init__(self, db_path: Path = Path(&quot;data/router_history.db&quot;)):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db_path = db_path
        with self._connect() as conn:
            conn.executescript(self.SCHEMA)
            conn.execute(&quot;PRAGMA journal_mode=WAL&quot;)

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(self.db_path, timeout=5.0)
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def log_decision(self, query, task_type, model_chosen, fallback_used,
                     auto_routing, latency_ms, request_id=None) -&gt; None:
        query_hash = hashlib.sha256(query.encode(&quot;utf-8&quot;)).hexdigest()[:16]
        query_preview = _mask_pii(query[:80])   # C7: маскирование PII
        try:
            with self._connect() as conn:
                conn.execute(
                    &quot;&quot;&quot;INSERT INTO routing_decisions
                       (timestamp, request_id, query_hash, query_preview, task_type,
                        model_chosen, fallback_used, auto_routing, latency_ms)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)&quot;&quot;&quot;,
                    (time.time(), request_id, query_hash, query_preview, task_type.value,
                     model_chosen, int(fallback_used), int(auto_routing), latency_ms))
        except sqlite3.Error as e:
            logger.error(f&quot;Failed to log routing decision: {e}&quot;)


# ═══════════════════════════════════════════════════════════════════════════
# Router
# ═══════════════════════════════════════════════════════════════════════════

class ModelRouter:
    &quot;&quot;&quot;
    Маршрутизация запросов к локальным LLM (Ollama) в архитектуре «Крепость».

        User → Охранник → Карантин → [Router + LLM] → Пост-процессор → User

    Контракт: вызвать health_check_all() до первого route(), ЛИБО положиться на
    авто-health при первом запросе (C1). is_available=False по умолчанию.
    &quot;&quot;&quot;

    # C4: синхронизировано с «Выбор моделей v1.3».
    # Основной мозг — Qwen3.6-27B dense; быстрая — Qwen3 4B.
    # Guardian (Qwen3Guard-Gen-4B) помечен is_routable=False — он НЕ генератор,
    # его вызывает security.py, в генеративный пул роутера он не входит.
    # Имена-теги Ollama — предполагаемые, сверить после `ollama list`.
    DEFAULT_MODELS: List[ModelConfig] = [
        ModelConfig(
            name=&quot;qwen3.6:27b&quot;,
            task_types=[TaskType.ANALYSIS, TaskType.CHAT, TaskType.CREATIVE,
                        TaskType.SUMMARIZE, TaskType.CODE, TaskType.SECURITY],
            priority=10, max_tokens=32768, temperature=0.7, timeout=180.0),
        ModelConfig(
            name=&quot;qwen3:4b&quot;,
            task_types=[TaskType.FAST, TaskType.CHAT],
            priority=5, max_tokens=8192, temperature=0.7, timeout=30.0),
        ModelConfig(
            name=&quot;qwen3guard-gen:4b&quot;,
            task_types=[TaskType.SECURITY],
            priority=1, max_tokens=4096, temperature=0.0, timeout=60.0,
            is_routable=False),   # C4: классификатор, не генератор
    ]

    def __init__(self, ollama_url=&quot;http://localhost:11434&quot;,
                 default_model=&quot;qwen3.6:27b&quot;, auto_routing_enabled=False,
                 classifier=None, history_db=None, on_event=None,
                 fallback_rate_threshold=0.30, fallback_alert_window=100,
                 fallback_alert_cooldown_sec=1800, ema_alpha=0.2,
                 health_check_cache_sec=30, all_down_alert_cooldown_sec=300,
                 emit_routing_decisions=False):
        self.ollama_url = ollama_url.rstrip(&quot;/&quot;)
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

    def _lock(self, attr: str) -&gt; asyncio.Lock:
        # lazy-init локов внутри running loop
        lk = getattr(self, attr)
        if lk is None:
            lk = asyncio.Lock()
            setattr(self, attr, lk)
        return lk

    async def _get_session(self) -&gt; aiohttp.ClientSession:
        async with self._lock(&quot;_session_lock&quot;):
            if self._session is None or self._session.closed:
                timeout = aiohttp.ClientTimeout(total=10, connect=5)
                self._session = aiohttp.ClientSession(timeout=timeout)
            return self._session

    async def close(self) -&gt; None:
        if self._session and not self._session.closed:
            await self._session.close()

    # ── Регистрация ──────────────────────────────────────────────────────────

    def _register_defaults(self) -&gt; None:
        for model in self.DEFAULT_MODELS:
            self.register_model(model)

    def register_model(self, config: ModelConfig) -&gt; None:
        old = self.models.get(config.name)
        if old is not None:
            logger.warning(f&quot;Model {config.name} re-registered — конфиг перезаписан&quot;)
            config.avg_latency = old.avg_latency   # S7: сохранить накопленную EMA
        self.models[config.name] = config
        logger.info(f&quot;Model registered | {config.name} | &quot;
                    f&quot;tasks={[t.value for t in config.task_types]} routable={config.is_routable}&quot;)

    # ── Health check (C3/C5/C6 + double-checked lock) ───────────────────────

    async def _fetch_available_models(self) -&gt; Optional[List[str]]:
        now = time.time()
        if (self._available_models_cache is not None
                and now - self._available_models_cache_time &lt; self.health_check_cache_sec):
            return self._available_models_cache

        async with self._lock(&quot;_cache_lock&quot;):
            # double-checked: пока ждали лок, другой коротин мог обновить кеш
            now = time.time()
            if (self._available_models_cache is not None
                    and now - self._available_models_cache_time &lt; self.health_check_cache_sec):
                return self._available_models_cache
            try:
                session = await self._get_session()
                async with session.get(f&quot;{self.ollama_url}/api/tags&quot;) as resp:
                    if resp.status != 200:
                        logger.warning(f&quot;Ollama API returned {resp.status} — держим старый кеш&quot;)
                        return self._available_models_cache   # C6: не ронять модели
                    try:
                        data = await resp.json()
                    except Exception:
                        logger.warning(&quot;Ollama вернула не-JSON — держим старый кеш&quot;)
                        return self._available_models_cache   # C6
                    available = [m[&quot;name&quot;] for m in data.get(&quot;models&quot;, [])]
                    self._available_models_cache = available
                    self._available_models_cache_time = time.time()
                    return available
            except Exception as e:
                logger.error(f&quot;Failed to fetch available models: {e}&quot;)
                return self._available_models_cache   # C6: transient → старый кеш, не None

    async def health_check_all(self) -&gt; Dict[str, bool]:
        available = await self._fetch_available_models()
        if available is None:
            # кеша нет вообще (первый запуск + Ollama недоступна) → честный RED
            status = {name: False for name in self.models}
            for name in self.models:
                self.models[name].is_available = False
            self._emit_all_down(&quot;Ollama API unreachable&quot;, {&quot;ollama_url&quot;: self.ollama_url})
            return status

        status = {}
        for name in self.models:
            is_available = name in available
            self.models[name].is_available = is_available
            status[name] = is_available

        available_count = sum(1 for v in status.values() if v)
        total = len(status)
        logger.info(f&quot;Health check | {available_count}/{total} models available&quot;)
        if available_count == 0:
            self._emit_all_down(f&quot;Все {total} моделей недоступны&quot;,
                                {&quot;models&quot;: list(self.models.keys())})
        return status

    def _emit_all_down(self, message: str, payload: dict) -&gt; None:
        # C5: cooldown на RED, чтобы не спамить при длительном Ollama down
        now = time.time()
        if now - self._last_all_down_alert &lt; self.all_down_alert_cooldown_sec:
            return
        self._last_all_down_alert = now
        self._emit_event(RouterEvent(
            level=EventLevel.RED, type=EventType.ALL_MODELS_UNAVAILABLE,
            message=message, payload=payload))

    def report_failure(self, model_name: str) -&gt; None:
        &quot;&quot;&quot;
        C3: обратная связь от вызывающего кода о падении модели в рантайме
        (OOM / Ollama 500). health_check видит только наличие в /api/tags, не
        работоспособность — без этого упавшая модель собирала бы весь трафик
        до следующего планового чека.
        &quot;&quot;&quot;
        if model_name in self.models:
            self.models[model_name].is_available = False
            logger.warning(f&quot;Inference failure reported: {model_name} → is_available=False&quot;)
            self._emit_event(RouterEvent(
                level=EventLevel.YELLOW, type=EventType.INFERENCE_FAILURE,
                message=f&quot;Сбой инференса {model_name} — модель отключена до следующего health-check&quot;,
                payload={&quot;failed_model&quot;: model_name}))

    # ── Routing ───────────────────────────────────────────────────────────

    def _best_model(self, task_type: TaskType) -&gt; Optional[ModelConfig]:
        candidates = [
            m for m in self.models.values()
            if task_type in m.task_types and m.is_available and m.is_routable  # C4
        ]
        if not candidates:
            return None
        # детерминированный tie-break: priority desc, latency asc, имя
        return max(candidates, key=lambda m: (
            m.priority,
            -m.avg_latency if m.avg_latency &gt; 0 else 0.0,
            m.name))

    async def route(self, query, force_task=None, force_model=None,
                    request_id=None) -&gt; RouteResult:
        # C1: первый route триггерит health, если не инициализирован вручную
        if not self._initialized:
            await self.health_check_all()
            self._initialized = True

        start = time.time()
        self._total += 1

        # 1. Ручной выбор — высший приоритет, но валидируем (раньше принимал любую строку)
        if force_model is not None:
            if force_model not in self.models:
                logger.warning(f&quot;force_model &#x27;{force_model}&#x27; не зарегистрирован — &quot;
                               f&quot;принят как экспертный override без гарантий&quot;)
            result = self._make_result(force_model, force_task or TaskType.CHAT,
                                       &quot;forced_model&quot;, start, False, False)
            await self._persist(query, result, request_id)
            return result

        # 2. Автороутинг выключен → default_model
        if not self.auto_routing_enabled:
            result = self._make_result(self.default_model, force_task or TaskType.CHAT,
                                       &quot;auto_routing_disabled_default&quot;, start, False, False)
            await self._persist(query, result, request_id)
            return result

        # 3. Детекция
        task_type = force_task or self.classifier.classify(query)

        # 4. Лучшая модель
        best = self._best_model(task_type)
        if best is not None:
            result = self._make_result(best.name, task_type, f&quot;best_for_{task_type.value}&quot;,
                                       start, False, True)
            if self.emit_routing_decisions:   # M3: опциональный GREEN на happy path
                self._emit_event(RouterEvent(
                    level=EventLevel.GREEN, type=EventType.ROUTING_DECISION,
                    message=f&quot;Routed to {best.name}&quot;,
                    payload={&quot;task&quot;: task_type.value, &quot;model&quot;: best.name}))
            await self._persist(query, result, request_id)
            return result

        # 5. Fallback на любую доступную routable
        available = [m for m in self.models.values() if m.is_available and m.is_routable]
        if available:
            fb = max(available, key=lambda m: (m.priority, m.name))
            self._fallbacks += 1
            self._emit_event(RouterEvent(
                level=EventLevel.GREEN, type=EventType.FALLBACK_USED,
                message=f&quot;Нет специалиста для {task_type.value}, использован {fb.name}&quot;,
                payload={&quot;task&quot;: task_type.value, &quot;model&quot;: fb.name}))
            self._check_fallback_rate_alert()
            result = self._make_result(fb.name, task_type, &quot;fallback_no_specialist&quot;,
                                       start, True, True)
            await self._persist(query, result, request_id)
            return result

        # 6. Все недоступны → дефолт вслепую + RED (с cooldown)
        self._fallbacks += 1
        self._emit_all_down(&quot;Все модели недоступны, использован default_model вслепую&quot;,
                            {&quot;default_model&quot;: self.default_model})
        result = self._make_result(self.default_model, task_type,
                                   &quot;fallback_default_no_health&quot;, start, True, True)
        await self._persist(query, result, request_id)
        return result

    # ── Helpers ───────────────────────────────────────────────────────────

    def _make_result(self, model, task_type, reason, start, fallback, auto_routing) -&gt; RouteResult:
        latency = round(time.time() - start, 3)
        self._update_stats(model, task_type, fallback)
        return RouteResult(model=model, task_type=task_type, reason=reason,
                           latency=latency, fallback_used=fallback, auto_routing=auto_routing)

    def _update_stats(self, model, task, fallback) -&gt; None:
        self._by_model[model] = self._by_model.get(model, 0) + 1
        self._by_task[task.value] = self._by_task.get(task.value, 0) + 1
        self._recent_decisions.append(fallback)

    async def _persist(self, query, result, request_id=None) -&gt; None:
        # C2: блокирующее дисковое I/O в пул потоков — не вешаем event loop
        if self.history_db is None:
            return
        await asyncio.to_thread(
            self.history_db.log_decision,
            query, result.task_type, result.model,
            result.fallback_used, result.auto_routing,
            result.latency * 1000, request_id)

    def _check_fallback_rate_alert(self) -&gt; None:
        if len(self._recent_decisions) &lt; self.fallback_alert_window:
            return
        rate = sum(self._recent_decisions) / len(self._recent_decisions)
        if rate &lt; self.fallback_rate_threshold:
            return
        now = time.time()
        if now - self._last_fallback_alert_time &lt; self.fallback_alert_cooldown_sec:
            return
        self._last_fallback_alert_time = now
        self._emit_event(RouterEvent(
            level=EventLevel.YELLOW, type=EventType.FALLBACK_RATE_HIGH,
            message=f&quot;Fallback rate {rate:.0%} за последние {len(self._recent_decisions)} запросов&quot;,
            payload={&quot;rate&quot;: rate, &quot;window&quot;: len(self._recent_decisions),
                     &quot;threshold&quot;: self.fallback_rate_threshold}))

    def _emit_event(self, event: RouterEvent) -&gt; None:
        log_msg = f&quot;[{event.level.value.upper()}] {event.type.value}: {event.message}&quot;
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
                logger.error(f&quot;on_event callback failed: {e}&quot;)

    # ── Latency ───────────────────────────────────────────────────────────

    def update_latency(self, model: str, latency: float) -&gt; None:
        if model in self.models:
            old = self.models[model].avg_latency
            if old == 0.0:
                self.models[model].avg_latency = round(latency, 2)
            else:
                self.models[model].avg_latency = round(
                    old * (1 - self.ema_alpha) + latency * self.ema_alpha, 2)
        self._latencies.append(latency)

    # ── Stats ─────────────────────────────────────────────────────────────

    def stats(self) -&gt; RouterStats:
        avg_latency = sum(self._latencies) / len(self._latencies) if self._latencies else 0.0
        fallback_rate = self._fallbacks / self._total if self._total else 0.0
        return RouterStats(
            total_requests=self._total, by_model=dict(self._by_model),
            by_task=dict(self._by_task), fallback_count=self._fallbacks,
            fallback_rate=round(fallback_rate, 3), avg_latency=round(avg_latency, 2))

    def model_status(self) -&gt; Dict:
        return {name: {&quot;available&quot;: m.is_available, &quot;routable&quot;: m.is_routable,
                       &quot;priority&quot;: m.priority, &quot;tasks&quot;: [t.value for t in m.task_types],
                       &quot;avg_latency&quot;: m.avg_latency, &quot;requests&quot;: self._by_model.get(name, 0)}
                for name, m in self.models.items()}


# ═══════════════════════════════════════════════════════════════════════════
# Smoke-тест
# ═══════════════════════════════════════════════════════════════════════════

async def _smoke_test():
    init_logging()

    def telegram_stub(event: RouterEvent) -&gt; None:
        if event.level in (EventLevel.YELLOW, EventLevel.RED):
            print(f&quot;  TG push: [{event.level.value}] {event.message}&quot;)

    async with ModelRouter(
        auto_routing_enabled=True,
        history_db=RoutingHistoryDB(Path(&quot;data/router_history.db&quot;)),
        on_event=telegram_stub,
    ) as router:
        # эмулируем доступность (без Ollama)
        await router.health_check_all()
        for m in router.models.values():
            m.is_available = True
        router._initialized = True

        queries = [
            &quot;напиши функцию для сортировки списка&quot;,
            &quot;проанализируй этот документ&quot;,
            &quot;быстро ответь да или нет&quot;,
            &quot;найди уязвимости в коде&quot;,
            &quot;расскажи историю про функциональное программирование&quot;,
            &quot;придумай стихотворение про робота&quot;,
        ]
        print(&quot;Routing test:&quot;)
        for q in queries:
            r = await router.route(q)
            marker = &quot; (fallback)&quot; if r.fallback_used else &quot;&quot;
            print(f&quot;  [{r.task_type.value:10}] -&gt; {r.model:20}{marker}&quot;)

        # C3: report_failure
        print(&quot;\nreport_failure test:&quot;)
        router.report_failure(&quot;qwen3.6:27b&quot;)
        print(f&quot;  после report_failure: qwen3.6:27b available=&quot;
              f&quot;{router.models[&#x27;qwen3.6:27b&#x27;].is_available}&quot;)

        # C4: guardian не маршрутизируется
        print(&quot;\nguardian вне пула (security при упавшем мозге):&quot;)
        r = await router.route(&quot;найди уязвимость&quot;, force_task=TaskType.SECURITY)
        print(f&quot;  security-запрос -&gt; {r.model} (qwen3guard НЕ должен быть генератором)&quot;)

        s = router.stats()
        print(f&quot;\nStats: total={s.total_requests} fallbacks={s.fallback_count} rate={s.fallback_rate}&quot;)


if __name__ == &quot;__main__&quot;:
    asyncio.run(_smoke_test())
