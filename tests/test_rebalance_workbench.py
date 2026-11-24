from __future__ import annotations

import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from pathlib import Path

from atlaspm.rebalance_workbench import Policy, RunStore, demo_inputs, latest_completed_session

try:
    import pandas_market_calendars  # noqa: F401
    import scipy  # noqa: F401
except ImportError:
    HAS_WORKBENCH = False
else:
    HAS_WORKBENCH = True


@unittest.skipUnless(HAS_WORKBENCH, "requires the workbench extra")
class RebalanceWorkbenchTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.store = RunStore(Path(directory.name) / "runs.sqlite3")
        self.day, self.scope, self.adjusted, self.raw, self.snapshot = demo_inputs()

    def run_report(self, *, adjusted=None, raw=None, account=None, policy=Policy()):
        key = self.store.run(
            self.day, self.scope, self.adjusted if adjusted is None else adjusted,
            self.raw if raw is None else raw,
            self.snapshot if account is None else json.dumps(account).encode(),
            policy, demo=True,
        )
        return self.store.get(key)

    def test_equal_and_quant_use_unadjusted_close_and_remaining_quantity(self):
        for allocation in ("equal", "quant"):
            with self.subTest(allocation=allocation):
                report = self.run_report(policy=Policy(allocation=allocation))
                self.assertEqual(report["status"], "review")
                row = next(item for item in report["positions"] if item["symbol"] == "ECHO")
                self.assertEqual(row["held"], 12)
                self.assertEqual(row["pending_buy"], 5)
                self.assertEqual(row["effective"], 17)
                self.assertEqual(row["delta"], row["target"] - 17)
                draft = next(item for item in report["draft"] if item["symbol"] == "ECHO")
                self.assertEqual(draft["shares"], abs(row["delta"]))
                self.assertEqual(draft["reference_close"], 157.53)

    def test_blocked_data_never_reaches_selection(self):
        lines = self.adjusted.splitlines(keepends=True)
        report = self.run_report(adjusted=b"".join(
            line for line in lines if not line.startswith(b"2024-12-31,ECHO,")
        ))
        self.assertEqual(report["status"], "blocked")
        self.assertEqual(report["checks"][-1]["detail"]["缺决策日复权收盘价"], ["ECHO"])
        self.assertNotIn("candidates", report)
        self.assertEqual(report["draft"], [])
        missing_raw = self.run_report(raw=b"".join(
            line for line in self.raw.splitlines(keepends=True) if b",ECHO," not in line
        ))
        self.assertEqual(missing_raw["checks"][-1]["detail"]["缺未复权收盘价"], ["ECHO"])

    def test_account_freshness_and_completeness(self):
        account = json.loads(self.snapshot)
        account["captured_at"] = "2024-12-30T21:00:15+00:00"
        stale = self.run_report(account=account)
        self.assertEqual(stale["status"], "blocked")
        self.assertEqual(stale["draft"], [])
        account = json.loads(self.snapshot)
        account["positions_and_orders_complete"] = False
        incomplete = self.run_report(account=account)
        self.assertEqual(incomplete["status"], "blocked")
        self.assertEqual(incomplete["draft"], [])

    def test_pending_buys_reserve_cash_and_sales_do_not_fund_buys(self):
        account = json.loads(self.snapshot)
        account["settled_cash"] = 19000
        account["available_cash"] = 19000
        account["open_orders"].extend([
            {"id": "buy", "symbol": "ECHO", "side": "BUY", "remaining": 3, "limit_price": 3700},
            {"id": "sell", "symbol": "ALFA", "side": "SELL", "remaining": 10, "limit_price": 95},
        ])
        report = self.run_report(account=account)
        cash = next(check for check in report["checks"] if check["name"] == "现金与费用缓冲")
        self.assertFalse(cash["passed"])
        self.assertAlmostEqual(cash["detail"]["挂单预留"], 11911.9)
        self.assertEqual(report["status"], "blocked")
        self.assertEqual(report["draft"], [])

    def test_pending_buy_cannot_back_a_sale(self):
        account = json.loads(self.snapshot)
        account["open_orders"].append({
            "id": "sell", "symbol": "ECHO", "side": "SELL", "remaining": 13, "limit_price": 150,
        })
        report = self.run_report(account=account)
        self.assertFalse(next(
            check["passed"] for check in report["checks"] if check["name"] == "卖出挂单库存"
        ))
        self.assertEqual(report["draft"], [])
        self.assertFalse(self.store.acknowledge(report["id"], "blocked"))

    def test_duplicate_run_and_concurrent_review(self):
        with ThreadPoolExecutor(max_workers=3) as pool:
            reports = list(pool.map(lambda _: self.run_report(), range(3)))
            self.assertEqual(len({report["id"] for report in reports}), 1)
            self.assertEqual(len(self.store.list()), 1)
            key = reports[0]["id"]
            saved = list(pool.map(lambda n: self.store.acknowledge(key, str(n)), range(3)))
        self.assertEqual(saved.count(True), 1)
        self.assertEqual(self.run_report()["status"], "reviewed")

    def test_latest_completed_session_respects_early_close(self):
        before = datetime(2024, 11, 29, 17, 59, tzinfo=timezone.utc)
        after = datetime(2024, 11, 29, 18, 1, tzinfo=timezone.utc)
        self.assertEqual(latest_completed_session(before), date(2024, 11, 27))
        self.assertEqual(latest_completed_session(after), date(2024, 11, 29))
