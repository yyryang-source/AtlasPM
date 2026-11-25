"""Preserve zero-based average tie ranks across training metrics."""
import random
import unittest

from atlaspm.research.training_engine import _ranks


def reference(values):
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i + 1
        while j < len(order) and values[order[j]] == values[order[i]]:
            j += 1
        for k in order[i:j]:
            ranks[k] = (i + j - 1) / 2
        i = j
    return ranks


class TrainingRankEfficiencyTests(unittest.TestCase):
    def test_exact_average_ties(self):
        rng = random.Random(91)
        for values in ([], [1], [4] * 10000, [3, 1, 2, 3, 1],
                       list(range(500)), list(range(500, 0, -1)),
                       [rng.randrange(40) for _ in range(10000)]):
            with self.subTest(size=len(values)):
                self.assertEqual(_ranks(values), reference(values))

    def test_nonfinite_ordering_unchanged(self):
        values = [float("nan"), 1.0, float("inf"), -float("inf"), 1.0]
        self.assertEqual(_ranks(values), reference(values))


if __name__ == "__main__":
    unittest.main()
