"""Тесты PrioritizedReplayBuffer — SumTree, sampling, priorities."""
import numpy as np

from krepost.training.per_buffer import SumTree, PrioritizedReplayBuffer


class TestSumTree:
    def test_add_and_total(self):
        tree = SumTree(4)
        tree.add(1.0, "a")
        tree.add(2.0, "b")
        tree.add(3.0, "c")
        assert abs(tree.total() - 6.0) < 1e-9

    def test_get_retrieves_correct_leaf(self):
        tree = SumTree(4)
        tree.add(1.0, "a")
        tree.add(2.0, "b")
        tree.add(3.0, "c")
        _, priority, data = tree.get(0.5)
        assert data == "a"
        assert abs(priority - 1.0) < 1e-9

    def test_update_changes_priority(self):
        tree = SumTree(4)
        tree.add(1.0, "a")
        tree.add(2.0, "b")
        old_total = tree.total()
        idx, _, _ = tree.get(0.5)
        tree.update(idx, 5.0)
        assert abs(tree.total() - (old_total - 1.0 + 5.0)) < 1e-9

    def test_circular_overwrite(self):
        tree = SumTree(2)
        tree.add(1.0, "a")
        tree.add(2.0, "b")
        tree.add(3.0, "c")
        assert tree.n_entries == 2
        assert abs(tree.total() - 5.0) < 1e-9


class TestPrioritizedReplayBuffer:
    def test_add_and_sample(self):
        buf = PrioritizedReplayBuffer(capacity=100)
        for i in range(20):
            buf.add(f"sample_{i}")
        batch, idxs, weights = buf.sample(5)
        assert len(batch) == 5
        assert len(idxs) == 5
        assert len(weights) == 5
        assert all(w > 0 for w in weights)

    def test_weights_normalized(self):
        buf = PrioritizedReplayBuffer(capacity=100)
        for i in range(50):
            buf.add(f"s{i}")
        _, _, weights = buf.sample(10)
        assert abs(weights.max() - 1.0) < 1e-6

    def test_update_priorities(self):
        buf = PrioritizedReplayBuffer(capacity=100)
        for i in range(10):
            buf.add(f"s{i}")
        _, idxs, _ = buf.sample(5)
        losses = [0.1, 0.5, 0.9, 0.01, 0.3]
        buf.update_priorities(idxs, losses)
        assert buf.max_priority >= 0.9

    def test_beta_annealing(self):
        buf = PrioritizedReplayBuffer(capacity=100, beta_start=0.4, beta_frames=100)
        assert abs(buf._beta() - 0.4) < 1e-6
        for _ in range(10):
            buf.add("x")
        for _ in range(100):
            buf.sample(2)
        assert buf._beta() > 0.9

    def test_empty_produces_nan_weights(self):
        buf = PrioritizedReplayBuffer(capacity=10)
        _, _, weights = buf.sample(5)
        assert np.all(np.isnan(weights))
