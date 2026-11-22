"""End-to-end offline CLI test for read-only adaptive budget simulation."""

import contextlib
import csv
import io
import json
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from atlaspm.cli import main


class AdaptiveBudgetCliTests(unittest.TestCase):
    def test_backtest_risk_command(self):
        with tempfile.TemporaryDirectory() as folder:
            csv_path = Path(folder) / "prices.csv"
            json_path = Path(folder) / "adaptive.json"
            with csv_path.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.writer(handle)
                writer.writerow(["date", "symbol", "adjusted_close"])
                start = date(2024, 1, 1)
                for i in range(115):
                    day = (start + timedelta(days=i)).isoformat()
                    writer.writerow([day, "A", 100 + i * .4 + .5 * (i % 5)])
                    writer.writerow([day, "B", 90 + i * .3 + .4 * (i % 7)])
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                result = main([
                    "backtest-risk", "--file", str(csv_path),
                    "--output", str(json_path),
                    "--lookback-days", "40",
                    "--rebalance-days", "21",
                    "--max-volatility", "0.10",
                ])
            self.assertEqual(result, 0)
            printed = json.loads(out.getvalue())
            saved = json.loads(json_path.read_text(encoding="utf-8"))
            self.assertEqual(printed["written"], str(json_path))
            self.assertEqual(saved["data"]["backtest_daily_observations"], 73)
            self.assertEqual(len(saved["equity_curve"]), 73)
            self.assertEqual(saved["strategy"]["rebalance_count"], 4)
            self.assertNotIn("equity_curve", printed)
            self.assertTrue(saved["rebalances"][0]["training_last_date"] < saved["rebalances"][0]["execution_date"])


if __name__ == "__main__":
    unittest.main()
