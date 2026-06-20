--- 
tags: [крепость, роадмэп, архитектура, мониторинг]
date: 2026-06-19
version: 2.1
status: active
replaces: roadmap-v2.0
hardware:
  mac_studio: M4 Max 64GB 1TB
  macbook_air: M5 32GB 1TB
  fast_ssd: WD SN850X 2TB TB5 (80Gbps)
  archive_hdd: 6TB
  ipad: Air M4 256GB
  iphone: Air 256GB
models:
  main: Qwen3.6-27B (Q4_K_M)
  guard: Qwen3Guard-Gen-4B
  attacker: Mistral-7B-instruct
  embedder: BGE-M3
  reader: OCC-RAG-1.7B (planned)
  image_gen: FLUX.1 [schnell] + [dev]
next_review: 2026-07-01
---

# 🗺️ КРЕПОСТЬ — РОАДМЭП v2.0

## Архитектура (зафиксирована)
- ОСНОВНОЙ ИИ (Mac Studio 64ГБ) — носитель конституции, говорит с тобой, берёт чистое из RAG.
- ЗАЩИТА — отдельная маленькая модель (Studio): Охранник + Карантин + проверка.
- АТАКУЮЩИЙ (MacBook Air 32ГБ, грязная зона) — спарринг для adversarial-тренировки защиты.
- ОБЛАКО — недоверенный помощник, ответ через Карантин (quarantine:true).
- СОВЕТ — по кнопке, не на каждый запрос. «Учитель» удалён.
- Threat model A (наружу не смотрит).

---

## ФАЗА 0 — СЕЙЧАС (без Mac, можно делать)

### 0.1 Выбор основной модели ⬅ старт
Кандидаты (НЕ финал): Qwen3.6-27B dense (Apache 2.0) основной / Qwen3-Guard 4B защита.
Критерии: открытая лицензия (дообучение!), слабая лишняя фильтрация, русский, ≤64ГБ.
Требует свежего поиска. Разблокирует всю математику обучения.

### 0.2 Системный промпт основного ИИ
Из «Конституции характера». Минимализм — регулируемым параметром, не константой.
Заменить в конституции «зеркала» → «буфер+карантин».

### 0.3 Защитный промпт
По НАМЕРЕНИЮ, не теме: блокировать инструкции/призыв к действию (детское — строго;
инструкции вреда), но НЕ резать новости/аналитику на острые темы.

### 0.4 security.py v1.1 (исправление багов)
9 багов зафиксированы (см. раздел «security.py — баги к фиксу**).
Порядок: 4 (дыра) → 1,3 → 2,6 → 7,8,9 → 5 (на Mac).

### 0.5 Структура датасета для обучения защиты
Формат human_verdict → изолированная база. JSONL-схема, разметка GREEN/YELLOW/RED.
Привязано к коду, не к модели — можно сейчас.

### 0.6 OCC-RAG Reader integration
Patch v1.1 готов. Компактный reader (0.6B/1.7B) для RAG.
Экономит 17GB RAM vs Qwen3.6-27B. Context-faithful by design.
Требует: retriever_protocol.py (адаптеры ChromaDB/FAISS).

### 0.7 SMART_CACHE v2.1
Трёхслойный кэш (L1 exact/L2 semantic/L3 full).
Уже реализован в src/krepost/cache/.
Требует: интеграция с Security Layer (кэшировать только GREEN).

### 0.8 Source Citations
Кликабельные ссылки на источники в ответах.
Критично для доверия к RAG.
Интеграция с Obsidian (obsidian://open?vault=...).

---

## ФАЗА 1 — ПРИХОД Mac (сборка)

### 1.1 main.py — соединить 9 модулей
Пайплайн: User → Охранник → Карантин → [Router+RAG+LLM] → Пост-процессор → User.
Общий EmbeddingProvider, подписка on_event на monitor, запуск watchers.

### 1.2 Новый app.py
Старые устарели. Под новые модули.

### 1.3 Llama Guard / Qwen-Guard слой 2
Заменить MockSafetyClassifier на реальный. Починить формат запроса (баг 5).

### 1.4 Инфраструктура
config.py, models.py, __init__.py, .env.example, requirements.txt, backup.sh.
backup.sh: рабочее (SN580) и бэкап (HDD) — РАЗНЫЕ диски.

### 1.5 Железо-доделки
SATA-бокс/док для 3.5" HDD (питание 12В) — проверить, тянет ли имеющаяся док-станция.

### 1.6 KVEraser integration
Обучаемое удаление harmful spans из KV-cache.
Latency +24% vs +1760% (full recompute).
Критично для гигиены памяти RAG-агента.

### 1.7 Modern Hopfield Memory
Episodic memory через content-addressable storage.
Lifelong learning без дообучения модели.
Интеграция с label_conflicts resolution.

### 1.8 Мониторинг и алерты
Telegram Bot + Pushover для iPhone/iPad.
Критические алерты: prompt injection, Guard crash.
Еженедельные отчёты: Email.

---

## ФАЗА 2 — ПОСЛЕ СБОРКИ

### 2.1 Атакующий + attack_vault (на Air)
Переиспользовать document_ingestion (2й экземпляр) + adversarial_pipeline читает из vault.
Отдельный Chroma-индекс. Старые jailbreak-промпты из архива → сюда как боеприпасы.
Защита НЕ видит vault атак заранее.

### 2.2 Red Team Loop (обучение защиты)
Атака → спарринг → провалы → изолированная база → дообучение (твой gate) →
safety-обвязка (прогон adversarial → откат если ASR вырос) → повтор.

### 2.3 self_improvement / PER
Самый опасный модуль. Последним. Ручной gate. Барьер обучения от заражения —
закрывается ЗДЕСЬ (до этого обучение физически не запускается).

### 2.4 Tool Chains
С санитизацией web-результатов (injection-канал).

---

## БЭКЛОГ (когда ядро работает)
- Knowledge Gap Detector (почти готов: rag_score<порога=дыра) — высокий приоритет.
- Silent Watcher (из monitoring+triggers).
- Local Data Miner, Personal Task Extractor, GraphRAG, Prompt Refiner.
- Cloud Fallback (опц., через карантин).
- Совет (5 агентов, по кнопке).
- Служебные промпты → prompts/tools/ (для агентов-инструментов, НЕ основного ИИ).
- KVEraser + Source Citations: автоинвалидация при удалении источников
- Retriever Protocol: формализация адаптеров (ChromaDB/FAISS)
- LedgerAgent: structured state для debugging
- DGX Spark (128GB unified memory): цель на 2027, $3000
- NVMe RAID 0 (2x4TB): цель на конец 2026, ~$600

---

## МЕТРИКИ УСПЕХА

### Безопасность
- Held-out Poison Recall: >80%
- False Positive Rate: <5%
- F-beta (β=2): >0.85

### Производительность
- Latency P95 (cache hit): <50ms
- Latency P95 (full pipeline): <5000ms
- Cache Hit Rate: >70%

### Надёжность
- Uptime: >99%
- Graceful shutdown: 100% случаев
- Unit Test Coverage: >90%

---

## ПОРЯДОК БЛИЖАЙШИХ ШАГОВ
1. Выбор модели (0.1) — разблокирует остальное.
2. security.py v1.1 (0.4) — починить дыру (баг 4).
3. Промпт основного ИИ (0.2) + защитный (0.3).
4. Структура датасета (0.5).
→ дальше ЖДЁМ Mac → Фаза 1 (сборка).