# per_buffer.py
# Prioritized Experience Replay для дообучения safety-классификатора Крепости.
# Sum-tree O(log n) сэмплинг. Приоритет = (|loss| + eps)^alpha.

import numpy as np


class SumTree:
    """Бинарное дерево сумм: лист = приоритет примера, узел = сумма поддерева.
    Сэмплинг и обновление за O(log n)."""

    def __init__(self, capacity: int):
        self.capacity = capacity
        self.tree = np.zeros(2 * capacity - 1, dtype=np.float64)
        self.data = np.empty(capacity, dtype=object)
        self.write = 0
        self.n_entries = 0

    def _propagate(self, idx: int, change: float):
        parent = (idx - 1) // 2
        self.tree[parent] += change
        if parent != 0:
            self._propagate(parent, change)

    def _retrieve(self, idx: int, s: float) -> int:
        left = 2 * idx + 1
        right = left + 1
        if left >= len(self.tree):       # дошли до листа
            return idx
        if s <= self.tree[left]:
            return self._retrieve(left, s)
        return self._retrieve(right, s - self.tree[left])

    def total(self) -> float:
        return self.tree[0]

    def add(self, priority: float, data):
        idx = self.write + self.capacity - 1
        self.data[self.write] = data
        self.update(idx, priority)
        self.write = (self.write + 1) % self.capacity      # кольцевая перезапись
        self.n_entries = min(self.n_entries + 1, self.capacity)

    def update(self, idx: int, priority: float):
        change = priority - self.tree[idx]
        self.tree[idx] = priority
        self._propagate(idx, change)

    def get(self, s: float):
        idx = self._retrieve(0, s)
        data_idx = idx - self.capacity + 1
        return idx, self.tree[idx], self.data[data_idx]


class PrioritizedReplayBuffer:
    def __init__(
        self,
        capacity: int = 50_000,
        alpha: float = 0.6,        # 0 = равномерно, 1 = чисто по приоритету
        beta_start: float = 0.4,   # стартовая коррекция IS-смещения
        beta_frames: int = 100_000,# за сколько шагов beta дойдёт до 1.0
        eps: float = 1e-5,         # чтобы p>0 даже при нулевом loss
    ):
        self.tree = SumTree(capacity)
        self.alpha = alpha
        self.beta_start = beta_start
        self.beta_frames = beta_frames
        self.eps = eps
        self.frame = 0
        self.max_priority = 1.0    # новым примерам — максимальный приоритет

    def _beta(self) -> float:
        # линейный отжиг beta: 0.4 -> 1.0
        return min(1.0, self.beta_start + self.frame * (1.0 - self.beta_start) / self.beta_frames)

    def add(self, sample):
        # новый пример входит с max_priority => гарантированно будет показан хотя бы раз
        p = self.max_priority ** self.alpha
        self.tree.add(p, sample)

    def sample(self, batch_size: int):
        batch, idxs, priorities = [], [], []
        segment = self.tree.total() / batch_size
        self.frame += 1
        beta = self._beta()

        for i in range(batch_size):
            s = np.random.uniform(segment * i, segment * (i + 1))  # стратификация
            idx, p, data = self.tree.get(s)
            batch.append(data)
            idxs.append(idx)
            priorities.append(p)

        probs = np.array(priorities) / self.tree.total()
        weights = (self.tree.n_entries * probs) ** (-beta)
        weights /= weights.max()                       # нормировка на max => стабильность
        return batch, idxs, np.array(weights, dtype=np.float32)

    def update_priorities(self, idxs, losses):
        # вызывать ПОСЛЕ forward: приоритет = (|loss| + eps)^alpha
        for idx, loss in zip(idxs, losses):
            p = (abs(float(loss)) + self.eps) ** self.alpha
            self.tree.update(idx, p)
            self.max_priority = max(self.max_priority, abs(float(loss)) + self.eps)
