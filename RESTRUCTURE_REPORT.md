Отчёт о реструктуризации репозитория Krepost

Ветка: restructure/v2.2
Дата: 2026-06-24
Статус: В процессе

===

ВЫПОЛНЕНО:

1. Ветка
   Создана ветка restructure/v2.2 от main

2. Удаление мусора
   TEST_CONNECTION_WORKS.md -> _trash/
   TEST_FILE.md -> _trash/

3. Исследования -> research/ (для git-crypt)
   src/krepost/MASTER_PLAN.py -> research/grok_rlhf_audit.yaml
   docs/other/TRUE_HACK_AI.md -> research/
   docs/other/WINX_AI_SETUP.md -> research/
   docs/prompts/INTERNAL_AUDIT_GROK.md -> research/
   docs/security/patterns/JAILBREAK_PATTERNS.md -> research/jailbreak_patterns/
   Создан research/.gitattributes

4. Удаление дублей
   MODEL_CHOICE_V1_2.md -> _trash/ (дубль TRAINING_SANDBOX.md)
   ULTIMATE_RAG_OBSIDIAN.md -> _trash/ (почти дубль RAG_FINAL_V2.md)

5. Извлечение кода из .md -> .py
   TRUST_BRIDGE.md -> src/krepost/integration/trust_bridge.py (3596 байт)
   ADDITIONAL_SCHEME_GUARD.md -> src/krepost/memory/episodic_memory.py (29083 байт)
   MODEL_ROUTER_V2.md -> src/krepost/router/model_router.py (28294 байт)
   DOCUMENT_INGESTION_V2_1.md -> src/krepost/ingestion/document_ingestion.py (30276 байт)
   KREPOST_FULL_SOURCE.md -> src/krepost/fallback/smart_fallback.py (9933 байт)
   SMART_CACHE.py -> src/krepost/cache/smart_cache.py (переименован)

6. Создание .gitignore
   Создан .gitignore

7. Обновление .md файлов
   TRUST_BRIDGE.md -> stub
   ADDITIONAL_SCHEME_GUARD.md -> stub
   MODEL_ROUTER_V2.md -> stub
   DOCUMENT_INGESTION_V2_1.md -> stub

===

ТРЕБУЕТСЯ ПРОВЕРКА:

Прогнать python -m py_compile для всех извлечённых файлов:
   src/krepost/integration/trust_bridge.py
   src/krepost/memory/episodic_memory.py
   src/krepost/router/model_router.py
   src/krepost/ingestion/document_ingestion.py
   src/krepost/fallback/smart_fallback.py
   src/krepost/cache/smart_cache.py

Если smart_fallback.py не компилируется - это битый OCR (D-2), нужно исправить вручную.

===

НЕЗАВЕРШЁННЫЕ ЗАДАЧИ:

1. Починка импортов
   Обновить импорты после переезда в пакеты:
   from document_ingestion import ... -> from krepost.ingestion.document_ingestion import ...
   Проверить: python -c "import krepost"

2. Переименование Krepost/ -> vault/
   Вопрос: Конфликт с runtime-путём vault/ для данных
   Рекомендация: Оставить Krepost/ как есть, runtime-данные в data/vault/

3. Создание __init__.py
   Нужно создать __init__.py в каждой папке src/krepost/*/

4. Обновление README.md
   Сейчас почти пустой

5. Очистка истории research/
   См. RESTRUCTURE_QUESTIONS.md - нужно git filter-repo + force-push
   НЕ ВЫПОЛНЯТЬ БЕЗ ПОДТВЕРЖДЕНИЯ!

===

БЛОКЕРЫ:

1. Отсутствует src/krepost/security/pipeline.py (блокер №1 из инструкции)

===

НЕ ДЕЛАТЬ:
- Не мерджить ветку сам
- Не пушить в main
- Не пушить research/ в открытый вид
- Не запускать git filter-repo без подтверждения
