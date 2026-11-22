import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from atlaspm.cli import main


class CliTest(unittest.TestCase):
    def test_analyze_json(self) -> None:
        data = {"accounts": [{
            "name": "cash", "account_type": "cash",
            "cash": 100, "debt": 0, "positions": [],
        }]}
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "portfolio.json"
            path.write_text(json.dumps(data), encoding="utf-8")
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                status = main(["analyze", "--file", str(path)])
        self.assertEqual(status, 0)
        self.assertEqual(json.loads(output.getvalue())["summary"]["total_equity"], 100)

    def test_allocate(self) -> None:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            status = main(["allocate", "--vol", "QQQ=0.2", "NVDA=0.4"])
        self.assertEqual(status, 0)
        self.assertAlmostEqual(sum(json.loads(output.getvalue())["weights"].values()), 1)

    def test_bad_input_fails_cleanly(self) -> None:
        errors = io.StringIO()
        with contextlib.redirect_stderr(errors):
            status = main(["allocate", "--vol", "bad-value"])
        self.assertEqual(status, 2)
        self.assertIn("AtlasPM error:", errors.getvalue())


if __name__ == "__main__":
    unittest.main()
