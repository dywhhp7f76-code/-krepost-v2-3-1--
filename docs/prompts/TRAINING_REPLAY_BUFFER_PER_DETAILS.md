Как правильно обновлять приоритеты в Prioritized Experience Replay

Обновление приоритетов — это один из самых важных моментов в PER. От того, как и когда вы обновляете приоритеты, сильно зависит эффективность всего метода.

1. Основная формула обновления приоритета

Самая распространённая и эффективная формула:

$$ p_i = | \delta_i | + \epsilon $$

Где:

- ( p_i ) — новый приоритет примера ( i )
- ( \delta_i ) — ошибка (обычно loss или TD-error)
- ( \epsilon ) — маленькая константа (обычно 1e-6 или 1e-5), чтобы приоритет никогда не был равен нулю

2. Когда обновлять приоритеты

|   |   |   |   |
|---|---|---|---|
|Когда обновлять|Плюсы|Минусы|Рекомендация|
|После каждого batch|Самый точный и быстрый отклик|Дорого по вычислениям|Не рекомендуется|
|После каждой эпохи|Хороший баланс|Средняя точность|Рекомендуется|
|После каждых N шагов|Компромисс|Нужно подбирать N|Хорошо|
|Только на валидации|Дёшево|Менее точный|Для больших буферов|

Лучшая практика для QLoRA (7B–13B):

- Обновлять приоритеты после каждой эпохи или каждые 200–500 шагов.

3. Практическая реализация

Вот улучшенная версия PrioritizedReplayBuffer с правильным обновлением приоритетов:

import numpy as np

from collections import deque

  

class PrioritizedReplayBuffer:

    def __init__(self, capacity=5000, alpha=0.6, beta=0.4, epsilon=1e-6):

        self.capacity = capacity

        self.alpha = alpha

        self.beta = beta

        self.epsilon = epsilon

        self.buffer = deque(maxlen=capacity)

        self.priorities = deque(maxlen=capacity)

        self.loss_history = deque(maxlen=capacity)  # для отслеживания forgetting

  

    def add(self, experience, initial_priority=None):

        if initial_priority is None:

            initial_priority = max(self.priorities, default=1.0)

        self.buffer.append(experience)

        self.priorities.append(initial_priority)

        self.loss_history.append(0.0)

  

    def sample(self, batch_size):

        if len(self.buffer) == 0:

            return [], [], []

  

        priorities = np.array(self.priorities)

        probs = priorities ** self.alpha

        probs /= probs.sum()

  

        indices = np.random.choice(len(self.buffer), batch_size, p=probs)

        # Importance Sampling weights

        weights = (len(self.buffer) * probs[indices]) ** (-self.beta)

        weights /= weights.max()

  

        samples = [self.buffer[i] for i in indices]

        return samples, list(indices), weights

  

    def update_priorities(self, indices, losses):

        &quot;&quot;&quot;Обновляем приоритеты после обучения&quot;&quot;&quot;

        for idx, loss in zip(indices, losses):

            # Основная формула

            new_priority = abs(loss) + self.epsilon

            # Опционально: добавляем forgetting signal

            # new_priority += 0.1 * abs(loss - self.loss_history[idx])

            self.priorities[idx] = new_priority

            self.loss_history[idx] = loss

  

    def get_all_priorities(self):

        return list(self.priorities)

4. Лучшие практики обновления

|   |   |   |
|---|---|---|
|Практика|Рекомендация|Почему|
|Добавлять epsilon|Всегда (1e-6)|Предотвращает нулевые приоритеты|
|Clipping приоритетов|Верхний предел (например, 10.0)|Предотвращает доминирование одного примера|
|Нормализация|После обновления нормализовать приоритеты|Стабилизирует обучение|
|Скользящее среднее|Использовать EMA для приоритетов|Сглаживает шум|
|Отдельный буфер для новых данных|Новые примеры получают высокий приоритет|Ускоряет обучение на свежих данных|

5. Полный цикл обучения с PER

# Псевдокод цикла

  

for epoch in range(num_epochs):

    for batch in dataloader:

        # 1. Выбираем примеры с приоритетами

        samples, indices, weights = replay_buffer.sample(batch_size)

        # 2. Обучаем модель

        loss = train_step(samples, weights)

        # 3. Обновляем приоритеты

        replay_buffer.update_priorities(indices, [loss] * len(indices))

6. Дополнительные улучшения

7. Forgetting-aware Priority  
    new_priority = abs(current_loss - previous_loss) + epsilon
8.   
    
9. Importance Sampling Correction

- Всегда используйте weights при вычислении loss.

5. Annealing beta

- Постепенно увеличивайте beta от 0.4 до 1.0 в течение обучения.

Рекомендация для 7B–13B моделей

- Используйте alpha = 0.6
- beta = 0.4 → постепенно увеличивайте до 0.8
- Обновляйте приоритеты каждые 300–500 шагов
- Добавляйте небольшой epsilon = 1e-6
- Ограничивайте максимальный приоритет (max_priority = 5.0)

  

Хочешь, я дам:

- Полный готовый класс PrioritizedReplayBuffer с поддержкой стратификации + PER
- Как интегрировать это в Unsloth
- Как комбинировать PER + Stratified Sampling

Что именно нужно дальше?