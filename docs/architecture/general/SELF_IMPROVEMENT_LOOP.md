
🏰 Полная инструкция по установке системы «Крепость»

Полная инфраструктура: локальный RAG + Multi-Agent Council + Smart Fallback + Self-Improvement + Monitoring + Telegram + Obsidian Sync

📋 Содержание
Требования
Структура проекта
Установка зависимостей
Конфигурация
Запуск
Тестирование
Интеграция с Obsidian
Мониторинг и Telegram
Самообучение и улучшение
Troubleshooting

🖥 Требования

Железо (минимум)
| Компонент | Минимум | Рекомендуется |
|-----------|---------|---------------|
| RAM | 16 GB | 32+ GB |
| GPU VRAM | 8 GB (для 8B моделей) | 24+ GB (для 70B) |
| Диск | 50 GB SSD | 200+ GB NVMe |
| CPU | 8 ядер | 16+ ядер |

Софт
| Инструмент | Версия | Установка |
|------------|--------|-----------|
| Python | 3.10+ | apt install python3.11 python3.11-venv |
| Ollama | Latest | curl -fsSL  \| sh |
| Docker | 24+ | apt install docker.io docker-compose |
| Redis | 7+ | apt install redis-server |
| Git | 2.30+ | apt install git |

📁 Структура проекта

⚙️ Установка зависимостей

Клонирование и окружение

requirements.txt

Установка системных зависимостей (Ubuntu/Debian)

🔧 Конфигурация

Создайте .env (НЕ КОММИТЬТЕ В GIT!)

.env.example

config.yaml (основная конфигурация)

🚀 Запуск

Подготовка инфраструктуры

docker-compose.yml

Загрузка моделей в Ollama

scripts/setupmodels.sh

Индексация Obsidian Vault

Запуск приложения

Systemd сервис (для production)

🧪 Тестирование

Health Checks

Тестовый запрос

Тест Multi-Agent

Тест Fallback

Запуск тестов

📚 Интеграция с Obsidian

Настройка Vault

Настройка Git Sync (опционально)

Настройка в Obsidian

Авто-экспорт ответов

Ответы автоматически сохраняются в папку RAGОтветы/ в формате:

📊 Мониторинг и Telegram

Настройка Telegram бота

Настройка алертов

Prometheus + Grafana

🧠 Самообучение и улучшение

Как это работает

Команды управления

Ручной триггер улучшения

🔧 Troubleshooting

Частые проблемы

| Проблема | Решение |
|----------|---------|
| Ollama не запускается | systemctl status ollama → journalctl -u ollama -f |
| CUDA OOM | Уменьшите contextwindow в config.yaml, используйте 4-bit квантизацию |
| Chroma не запускается | Проверьте порт 8001: lsof -i :8001 |
| Redis connection refused | systemctl start redis |
| Telegram бот не отвечает | Проверьте токен и chatid в .env |
| Obsidian не синхронизируется | Проверьте путь к vault в config.yaml |
| Out of Memory | Добавьте swap: sudo fallocate -l 16G /swapfile &amp;&amp; chmod 600 /swapfile &amp;&amp; mkswap /swapfile &amp;&amp; swapon /swapfile |

Логи

Бэкап

scripts/backupvault.sh

📈 Масштабирование

Горизонтальное масштабирование

Вертикальное масштабирование

| Нагрузка | Рекомендации |
|----------|--------------|
| &lt; 100 req/min | 1 инстанс, 8GB RAM |
| 100-500 req/min | 2-3 инстанса, 16GB RAM |
| 500+ req/min | 5+ инстансов, GPU кластер, балансировщик |

🔐 Безопасность

Чек-лист перед продакшеном

[ ] Все секреты в .env (не в коде!)
[ ] DEBUG=false в config.yaml
[ ] Сгенерирован сильный SECRETKEY
[ ] Настроен ratelimit в config.yaml
[ ] Настроен HTTPS (nginx + Let&#x27;s Encrypt)
[ ] Настроен firewall (только нужные порты)
[ ] Регулярные бэкапы настроены
[ ] Мониторинг и алерты работают
[ ] Документирован план аварийного восстановления

📞 Поддержка

| Канал | Описание |
|-------|----------|
| GitHub Issues | Баги и feature requests |
| Telegram | Оперативные вопросы |
| Wiki | Документация и гайды |
| Discord | Комьюнити и обсуждения |

📄 Лицензия

MIT License — используйте, модифицируйте, распространяйте.

💡 Совет: Начните с минимальной конфигурации (только локальный RAG + Ollama), затем постепенно включайте модули: Fallback → Multi-Agent → Self-Improvement → Monitoring.

Удачного развёртывания! 🏰🚀