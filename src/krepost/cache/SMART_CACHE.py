"""
krepost/cache/smart_cache.py
Smart Cache v2.1 — трёхслойный кэш для архитектуры «Крепость».

Положение в пайплайне:
    User → Охранник → Карантин →
        [L1 embedding] → [L2 RAG] → [L3 LLM] →
        Учитель (только при L3 miss) → Пост-процессор → User

Слои:
- L1 QueryEmbeddingCache  — exact match по хешу запроса. Lookup O(1).
- L2 RAGResultsCache      — semantic match по эмбеддингу (cosine ≥ 0.92).
- L3 LLMResponseCache     — exact match по (query + context + model + prompt_version).

Безопасность:
- L1 кэшируется всегда (хеш→вектор, опасности нет).
- L2 и L3 кэшируют только запросы с verdict=GREEN.
- При verdict=YELLOW/RED — no-op, ничего не сохраняется.

Инвалидация:
- L1: только при смене embedding-модели (metadata-check).
- L2: по изменению заметки Obsidian через invalidate_by_note(path).
- L3: каскадно через CacheLayer.invalidate_by_note + TTL.

Изменения v2.0 → v2.1 (свод 5 черновиков + прогон кода + pytest):
  FIX-1  _atomic_write_npz: temp-имя кончается на .npz (numpy дописывает .npz сам;
         иначе реальный файл *.npz.tmp.npz, а replace() ищет *.npz.tmp → краш L1/L2).
  FIX-2  AnomalyDetector вынесен в отдельный класс (тестируемость + чистый API).
  FIX-3  growth-формула: окно < 60с → абсолютный порог за окно (без экстраполяции
         в минуту, иначе 10 puts за 0.5с → 1200/мин = мнимый флуд); окно ≥ 60с →
         нормировка к минуте.
  FIX-4  CacheLayer API согласован с тестами: is_ready, l1_max_entries,
         {l2_removed,l3_removed}, stats()["anomaly"], lazy warmup.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Callable, Dict, List, Optional, Set

import numpy as np
from loguru import logger
from pydantic import BaseModel, Field
from sentence_transformers import SentenceTransformer


def init_logging(log_dir: Path = Path("data/logs")) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    logger.add(log_dir / "smart_cache.log", rotation="10 MB", level="INFO", enqueue=True)


def init_cache_dirs(cache_dir: Path = Path("data/cache")) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)


class SecurityVerdict(str, Enum):
    GREEN = "green"
    YELLOW = "yellow"
    RED = "red"


class CacheLevel(str, Enum):
    L1_EMBEDDING = "l1_embedding"
    L2_RAG = "l2_rag"
    L3_LLM = "l3_llm"


class EventLevel(str, Enum):
    GREEN = "green"
    YELLOW = "yellow"
    RED = "red"


class CacheEventType(str, Enum):
    HIT = "hit"
    MISS = "miss"
    PUT = "put"
    EVICTED = "evicted"
    INVALIDATED_BY_NOTE = "invalidated_by_note"
    MISS_RATE_HIGH = "miss_rate_high"
    GROWTH_ANOMALY = "growth_anomaly"
    MASS_INVALIDATION = "mass_invalidation"
    MODEL_MISMATCH = "model_mismatch"


@dataclass
class CacheEvent:
    level: EventLevel
    type: CacheEventType
    layer: CacheLevel
    message: str
    payload: dict = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)


class L1Entry(BaseModel):
    key: str
    query_preview: str
    timestamp: float = Field(default_factory=time.time)
    last_accessed_at: float = Field(default_factory=time.time)
    hits: int = 0


class L2Entry(BaseModel):
    key: str
    query_preview: str
    chunks: List[Dict]
    source_notes: List[str]
    timestamp: float = Field(default_factory=time.time)
    last_accessed_at: float = Field(default_factory=time.time)
    ttl: float = 86400.0 * 7
    hits: int = 0


class L3Entry(BaseModel):
    key: str
    query_preview: str
    response: str
    model: str
    context_hash: str
    prompt_version: str
    source_notes: List[str]
    timestamp: float = Field(default_factory=time.time)
    last_accessed_at: float = Field(default_factory=time.time)
    ttl: float = 86400.0
    hits: int = 0


class CacheStats(BaseModel):
    layer: CacheLevel
    entries: int
    hits: int
    misses: int
    hit_rate: float
    last_event_at_iso: Optional[str] = None


class _CacheBase:
    LAYER: CacheLevel

    def __init__(self, cache_dir: Path, max_entries: int,
                 on_event: Optional[Callable[[CacheEvent], None]] = None):
        self.cache_dir = cache_dir
        self.max_entries = max_entries
        self.on_event = on_event
        self._hits = 0
        self._misses = 0
        self._last_event_at: Optional[float] = None
        self._writeback_counter = 0
        self._writeback_every = 20

    def _emit(self, event_type: CacheEventType, level: EventLevel,
              message: str, payload: Optional[dict] = None) -> None:
        evt = CacheEvent(level=level, type=event_type, layer=self.LAYER,
                         message=message, payload=payload or {})
        self._last_event_at = evt.timestamp
        log_msg = f"[{level.value.upper()}] {self.LAYER.value}/{event_type.value}: {message}"
        if level == EventLevel.GREEN:
            logger.debug(log_msg)
        elif level == EventLevel.YELLOW:
            logger.warning(log_msg)
        else:
            logger.error(log_msg)
        if self.on_event is not None:
            try:
                self.on_event(evt)
            except Exception as e:
                logger.error(f"on_event callback failed: {e}")

    def _atomic_write(self, path: Path, content: str) -> None:
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(content, encoding="utf-8")
        tmp.replace(path)

    def _atomic_write_npz(self, path: Path, arrays: Dict[str, np.ndarray]) -> None:
        # FIX-1: numpy принудительно добавляет .npz, если имя на него не кончается.
        # Поэтому temp-имя ДОЛЖНО кончаться на .npz, иначе реальный файл будет
        # *.npz.tmp.npz, а replace() будет искать *.npz.tmp → FileNotFoundError.
        tmp = path.with_suffix(".tmp.npz")
        np.savez_compressed(tmp, **arrays)
        tmp.replace(path)


class QueryEmbeddingCache(_CacheBase):
    LAYER = CacheLevel.L1_EMBEDDING
    QUERY_PREFIX = "query: "

    def __init__(self, encoder: SentenceTransformer, cache_dir: Path = Path("data/cache"),
                 max_entries: int = 10_000,
                 on_event: Optional[Callable[[CacheEvent], None]] = None):
        super().__init__(cache_dir=cache_dir, max_entries=max_entries, on_event=on_event)
        self.encoder = encoder
        self.dim = encoder.get_sentence_embedding_dimension()
        self.model_name = encoder._first_module().auto_model.config._name_or_path
        self._embeddings: Dict[str, np.ndarray] = {}
        self._entries: Dict[str, L1Entry] = {}
        self._jsonl_path = cache_dir / "l1_entries.jsonl"
        self._npz_path = cache_dir / "l1_embeddings.npz"
        self._meta_path = cache_dir / "l1_metadata.json"
        self._load()

    def _hash(self, query: str) -> str:
        return hashlib.sha256(query.encode("utf-8")).hexdigest()[:16]

    def _check_compat(self) -> bool:
        if not self._meta_path.exists():
            return False
        try:
            meta = json.loads(self._meta_path.read_text(encoding="utf-8"))
            return meta.get("model") == self.model_name and meta.get("dim") == self.dim
        except Exception:
            return False

    def _save_metadata(self) -> None:
        meta = {"model": self.model_name, "dim": self.dim,
                "layer": self.LAYER.value, "version": "2.1"}
        self._atomic_write(self._meta_path, json.dumps(meta, ensure_ascii=False, indent=2))

    def _load(self) -> None:
        if not self._check_compat():
            if self._meta_path.exists():
                self._emit(CacheEventType.MODEL_MISMATCH, EventLevel.YELLOW,
                           "Embedding model изменилась, L1 кэш сбрасывается")
            self._save_metadata()
            return
        if self._jsonl_path.exists():
            for line in self._jsonl_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = L1Entry.model_validate_json(line)
                    self._entries[entry.key] = entry
                except Exception as e:
                    logger.warning(f"L1: skip bad entry: {e}")
        if self._npz_path.exists():
            try:
                data = np.load(self._npz_path)
                for key in data.files:
                    if key in self._entries:
                        self._embeddings[key] = data[key]
            except Exception as e:
                logger.warning(f"L1: failed to load embeddings: {e}")
        valid_keys = set(self._entries) & set(self._embeddings)
        self._entries = {k: self._entries[k] for k in valid_keys}
        self._embeddings = {k: self._embeddings[k] for k in valid_keys}
        logger.info(f"L1 loaded | entries={len(self._entries)}")

    async def encode(self, query: str) -> np.ndarray:
        key = self._hash(query)
        if key in self._embeddings:
            entry = self._entries[key]
            entry.hits += 1
            entry.last_accessed_at = time.time()
            self._hits += 1
            self._emit(CacheEventType.HIT, EventLevel.GREEN, f"L1 hit | query={query[:50]}",
                       payload={"key": key, "hits": entry.hits})
            self._maybe_writeback()
            return self._embeddings[key]
        self._misses += 1
        self._emit(CacheEventType.MISS, EventLevel.GREEN, f"L1 miss | query={query[:50]}")
        text = self.QUERY_PREFIX + query
        embedding = await asyncio.to_thread(self.encoder.encode, text,
                                            convert_to_numpy=True, normalize_embeddings=True)
        self._put(key, query, embedding)
        return embedding

    def _put(self, key: str, query: str, embedding: np.ndarray) -> None:
        if len(self._entries) >= self.max_entries:
            self._evict(target_size=self.max_entries - 1)
        entry = L1Entry(key=key, query_preview=query[:80])
        self._entries[key] = entry
        self._embeddings[key] = embedding
        self._writeback_counter += 1
        self._save_entry_append(entry)
        self._save_embeddings()
        self._emit(CacheEventType.PUT, EventLevel.GREEN, f"L1 put | key={key}")

    def _save_entry_append(self, entry: L1Entry) -> None:
        with open(self._jsonl_path, "a", encoding="utf-8") as f:
            f.write(entry.model_dump_json() + "\n")

    def _save_embeddings(self) -> None:
        if not self._embeddings:
            return
        self._atomic_write_npz(self._npz_path, self._embeddings)

    def _maybe_writeback(self) -> None:
        self._writeback_counter += 1
        if self._writeback_counter >= self._writeback_every:
            self._writeback_counter = 0
            self._full_rewrite()

    def _full_rewrite(self) -> None:
        content = "\n".join(e.model_dump_json() for e in self._entries.values()) + "\n"
        self._atomic_write(self._jsonl_path, content)

    def _evict(self, target_size: int) -> None:
        if len(self._entries) <= target_size:
            return
        sorted_by_lru = sorted(self._entries.items(), key=lambda kv: kv[1].last_accessed_at)
        n_remove = len(self._entries) - target_size
        for key, _ in sorted_by_lru[:n_remove]:
            del self._entries[key]
            self._embeddings.pop(key, None)
        self._emit(CacheEventType.EVICTED, EventLevel.GREEN, f"L1 evicted {n_remove} entries")
        self._full_rewrite()
        self._save_embeddings()

    def stats(self) -> CacheStats:
        total = self._hits + self._misses
        return CacheStats(layer=self.LAYER, entries=len(self._entries), hits=self._hits,
                          misses=self._misses, hit_rate=round(self._hits / total, 3) if total else 0.0,
                          last_event_at_iso=(datetime.fromtimestamp(self._last_event_at, tz=timezone.utc).isoformat()
                                             if self._last_event_at else None))

    def close(self) -> None:
        self._full_rewrite()
        self._save_embeddings()
        self._save_metadata()


class RAGResultsCache(_CacheBase):
    LAYER = CacheLevel.L2_RAG

    def __init__(self, l1_cache: QueryEmbeddingCache, cache_dir: Path = Path("data/cache"),
                 max_entries: int = 5_000, similarity_threshold: float = 0.92,
                 default_ttl: float = 86400.0 * 7,
                 on_event: Optional[Callable[[CacheEvent], None]] = None):
        super().__init__(cache_dir=cache_dir, max_entries=max_entries, on_event=on_event)
        self.l1 = l1_cache
        self.threshold = similarity_threshold
        self.default_ttl = default_ttl
        self._entries: Dict[str, L2Entry] = {}
        self._embeddings: Dict[str, np.ndarray] = {}
        self._note_to_keys: Dict[str, Set[str]] = {}
        self._matrix_keys: List[str] = []
        self._matrix: Optional[np.ndarray] = None
        self._matrix_dirty = True
        self._jsonl_path = cache_dir / "l2_entries.jsonl"
        self._npz_path = cache_dir / "l2_embeddings.npz"
        self._load()

    def _hash(self, query: str) -> str:
        return hashlib.sha256(query.encode("utf-8")).hexdigest()[:16]

    def _is_expired(self, entry: L2Entry) -> bool:
        return time.time() - entry.timestamp > entry.ttl

    def _rebuild_reverse_index(self) -> None:
        self._note_to_keys.clear()
        for key, entry in self._entries.items():
            for note in entry.source_notes:
                self._note_to_keys.setdefault(note, set()).add(key)

    def _rebuild_matrix(self) -> None:
        if not self._embeddings:
            self._matrix_keys = []
            self._matrix = None
        else:
            self._matrix_keys = list(self._embeddings.keys())
            self._matrix = np.array([self._embeddings[k] for k in self._matrix_keys])
        self._matrix_dirty = False

    def _load(self) -> None:
        if self._jsonl_path.exists():
            for line in self._jsonl_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = L2Entry.model_validate_json(line)
                    if not self._is_expired(entry):
                        self._entries[entry.key] = entry
                except Exception as e:
                    logger.warning(f"L2: skip bad entry: {e}")
        if self._npz_path.exists():
            try:
                data = np.load(self._npz_path)
                for key in data.files:
                    if key in self._entries:
                        self._embeddings[key] = data[key]
            except Exception as e:
                logger.warning(f"L2: failed to load embeddings: {e}")
        valid_keys = set(self._entries) & set(self._embeddings)
        self._entries = {k: self._entries[k] for k in valid_keys}
        self._embeddings = {k: self._embeddings[k] for k in valid_keys}
        self._rebuild_reverse_index()
        self._matrix_dirty = True
        logger.info(f"L2 loaded | entries={len(self._entries)}")

    async def get(self, query: str) -> Optional[L2Entry]:
        if not self._entries:
            self._misses += 1
            return None
        if self._matrix_dirty:
            self._rebuild_matrix()
        if self._matrix is None or len(self._matrix) == 0:
            self._misses += 1
            return None
        query_emb = await self.l1.encode(query)
        scores = self._matrix @ query_emb
        best_idx = int(np.argmax(scores))
        best_score = float(scores[best_idx])
        if best_score >= self.threshold:
            key = self._matrix_keys[best_idx]
            entry = self._entries[key]
            if self._is_expired(entry):
                self._remove(key)
                self._misses += 1
                return None
            entry.hits += 1
            entry.last_accessed_at = time.time()
            self._hits += 1
            self._emit(CacheEventType.HIT, EventLevel.GREEN, f"L2 hit | score={best_score:.3f}",
                       payload={"key": key, "score": best_score})
            return entry
        self._misses += 1
        self._emit(CacheEventType.MISS, EventLevel.GREEN, f"L2 miss | best_score={best_score:.3f}")
        return None

    async def put(self, query: str, chunks: List[Dict], source_notes: List[str],
                  verdict: SecurityVerdict, ttl: Optional[float] = None) -> Optional[str]:
        if verdict != SecurityVerdict.GREEN:
            return None
        key = self._hash(query)
        if key in self._entries and not self._is_expired(self._entries[key]):
            return key
        if len(self._entries) >= self.max_entries:
            self._evict(target_size=self.max_entries - 1)
        embedding = await self.l1.encode(query)
        entry = L2Entry(key=key, query_preview=query[:80], chunks=chunks,
                        source_notes=source_notes, ttl=ttl if ttl is not None else self.default_ttl)
        self._entries[key] = entry
        self._embeddings[key] = embedding
        self._matrix_dirty = True
        for note in source_notes:
            self._note_to_keys.setdefault(note, set()).add(key)
        self._save_entry_append(entry)
        self._save_embeddings()
        self._emit(CacheEventType.PUT, EventLevel.GREEN, f"L2 put | key={key}")
        return key

    def invalidate_by_note(self, note_path: str) -> int:
        keys = self._note_to_keys.pop(note_path, set())
        for key in keys:
            self._remove(key, skip_index=True)
        if keys:
            self._full_rewrite()
            self._save_embeddings()
            level = EventLevel.YELLOW if len(keys) > 500 else EventLevel.GREEN
            event_type = (CacheEventType.MASS_INVALIDATION if len(keys) > 500
                          else CacheEventType.INVALIDATED_BY_NOTE)
            self._emit(event_type, level,
                       f"L2 invalidated {len(keys)} entries for note {note_path}",
                       payload={"note": note_path, "count": len(keys)})
        return len(keys)

    def _remove(self, key: str, skip_index: bool = False) -> None:
        entry = self._entries.pop(key, None)
        self._embeddings.pop(key, None)
        self._matrix_dirty = True
        if entry and not skip_index:
            for note in entry.source_notes:
                if note in self._note_to_keys:
                    self._note_to_keys[note].discard(key)
                    if not self._note_to_keys[note]:
                        del self._note_to_keys[note]

    def _evict(self, target_size: int) -> None:
        expired = [k for k, e in self._entries.items() if self._is_expired(e)]
        for k in expired:
            self._remove(k)
        if len(self._entries) > target_size:
            sorted_by_lru = sorted(self._entries.items(), key=lambda kv: kv[1].last_accessed_at)
            n_remove = len(self._entries) - target_size
            for key, _ in sorted_by_lru[:n_remove]:
                self._remove(key)
        self._full_rewrite()
        self._save_embeddings()

    def _save_entry_append(self, entry: L2Entry) -> None:
        with open(self._jsonl_path, "a", encoding="utf-8") as f:
            f.write(entry.model_dump_json() + "\n")

    def _save_embeddings(self) -> None:
        if not self._embeddings:
            return
        self._atomic_write_npz(self._npz_path, self._embeddings)

    def _full_rewrite(self) -> None:
        content = "\n".join(e.model_dump_json() for e in self._entries.values())
        if content:
            content += "\n"
        self._atomic_write(self._jsonl_path, content)

    def stats(self) -> CacheStats:
        total = self._hits + self._misses
        return CacheStats(layer=self.LAYER, entries=len(self._entries), hits=self._hits,
                          misses=self._misses, hit_rate=round(self._hits / total, 3) if total else 0.0,
                          last_event_at_iso=(datetime.fromtimestamp(self._last_event_at, tz=timezone.utc).isoformat()
                                             if self._last_event_at else None))

    def close(self) -> None:
        self._full_rewrite()
        self._save_embeddings()


class LLMResponseCache(_CacheBase):
    LAYER = CacheLevel.L3_LLM

    def __init__(self, cache_dir: Path = Path("data/cache"), max_entries: int = 2_000,
                 default_ttl: float = 86400.0, prompt_version: str = "v1",
                 on_event: Optional[Callable[[CacheEvent], None]] = None):
        super().__init__(cache_dir=cache_dir, max_entries=max_entries, on_event=on_event)
        self.default_ttl = default_ttl
        self.prompt_version = prompt_version
        self._entries: Dict[str, L3Entry] = {}
        self._note_to_keys: Dict[str, Set[str]] = {}
        self._jsonl_path = cache_dir / "l3_entries.jsonl"
        self._load()

    def _make_key(self, query: str, context_hash: str, model: str) -> str:
        material = f"{self.prompt_version}|{model}|{context_hash}|{query}"
        return hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]

    def _is_expired(self, entry: L3Entry) -> bool:
        return time.time() - entry.timestamp > entry.ttl

    def _load(self) -> None:
        if self._jsonl_path.exists():
            for line in self._jsonl_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = L3Entry.model_validate_json(line)
                    if entry.prompt_version != self.prompt_version:
                        continue
                    if not self._is_expired(entry):
                        self._entries[entry.key] = entry
                except Exception as e:
                    logger.warning(f"L3: skip bad entry: {e}")
        for key, entry in self._entries.items():
            for note in entry.source_notes:
                self._note_to_keys.setdefault(note, set()).add(key)
        logger.info(f"L3 loaded | entries={len(self._entries)}")

    def get(self, query: str, context_hash: str, model: str) -> Optional[L3Entry]:
        key = self._make_key(query, context_hash, model)
        entry = self._entries.get(key)
        if entry is None:
            self._misses += 1
            self._emit(CacheEventType.MISS, EventLevel.GREEN, f"L3 miss | query={query[:50]}")
            return None
        if self._is_expired(entry):
            self._remove(key)
            self._misses += 1
            return None
        entry.hits += 1
        entry.last_accessed_at = time.time()
        self._hits += 1
        self._emit(CacheEventType.HIT, EventLevel.GREEN, f"L3 hit | query={query[:50]}",
                   payload={"key": key, "hits": entry.hits})
        return entry

    def put(self, query: str, response: str, context_hash: str, model: str,
            source_notes: List[str], verdict: SecurityVerdict,
            ttl: Optional[float] = None) -> Optional[str]:
        if verdict != SecurityVerdict.GREEN:
            return None
        key = self._make_key(query, context_hash, model)
        if key in self._entries and not self._is_expired(self._entries[key]):
            return key
        if len(self._entries) >= self.max_entries:
            self._evict(target_size=self.max_entries - 1)
        entry = L3Entry(key=key, query_preview=query[:80], response=response, model=model,
                        context_hash=context_hash, prompt_version=self.prompt_version,
                        source_notes=source_notes, ttl=ttl if ttl is not None else self.default_ttl)
        self._entries[key] = entry
        for note in source_notes:
            self._note_to_keys.setdefault(note, set()).add(key)
        self._save_entry_append(entry)
        self._emit(CacheEventType.PUT, EventLevel.GREEN, f"L3 put | model={model}")
        return key

    def invalidate_by_note(self, note_path: str) -> int:
        keys = self._note_to_keys.pop(note_path, set())
        for key in keys:
            self._remove(key, skip_index=True)
        if keys:
            self._full_rewrite()
            level = EventLevel.YELLOW if len(keys) > 500 else EventLevel.GREEN
            event_type = (CacheEventType.MASS_INVALIDATION if len(keys) > 500
                          else CacheEventType.INVALIDATED_BY_NOTE)
            self._emit(event_type, level,
                       f"L3 invalidated {len(keys)} entries for note {note_path}",
                       payload={"note": note_path, "count": len(keys)})
        return len(keys)

    def _remove(self, key: str, skip_index: bool = False) -> None:
        entry = self._entries.pop(key, None)
        if entry and not skip_index:
            for note in entry.source_notes:
                if note in self._note_to_keys:
                    self._note_to_keys[note].discard(key)
                    if not self._note_to_keys[note]:
                        del self._note_to_keys[note]

    def _evict(self, target_size: int) -> None:
        expired = [k for k, e in self._entries.items() if self._is_expired(e)]
        for k in expired:
            self._remove(k)
        if len(self._entries) > target_size:
            sorted_by_lru = sorted(self._entries.items(), key=lambda kv: kv[1].last_accessed_at)
            n_remove = len(self._entries) - target_size
            for key, _ in sorted_by_lru[:n_remove]:
                self._remove(key)
        self._full_rewrite()

    def _save_entry_append(self, entry: L3Entry) -> None:
        with open(self._jsonl_path, "a", encoding="utf-8") as f:
            f.write(entry.model_dump_json() + "\n")

    def _full_rewrite(self) -> None:
        content = "\n".join(e.model_dump_json() for e in self._entries.values())
        if content:
            content += "\n"
        self._atomic_write(self._jsonl_path, content)

    def stats(self) -> CacheStats:
        total = self._hits + self._misses
        return CacheStats(layer=self.LAYER, entries=len(self._entries), hits=self._hits,
                          misses=self._misses, hit_rate=round(self._hits / total, 3) if total else 0.0,
                          last_event_at_iso=(datetime.fromtimestamp(self._last_event_at, tz=timezone.utc).isoformat()
                                             if self._last_event_at else None))

    def close(self) -> None:
        self._full_rewrite()


@dataclass
class _SlidingWindow:
    """Кольцевой буфер timestamp'ов для подсчёта событий в плавающем окне."""
    window_seconds: float
    _buckets: deque = field(default_factory=deque)

    def add(self, ts: float) -> None:
        self._buckets.append(ts)

    def count(self, now: float) -> int:
        cutoff = now - self.window_seconds
        while self._buckets and self._buckets[0] < cutoff:
            self._buckets.popleft()
        return len(self._buckets)


class AnomalyDetector:
    """
    Детектирует аномалии кэша (только эмитит события, никогда не блокирует):
      - GROWTH_ANOMALY  — слишком быстрый рост (потенциальный cache flood)
      - MISS_RATE_HIGH  — высокий miss rate (атака уникальными запросами / деградация)
    """

    def __init__(self, growth_threshold_per_min: int = 60,
                 miss_rate_threshold: float = 0.90,
                 window_seconds: float = 300.0,
                 on_event: Optional[Callable[[CacheEvent], None]] = None):
        self.growth_threshold = growth_threshold_per_min
        self.miss_rate_threshold = miss_rate_threshold
        self.window_seconds = window_seconds
        self.on_event = on_event
        self._put_timestamps = _SlidingWindow(window_seconds)
        self._hit_timestamps = _SlidingWindow(window_seconds)
        self._miss_timestamps = _SlidingWindow(window_seconds)
        self._last_check_time = time.time()
        self._check_interval = 30.0

    def record_put(self) -> None:
        self._put_timestamps.add(time.time())
        self._maybe_check()

    def record_hit(self) -> None:
        self._hit_timestamps.add(time.time())
        self._maybe_check()

    def record_miss(self) -> None:
        self._miss_timestamps.add(time.time())
        self._maybe_check()

    def _maybe_check(self) -> None:
        now = time.time()
        if now - self._last_check_time < self._check_interval:
            return
        self._last_check_time = now
        self._check(now)

    def _check(self, now: float) -> None:
        if self.on_event is None:
            return
        puts_in_window = self._put_timestamps.count(now)
        # FIX-3: на коротком окне (< 60с) нормировка-к-минуте экстраполирует редкие
        # события в мнимый флуд (10 puts за 0.5с → 1200/мин). Поэтому:
        #   окно ≥ 60с → сравниваем нормированный к минуте rate с порогом;
        #   окно < 60с → сравниваем абсолютное число puts в окне с порогом.
        if self.window_seconds >= 60.0:
            growth_metric = puts_in_window / (self.window_seconds / 60.0)
        else:
            growth_metric = puts_in_window
        if growth_metric > self.growth_threshold:
            self.on_event(CacheEvent(
                level=EventLevel.YELLOW, type=CacheEventType.GROWTH_ANOMALY,
                layer=CacheLevel.L3_LLM,
                message=f"Cache growth anomaly: {growth_metric:.0f} "
                        f"(threshold={self.growth_threshold}, window={self.window_seconds}s, "
                        f"puts={puts_in_window})",
                payload={"growth_metric": round(growth_metric, 1),
                         "threshold": self.growth_threshold,
                         "window_seconds": self.window_seconds,
                         "total_puts": puts_in_window}))
        hits = self._hit_timestamps.count(now)
        misses = self._miss_timestamps.count(now)
        total = hits + misses
        if total >= 10:
            miss_rate = misses / total
            if miss_rate > self.miss_rate_threshold:
                self.on_event(CacheEvent(
                    level=EventLevel.YELLOW, type=CacheEventType.MISS_RATE_HIGH,
                    layer=CacheLevel.L3_LLM,
                    message=f"High miss rate: {miss_rate:.1%} (threshold="
                            f"{self.miss_rate_threshold:.0%}, total={total})",
                    payload={"miss_rate": round(miss_rate, 3), "threshold": self.miss_rate_threshold,
                             "hits": hits, "misses": misses, "total": total}))

    def stats_dict(self) -> dict:
        now = time.time()
        hits = self._hit_timestamps.count(now)
        misses = self._miss_timestamps.count(now)
        total = hits + misses
        return {"window_seconds": self.window_seconds, "hits_in_window": hits,
                "misses_in_window": misses,
                "miss_rate": round(misses / total, 3) if total else 0.0,
                "puts_in_window": self._put_timestamps.count(now)}


class CacheLayer:
    """
    Фасад трёхслойного кэша: связывает L1→L2→L3, lazy-load encoder,
    каскадную инвалидацию L2→L3, verdict-фильтр, anomaly detection.
    """

    DEFAULT_MODEL = "intfloat/multilingual-e5-base"

    def __init__(self, cache_dir: Path = Path("data/cache"), *,
                 model_name: str = DEFAULT_MODEL,
                 l1_max_entries: int = 10_000, l2_max_entries: int = 5_000,
                 l3_max_entries: int = 2_000, l2_similarity_threshold: float = 0.92,
                 l2_default_ttl: float = 86400.0 * 7, l3_default_ttl: float = 86400.0,
                 prompt_version: str = "v1", anomaly_growth_threshold: int = 60,
                 anomaly_miss_rate_threshold: float = 0.90,
                 anomaly_window_seconds: float = 300.0,
                 on_event: Optional[Callable[[CacheEvent], None]] = None):
        self.cache_dir = cache_dir
        init_cache_dirs(cache_dir)
        self._model_name = model_name
        self._encoder: Optional[SentenceTransformer] = None
        self.anomaly = AnomalyDetector(growth_threshold_per_min=anomaly_growth_threshold,
                                       miss_rate_threshold=anomaly_miss_rate_threshold,
                                       window_seconds=anomaly_window_seconds, on_event=on_event)

        def _wrapped_on_event(event: CacheEvent) -> None:
            if event.type == CacheEventType.HIT:
                self.anomaly.record_hit()
            elif event.type == CacheEventType.MISS:
                self.anomaly.record_miss()
            elif event.type == CacheEventType.PUT:
                self.anomaly.record_put()
            if on_event is not None:
                on_event(event)

        self._on_event = _wrapped_on_event
        self._l1_max = l1_max_entries
        self._l2_max = l2_max_entries
        self._l3_max = l3_max_entries
        self._l2_similarity = l2_similarity_threshold
        self._l2_ttl = l2_default_ttl
        self._l3_ttl = l3_default_ttl
        self._prompt_version = prompt_version

        self._l1: Optional[QueryEmbeddingCache] = None
        self._l2: Optional[RAGResultsCache] = None
        self._l3: Optional[LLMResponseCache] = None
        self._ready = False

    @property
    def l1_max_entries(self) -> int:
        return self._l1_max

    @property
    def is_ready(self) -> bool:
        return self._ready

    def warmup(self) -> None:
        if self._ready:
            return
        self._encoder = SentenceTransformer(self._model_name)
        self._l1 = QueryEmbeddingCache(
            encoder=self._encoder, cache_dir=self.cache_dir,
            max_entries=self._l1_max, on_event=self._on_event)
        self._l2 = RAGResultsCache(
            l1_cache=self._l1, cache_dir=self.cache_dir,
            max_entries=self._l2_max, similarity_threshold=self._l2_similarity,
            default_ttl=self._l2_ttl, on_event=self._on_event)
        self._l3 = LLMResponseCache(
            cache_dir=self.cache_dir, max_entries=self._l3_max,
            default_ttl=self._l3_ttl, prompt_version=self._prompt_version,
            on_event=self._on_event)
        self._ready = True
        logger.info("CacheLayer warmup complete")

    def _ensure_ready(self) -> None:
        if not self._ready:
            self.warmup()

    async def encode(self, query: str) -> np.ndarray:
        self._ensure_ready()
        assert self._l1 is not None
        return await self._l1.encode(query)

    async def get_l2(self, query: str) -> Optional[L2Entry]:
        self._ensure_ready()
        assert self._l2 is not None
        return await self._l2.get(query)

    def get_l3(self, query: str, context_hash: str, model: str) -> Optional[L3Entry]:
        self._ensure_ready()
        assert self._l3 is not None
        return self._l3.get(query, context_hash, model)

    async def put_l2(self, query: str, chunks: List[Dict], source_notes: List[str],
                     verdict: SecurityVerdict, ttl: Optional[float] = None) -> Optional[str]:
        self._ensure_ready()
        assert self._l2 is not None
        return await self._l2.put(query, chunks, source_notes, verdict, ttl)

    def put_l3(self, query: str, response: str, context_hash: str, model: str,
               source_notes: List[str], verdict: SecurityVerdict,
               ttl: Optional[float] = None) -> Optional[str]:
        self._ensure_ready()
        assert self._l3 is not None
        return self._l3.put(query, response, context_hash, model, source_notes, verdict, ttl)

    def invalidate_by_note(self, note_path: str) -> dict:
        self._ensure_ready()
        assert self._l2 is not None
        assert self._l3 is not None
        l2_removed = self._l2.invalidate_by_note(note_path)
        l3_removed = self._l3.invalidate_by_note(note_path)
        return {"l2_removed": l2_removed, "l3_removed": l3_removed}

    def stats(self) -> dict:
        self._ensure_ready()
        assert self._l1 is not None
        assert self._l2 is not None
        assert self._l3 is not None
        return {
            "l1": self._l1.stats().model_dump(),
            "l2": self._l2.stats().model_dump(),
            "l3": self._l3.stats().model_dump(),
            "anomaly": self.anomaly.stats_dict(),
        }

    def close(self) -> None:
        if self._l1 is not None:
            self._l1.close()
        if self._l2 is not None:
            self._l2.close()
        if self._l3 is not None:
            self._l3.close()
        self._ready = False
        logger.info("CacheLayer closed")