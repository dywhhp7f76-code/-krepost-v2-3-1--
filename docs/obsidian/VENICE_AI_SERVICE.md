- **Коротко:** Генерирует сырой код в формате JSON для файлов бесконечного холста `.canvas`, позволяя Grok буквально рисовать связи между вашими карточками.

markdown

````
# Skill: Проектировщик Obsidian Canvas (Obsidian Canvas Architect)
# Description: Создает JSON-структуру для файлов Obsidian Canvas (.canvas), визуализируя связи между заметками на интерактивной доске.

## Triggers
- &quot;Сделай холст для Obsidian&quot;, &quot;Сделай Canvas&quot;, &quot;Сгенерируй файл .canvas&quot;, &quot;Визуальная схема для Обсидиан&quot;.

## Workflow Instructions
1. Изучи логическую схему или майндмэп, предложенный пользователем.
2. Рассчитай координаты (x, y, width, height) для текстовых нод (узлов) и карточек файлов.
3. Сгенерируй валидный JSON-код холста, включая массив элементов `nodes` и связей `edges`.

## Response Template
### 🗺 Код для файла `project_map.canvas`
*(Создайте пустой файл `.canvas` в Obsidian, откройте его как текстовый файл и вставьте код ниже)*
```json
{
  &quot;nodes&quot;: [
    {&quot;id&quot;: &quot;n1&quot;, &quot;type&quot;: &quot;text&quot;, &quot;text&quot;: &quot;# Главная цель&quot;, &quot;x&quot;: 0, &quot;y&quot;: 0, &quot;width&quot;: 250, &quot;height&quot;: 100}
  ],
  &quot;edges&quot;: []
}
```
````