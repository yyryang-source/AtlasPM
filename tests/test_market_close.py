from __future__ import annotations

import hashlib
import importlib.util
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

from atlaspm.close_drafts import draft_after_close
from atlaspm.market_close import approved_inputs, prepare_close
from atlaspm.rebalance_workbench import Policy, RunStore, _json, exchange_window


@unittest.skipUnless(importlib.util.find_spec("pandas_market_calendars"), "calendar extra required")
class ClosePipelineTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.day = date(2024, 11, 27)
        self.next_day = date(2024, 11, 29)
        self.sessions, self.close = exchange_window(self.day)
        self.manifest = self.root / "manifest.csv"
        self.manifest.write_text(
            "listing_symbol,provider_symbol,snapshot_utc\n"
            + "".join(f"{symbol},{symbol},2024-11-27T20:00:00+00:00\n" for symbol in ("AAA", "BBB", "CCC")),
            encoding="utf-8",
        )
        self.calls: list[tuple[str, date, date]] = []
        self.missing: set[str] = set()
        self.adjustment = 1.0

    def fetch(self, symbol, start, end):
        self.calls.append((symbol, start, end))
        sessions, _ = exchange_window(end - timedelta(days=1))
        selected = [stamp for stamp in sessions if start <= stamp < end]
        if symbol in self.missing:
            selected = selected[:-1]
        return [
            (stamp, (100 + (stamp - date(2023, 1, 1)).days * .05) * self.adjustment, 101.5)
            for stamp in selected
        ]

    def prepare(self, day=None, **kwargs):
        day = day or self.day
        _, close = exchange_window(day)
        return prepare_close(
            self.root / "market", self.manifest, day=day, now=close + timedelta(minutes=25),
            symbols=("AAA", "BBB"), fetch=self.fetch, source="fixture", **kwargs,
        )

    def test_incomplete_close_blocks_draft_and_retry_only_fetches_missing_names(self):
        account = json.dumps({
            "currency": "USD", "account_type": "cash", "account_id": "FIXTURE",
            "captured_at": (self.close + timedelta(minutes=25)).isoformat(),
            "positions_and_orders_complete": True, "managed_equities_only": True,
            "settled_cash": 100000, "available_cash": 100000, "positions": {}, "open_orders": [],
        }).encode()
        store = RunStore(self.root / "rebalance.sqlite3")
        self.missing.add("BBB")
        first = self.prepare()
        self.assertEqual(json.loads(first.read_text(encoding="utf-8"))["status"], "blocked")
        with self.assertRaisesRegex(ValueError, "blocked"):
            store.run_prepared(first, account)
        self.assertEqual(store.list(), [])
        self.missing.clear()
        self.calls.clear()
        success = self.prepare()
        self.assertEqual([record[0] for record in self.calls], ["BBB"])
        self.assertEqual(success, self.prepare())
        report = store.get(store.run_prepared(success, account))
        self.assertEqual(report["status"], "review")
        self.assertIn("market_audit", report["input_sha256"])
        self.assertTrue(all(order["reference_close"] == 101.5 for order in report["draft"]))
        (success.parent / "close.csv").write_text("invalid", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "differs"):
            store.run_prepared(success, account)

    def test_next_close_requests_short_overlap_and_reloads_on_revision(self):
        self.prepare()
        self.calls.clear()
        self.adjustment = .98
        result = self.prepare(self.next_day)
        self.assertEqual(json.loads(result.read_text(encoding="utf-8"))["updates"], {"reloaded": 2})
        sessions, _ = exchange_window(self.next_day)
        self.assertEqual(self.calls, [
            (symbol, start, self.next_day + timedelta(days=1))
            for symbol in ("AAA", "BBB") for start in (sessions[-6], sessions[0])
        ])

    def test_rate_or_batch_limit_resumes_without_starving_later_names(self):
        self.missing.add("AAA")
        first = self.prepare(max_symbols=1)
        second = self.prepare(max_symbols=1)
        self.assertNotEqual(first, second)
        self.assertEqual([item[0] for item in self.calls], ["AAA", "BBB"])
        self.missing.clear()
        success = self.prepare(max_symbols=1)
        self.assertEqual(json.loads(success.read_text(encoding="utf-8"))["status"], "ready")

    def test_reference_only_names_and_rebalance_schedule(self):
        audit = self.prepare(schedule="month-end", additional_quotes=("CCC",))
        self.assertEqual(self.calls[-1], ("CCC", self.day, self.day + timedelta(days=1)))
        with self.assertRaisesRegex(ValueError, "no scheduled rebalance"):
            approved_inputs(audit)
        same_day_daily = self.prepare(additional_quotes=("CCC",))
        _, snapshot, _, references, _ = approved_inputs(same_day_daily)
        self.assertEqual(json.loads(snapshot)["symbols"], ["AAA", "BBB"])
        self.assertIn(b"CCC,101.5", references)

    def test_early_close_delay_prevents_fetch_before_eligible(self):
        _, close = exchange_window(self.next_day)
        with self.assertRaisesRegex(ValueError, "post-close delay"):
            prepare_close(
                self.root / "market", self.manifest, day=self.next_day, now=close + timedelta(minutes=19),
                symbols=("AAA",), fetch=self.fetch, source="fixture",
            )
        self.assertEqual(close.hour, 18)
        self.assertEqual(self.calls, [])

    def test_close_handoff_refreshes_account_after_supplementing_marks_and_reuses_draft(self):
        market = self.root / "market"
        store = RunStore(self.root / "workbench.sqlite3")
        observed = self.close + timedelta(minutes=25)
        snapshot = {
            "account_id": "DU0000000", "currency": "USD", "account_type": "cash",
            "captured_at": observed.isoformat(), "positions_and_orders_complete": True,
            "managed_equities_only": True, "settled_cash": 100000, "available_cash": 100000,
            "positions": {"CCC": 1}, "open_orders": [],
        }
        self.missing.add("BBB")
        partial = self.prepare()
        self.missing.clear()
        ready = self.prepare()
        marks = []

        def account_marks(day, symbols):
            marks.append((day, symbols))
            return self.prepare(additional_quotes=symbols)

        refreshed = snapshot | {"positions": {"CCC": 3}}
        with patch("atlaspm.close_drafts.collect_cash_equity_snapshot", side_effect=[snapshot, refreshed]) as read:
            args = {"account_id": snapshot["account_id"], "policy": Policy(), "now": observed,
                    "prepare_account_marks": account_marks}
            self.assertEqual(draft_after_close(market, partial, store, object(), **args)["status"], "market_blocked")
            read.assert_not_called()
            result = draft_after_close(market, ready, store, object(), **args)
            self.assertEqual(result["status"], "review_available")
            self.assertEqual(draft_after_close(market, ready, store, object(), **args), result)
            later = args | {"now": self.close + timedelta(hours=5)}
            self.assertEqual(draft_after_close(market, ready, store, object(), **later), result)
            self.assertEqual(read.call_count, 2)
        self.assertEqual(marks, [(self.day, ("CCC",))])
        draft = store.get(result["run_id"])["draft"]
        self.assertEqual(next(row["shares"] for row in draft if row["symbol"] == "CCC"), 3)

    def test_new_pending_holding_during_quote_refresh_blocks_until_all_marks_are_ready(self):
        market = self.root / "market"
        store = RunStore(self.root / "workbench.sqlite3")
        observed = self.close + timedelta(minutes=25)
        snapshot = {"positions": {"CCC": 1}, "open_orders": []}
        new_order = snapshot | {"open_orders": [{"symbol": "DDD"}]}
        ready = self.prepare()
        with patch("atlaspm.close_drafts.collect_cash_equity_snapshot", side_effect=[snapshot, new_order]):
            result = draft_after_close(
                market, ready, store, object(), account_id="DU0000000", policy=Policy(), now=observed,
                prepare_account_marks=lambda _, symbols: self.prepare(additional_quotes=symbols),
            )
        self.assertEqual(result["status"], "account_marks_blocked")
        self.assertEqual(result["missing_symbols"], ["DDD"])
        self.assertEqual(store.list(), [])

    def test_legacy_handoff_is_reused_only_if_the_full_policy_matches(self):
        market = self.root / "market"
        store = RunStore(self.root / "workbench.sqlite3")
        observed = self.close + timedelta(minutes=25)
        snapshot = {
            "account_id": "DU0000000", "currency": "USD", "account_type": "cash",
            "captured_at": observed.isoformat(), "positions_and_orders_complete": True,
            "managed_equities_only": True, "settled_cash": 100000, "available_cash": 100000,
            "positions": {}, "open_orders": [],
        }
        ready = self.prepare()
        default = Policy()
        existing = store.run_prepared(ready, _json(snapshot).encode(), default,
                                      account_source="local_ibkr_cash_equity_snapshot")
        legacy = hashlib.sha256(_json([approved_inputs(ready)[-1], snapshot["account_id"], default.allocation]).encode()).hexdigest()
        with closing(sqlite3.connect(market / "draft_handoffs.sqlite3")) as db, db:
            db.execute("CREATE TABLE handoffs (id TEXT PRIMARY KEY, run_id TEXT NOT NULL)")
            db.execute("INSERT INTO handoffs VALUES (?, ?)", (legacy, existing))
        with patch("atlaspm.close_drafts.collect_cash_equity_snapshot", return_value=snapshot) as read:
            args = {"account_id": snapshot["account_id"], "now": observed,
                    "prepare_account_marks": lambda *_: self.fail("unexpected missing quotes")}
            old = draft_after_close(market, ready, store, object(), policy=default, **args)
            self.assertEqual(old["run_id"], existing)
            read.assert_not_called()
            new = draft_after_close(market, ready, store, object(), policy=Policy(top=1), **args)
            self.assertNotEqual(new["run_id"], existing)
            self.assertEqual(store.get(new["run_id"])["policy"]["top"], 1)
            read.assert_called_once()
