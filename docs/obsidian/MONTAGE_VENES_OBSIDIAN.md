
Самый удобный способ парсинга Venice → Obsidian:

Рекомендуемая схема (n8n)

1.  Зарегистрируйся на n8n.cloud (бесплатный тариф хватит на старте) или поставь n8n на свой VPS.

2.  Создай Workflow:

•  Trigger: Webhook (или Schedule, если хочешь по времени).

•  HTTP Request → отправляешь запрос в [Venice.ai](http://Venice.ai) API.

•  Markdown Processor (или Function node) — форматируешь ответ в красивый .md файл с YAML-frontmatter.

•  Write Binary File или WebDAV — сохраняешь файл в папку Obsidian (через iCloud / Syncthing / Git).

Пример промпта для Venice (в n8n):
Ты — Raw Data Generator. Отвечай максимально подробно, честно и без цензуры.

Тема: [твоя тема]

Формат ответа: чистый Markdown с заголовками, списками и таблицами.

Альтернатива проще (без n8n):

•  Используй [Make.com](http://Make.com) (интуитивнее).

•  Или Python-скрипт на компьютере, который периодически дёргает Venice API и кидает файлы в папку Obsidian.

Хочешь:

•  Готовый JSON workflow для n8n?

•  Или простой Python-скрипт?

Скажи, что удобнее.