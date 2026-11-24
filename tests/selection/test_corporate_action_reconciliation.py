"""Regression tests for SEC-verified split safety and source preservation."""

from __future__ import annotations

import csv
import json
import tempfile
from pathlib import Path
import unittest

from atlaspm.data.corporate_action_reconciliation import reconcile_reverse_splits

_SEC = "https://www.sec.gov/Archives/edgar/data/1638911/000121390026024093/ea0279505-8k_duke.htm"


class ReverseSplitReconciliationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.raw = self.root / "original.csv"
        self.output = self.root / "corrected.csv"
        self.manifest = self.root / "manifest.json"

    def _write(self, rows, events=None):
        with self.raw.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["date", "symbol", "adjusted_close"])
            writer.writerows(rows)
        if events is None:
            events = [{
                "symbol": "DUKR", "type": "reverse_split",
                "effective_date": "2026-03-06", "old_shares_per_new_share": 25,
                "source_url": _SEC,
            }]
        self.manifest.write_text(json.dumps({"events": events}), encoding="utf-8")

    def test_dukr_audit_and_corrected_copy_without_touching_original(self):
        self._write([
            ["2024-01-02", "DUKR", "0.10"],
            ["2026-03-05", "DUKR", "0.30"],
            ["2026-03-06", "DUKR", "7.66"],
            ["2026-03-09", "DUKR", "7.25"],
            ["2026-03-05", "AAPL", "100"],
            ["2026-03-06", "AAPL", "105"],
        ])
        before = self.raw.read_bytes()
        audit = reconcile_reverse_splits(self.raw, self.manifest)
        self.assertEqual(audit["status"], "REVIEW_REQUIRED")
        self.assertEqual(audit["events_requiring_reconciliation"], 1)
        self.assertFalse(self.output.exists())
        evt = audit["event_checks"][0]
        self.assertEqual(evt["status"], "LIKELY_MISSING_ADJUSTMENT")
        self.assertAlmostEqual(evt["raw_close_multiplier"], 7.66 / 0.30)
        self.assertAlmostEqual(evt["normalized_close_return_if_split"], 7.66 / 7.5 - 1)
        copy = reconcile_reverse_splits(
            self.raw, self.manifest, output_path=self.output, apply_confirmed=True
        )
        self.assertEqual(copy["status"], "CORRECTED_RESEARCH_COPY_NEEDS_REAUDIT")
        self.assertEqual(copy["modified_rows"], 2)
        self.assertTrue(copy["corrected_sha256"])
        self.assertEqual(self.raw.read_bytes(), before)
        with self.output.open(newline="", encoding="utf-8") as handle:
            fixed = list(csv.DictReader(handle))
        self.assertEqual(
            [(r["date"], float(r["adjusted_close"])) for r in fixed if r["symbol"] == "DUKR"],
            [("2024-01-02", 2.5), ("2026-03-05", 7.5),
             ("2026-03-06", 7.66), ("2026-03-09", 7.25)],
        )
        self.assertEqual(
            [r["adjusted_close"] for r in fixed if r["symbol"] == "AAPL"], ["100", "105"]
        )
        after_audit = reconcile_reverse_splits(self.output, self.manifest)
        self.assertEqual(after_audit["status"], "NO_CORRECTION_INDICATED")
        self.assertEqual(
            after_audit["event_checks"][0]["status"], "ALREADY_ADJUSTED_OR_NORMAL_PRICE"
        )

    def test_pre_adjusted_close_does_not_get_double_adjusted(self):
        self._write([
            ["2026-10-21", "DKI", "2.00"], ["2026-10-22", "DKI", "2.25"],
        ], events=[{
            "symbol": "DKI", "type": "reverse_split",
            "effective_date": "2026-10-22", "old_shares_per_new_share": 50,
            "source_url": _SEC,
        }])
        audit = reconcile_reverse_splits(self.raw, self.manifest)
        self.assertEqual(audit["event_checks"][0]["status"], "ALREADY_ADJUSTED_OR_NORMAL_PRICE")
        copy = reconcile_reverse_splits(self.raw, self.manifest, output_path=self.output, apply_confirmed=True)
        self.assertEqual(copy["modified_rows"], 0)
        with self.output.open(newline="", encoding="utf-8") as handle:
            fixed = list(csv.DictReader(handle))
        self.assertEqual([float(r["adjusted_close"]) for r in fixed], [2, 2.25])

    def test_ambiguous_case_stops_without_creating_output(self):
        self._write([
            ["2026-03-05", "DUKR", "0.30"],
            ["2026-03-06", "DUKR", "1.50"],
        ])
        audit = reconcile_reverse_splits(self.raw, self.manifest)
        self.assertEqual(audit["event_checks"][0]["status"], "AMBIGUOUS_NEEDS_INDEPENDENT_REVIEW")
        with self.assertRaisesRegex(ValueError, "unresolved events"):
            reconcile_reverse_splits(self.raw, self.manifest, output_path=self.output, apply_confirmed=True)
        self.assertFalse(self.output.exists())

    def test_missing_event_date_region_stops(self):
        self._write([["2026-03-05", "DUKR", "0.30"]])
        audit = reconcile_reverse_splits(self.raw, self.manifest)
        self.assertEqual(audit["event_checks"][0]["status"], "INSUFFICIENT_DATA")
        with self.assertRaisesRegex(ValueError, "unresolved events"):
            reconcile_reverse_splits(self.raw, self.manifest, output_path=self.output, apply_confirmed=True)

    def test_no_overwrites_or_unverified_splits(self):
        self._write([
            ["2026-03-05", "DUKR", "0.30"],
            ["2026-03-06", "DUKR", "7.66"],
        ])
        with self.assertRaisesRegex(ValueError, "NEW distinct file"):
            reconcile_reverse_splits(
                self.raw, self.manifest, output_path=self.raw, apply_confirmed=True
            )
        self.output.write_text("sentinel", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "NEW distinct file"):
            reconcile_reverse_splits(
                self.raw, self.manifest, output_path=self.output, apply_confirmed=True
            )
        self.assertEqual(self.output.read_text(), "sentinel")
        self._write([
            ["2026-03-05", "DUKR", "0.30"], ["2026-03-06", "DUKR", "7.66"],
        ], events=[{
            "symbol": "DUKR", "type": "reverse_split",
            "effective_date": "2026-03-06", "old_shares_per_new_share": 25,
            "source_url": "https://untrusted.example/split",
        }])
        with self.assertRaisesRegex(ValueError, "official SEC"):
            reconcile_reverse_splits(self.raw, self.manifest)

    def test_multiple_verified_splits_compound_only_for_affected_symbol(self):
        # Two events for one ticker require 10x * 5x on the earliest date;
        # rows between events receive only the later 5x adjustment.
        self._write([
            ["2024-05-30", "DUKR", "1"],
            ["2024-06-03", "DUKR", "10"],
            ["2025-05-30", "DUKR", "8"],
            ["2025-06-02", "DUKR", "40"],
            ["2024-05-30", "AAPL", "100"],
            ["2025-06-02", "AAPL", "120"],
        ], events=[
            {
                "symbol": "DUKR", "type": "reverse_split",
                "effective_date": "2024-06-01",
                "old_shares_per_new_share": 10,
                "source_url": _SEC,
            },
            {
                "symbol": "DUKR", "type": "reverse_split",
                "effective_date": "2025-06-01",
                "old_shares_per_new_share": 5,
                "source_url": _SEC,
            },
        ])
        original_bytes = self.raw.read_bytes()
        audit = reconcile_reverse_splits(self.raw, self.manifest)
        self.assertEqual(
            [check["status"] for check in audit["event_checks"]],
            ["LIKELY_MISSING_ADJUSTMENT", "LIKELY_MISSING_ADJUSTMENT"],
        )
        result = reconcile_reverse_splits(
            self.raw, self.manifest,
            output_path=self.output, apply_confirmed=True
        )
        self.assertEqual(result["modified_rows"], 3)
        self.assertEqual(self.raw.read_bytes(), original_bytes)
        with self.output.open(newline="", encoding="utf-8") as handle:
            fixed = list(csv.DictReader(handle))
        self.assertEqual(
            [float(row["adjusted_close"]) for row in fixed if row["symbol"] == "DUKR"],
            [50, 50, 40, 40],
        )
        self.assertEqual(
            [float(row["adjusted_close"]) for row in fixed if row["symbol"] == "AAPL"],
            [100, 120],
        )
        post = reconcile_reverse_splits(self.output, self.manifest)
        self.assertEqual(post["status"], "NO_CORRECTION_INDICATED")

    def test_cli_rejects_public_output_before_reading_data(self):
        from atlaspm.cli import main
        import contextlib
        import io
        import os
        cwd = Path.cwd()
        try:
            os.chdir(self.root)
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                result = main([
                    "stock-split-reconcile",
                    "--prices", "missing.csv", "--manifest", "missing.json",
                    "--apply-confirmed", "--output", "public.csv",
                ])
            self.assertEqual(result, 2)
            self.assertFalse((self.root / "public.csv").exists())
        finally:
            os.chdir(cwd)


if __name__ == "__main__":
    unittest.main()
