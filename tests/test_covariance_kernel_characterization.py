"""Exact regression coverage for sample covariance, including fallback inputs."""
import random
import unittest
from decimal import Decimal

from atlaspm.data.historical import annualized_sample_covariance


def reference(rows, means):
    n = len(rows)
    return [[252 * sum((row[i] - means[i]) * (row[j] - means[j])
                       for row in rows) / (n - 1)
             for j in range(len(means))] for i in range(len(means))]


class CovarianceKernelTests(unittest.TestCase):
    def test_float_matrix_exact(self):
        rng = random.Random(107)
        for n, p in ((2, 4), (63, 8), (252, 12), (10, 3)):
            rows = [[rng.uniform(-0.1, 0.1) for _ in range(p)] for _ in range(n)]
            means = [sum(row[i] for row in rows) / n for i in range(p)]
            with self.subTest(n=n, p=p):
                self.assertEqual(annualized_sample_covariance(rows, means),
                                 reference(rows, means))

    def test_non_float_numeric_fallback(self):
        rows = [[Decimal(i + j) for j in range(4)] for i in range(3)]
        means = [Decimal(j + 1) for j in range(4)]
        self.assertEqual(annualized_sample_covariance(rows, means),
                         reference(rows, means))

    def test_invalid_dimensions(self):
        with self.assertRaisesRegex(ValueError, "Sample covariance requires"):
            annualized_sample_covariance([[1.0]], [1.0])
        with self.assertRaises(IndexError):
            annualized_sample_covariance([[1.0], [2.0]], [1.0] * 4)


if __name__ == "__main__":
    unittest.main()
