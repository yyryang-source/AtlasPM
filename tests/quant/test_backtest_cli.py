"""CLI integration smoke test for the offline walk-forward scenario."""

import contextlib
import csv
import io
import json
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from atlaspm.cli import main


class BacktestCliTest(unittest.TestCase):
    def test_backtest_writes_json_and_prints_compact_summary(self):
        with tempfile.TemporaryDirectory() as folder:
            csv_path = Path(folder) / "prices.csv"
            result_path = Path(folder) / "walkforward.json"
            with csv_path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.writer(handle)
                writer.writerow(["date", "symbol", "adjusted_close"])
                start = date(2023, 1, 1)
                for i in range(100):
                    day = (start + timedelta(days=i)).isoformat()
                    writer.writerow([day, "A", 100 + i * .18 + (i % 3) * .2])
                    writer.writerow([day, "B", 90 + i * .13 + (i % 7) * .3])
            printed = io.StringIO()
            with contextlib.redirect_stdout(printed):
                code = main([
                    "backtest", "--file", str(csv_path),
                    "--output", str(result_path),
                    "--lookback-days", "40",
                    "--rebalance-days", "20",
                    "--fee-bps", "5",
                ])
            self.assertEqual(code, 0)
            summary = json.loads(printed.getvalue())
            self.assertEqual(summary["written"], str(result_path))
            self.assertNotIn("equity_curve", summary)
            saved = json.loads(result_path.read_text(encoding="utf-8"))
            self.assertEqual(
                saved["data"]["backtest_daily_observations"], 58
            )
            self.assertEqual(
                len(saved["equity_curve"]), 58
            )
            self.assertGreater(saved["strategy"]["rebalance_count"], 0)


if __name__ == "__main__":
    unittest.main()
