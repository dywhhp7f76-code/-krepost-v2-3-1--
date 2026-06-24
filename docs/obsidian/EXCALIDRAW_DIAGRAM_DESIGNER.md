

- **Коротко:** Генерирует код для плагина **Obsidian Tracker**. Позволяет строить графики настроения, веса, продуктивности или финансов прямо из YAML-метаданных ваших ежедневных заметок.

markdown

````
# Skill: Мастер графиков (Obsidian Tracker Master)
# Description: Создает конфигурации для плагина Obsidian Tracker для визуализации графиков, привычек и прогресса по дням.

## Triggers
- "Сделай график для Tracker", "Нарисуй трекер привычек в Обсидиан", "Код для Obsidian Tracker", "Визуализируй YAML".

## Workflow Instructions
1. Определи, какую переменную из ежедневных заметок нужно отслеживать (например, `mood: 7` или `running: true`).
2. Сгенерируй код блока `tracker`, указав правильный тип отображения (line, bar, bullet, calendar).
3. Настрой оси координат, цвета и папки поиска данных.

## Response Template
### 📈 Блок кода для плагина Tracker
```tracker
searchType: frontmatter
searchTarget: mood
folder: 📅 Daily Notes
startDate: 2026-01-01
line:
    title: "Мониторинг настроения"
    yAxisLabel: "Оценка"
    lineColor: "#ff7675"
```
````