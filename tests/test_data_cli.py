"""Tests for the offline historical-data CLI workflow."""

import contextlib
import csv
import io
import json
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

from atlaspm.cli import main


class DataCliTest(unittest.TestCase):
    def test_estimate_writes_optimizer_compatible_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "prices.csv"
            output = Path(temp) / "estimated.json"
            with path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.writer(handle)
                writer.writerow(["date", "symbol", "adjusted_close"])
                start = date(2026, 1, 1)
                for i in range(71):
                    day = (start + timedelta(days=i)).isoformat()
                    writer.writerow([day, "A", 100 + i + (i % 3) * 0.25])
                    writer.writerow([day, "B", 140 + i * 0.6 + (i % 4) * 0.25])
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                status = main(["estimate", "--file", str(path), "--output", str(output)])
            self.assertEqual(status, 0)
            result = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(set(result["expected_returns"]), {"A", "B"})
            self.assertEqual(result["metadata"]["return_observations"], 70)
            self.assertIn("written", json.loads(stdout.getvalue()))

    def test_download_command_uses_correct_boundaries(self) -> None:
        stdout = io.StringIO()
        expected = {"provider": "mock", "rows_per_symbol": {"A": 65}}
        with patch("atlaspm.cli.download_yahoo_adjusted_prices", return_value=expected) as mock:
            with contextlib.redirect_stdout(stdout):
                status = main([
                    "download-prices", "--symbols", "A",
                    "--start", "2026-01-01", "--end", "2026-05-01",
                ])
        self.assertEqual(status, 0)
        self.assertEqual(json.loads(stdout.getvalue())["provider"], "mock")
        self.assertEqual(mock.call_args.args[:3], (["A"], "2026-01-01", "2026-05-01"))


if __name__ == "__main__":
    unittest.main()
