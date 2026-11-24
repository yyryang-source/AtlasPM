"""No network or subscription billing in CI."""
import json
import os
import unittest
from io import BytesIO
from unittest.mock import patch

from atlaspm.data.api_gateway import Gateway, GatewayError, Response
from atlaspm.research import options_intelligence as opt


def alert(**updates):
    row = dict(id="a", ticker="SPY", type="call", option_chain="SPY261218C00650000",
               created_at="2026-01-05T15:02:00Z", start_time="2026-01-05T15:00:00Z",
               end_time="2026-01-05T15:01:00Z", total_premium="200000",
               total_ask_side_prem="180000", total_bid_side_prem="10000",
               volume=200, open_interest=100, iv_start="0.2", iv_end="0.25",
               has_multileg=True, has_singleleg=False, has_sweep=True,
               has_floor=False, all_opening_trades=False)
    row.update(updates)
    return row


def response(rows):
    return Response("unusual_whales", "fixture", "2026-01-05T15:03:00Z",
                    json.dumps({"data": rows}).encode())


class OptionsIntelligenceTests(unittest.TestCase):
    def test_no_direction_inference_and_overlap_dedup(self):
        out = opt.analyze_alerts(response([alert(), alert(), alert(id="b")]),
                                 as_of="2026-01-05T15:03:00Z")
        self.assertEqual(len(out["alerts"]), 2)
        self.assertEqual(out["excluded"]["duplicate_id"], 1)
        self.assertEqual(out["clusters"][0]["alerts"], 2)
        row = out["alerts"][0]
        self.assertEqual(row["alert_ask_minus_bid_premium"], "170000")
        self.assertEqual(row["volume_oi_ratio"], "2")
        self.assertEqual(row["iv_change"], "0.05")
        self.assertIsNone(row["directional_signal"])
        self.assertIn("MULTILEG_OR_UNKNOWN_LEG_STRUCTURE", row["risk_notes"])
        self.assertFalse(out["premium_aggregated_across_alerts"])
        self.assertFalse(out["external_ai_allowed"])

    def test_conflicts_future_and_missing_oi(self):
        with self.assertRaisesRegex(GatewayError, "Conflicting"):
            opt.analyze_alerts(response([alert(), alert(volume=201)]), as_of="2026-01-05T15:03:00Z")
        r = opt.analyze_alerts(response([alert(open_interest=0)]), as_of="2026-01-05T15:03:00Z")
        self.assertIsNone(r["alerts"][0]["volume_oi_ratio"])
        r = opt.analyze_alerts(response([alert()]), as_of="2026-01-05T15:01:30Z")
        self.assertEqual(r["excluded"]["after_cutoff"], 1)
        for change in ({"total_ask_side_prem": "500000"}, {"has_multileg": "false"},
                       {"created_at": "2026-01-05T15:04:00Z"}):
            with self.assertRaises(ValueError):
                opt.analyze_alerts(response([alert(**change)]), as_of="2026-01-05T15:03:00Z")

    def test_gateway_rights_budget_and_scope(self):
        calls = []
        def opener(request, timeout):
            calls.append(request)
            return BytesIO(b'{"data":[]}')
        gateway = Gateway(opener=opener)
        url = opt.BASE + "/api/option-trades/flow-alerts"
        with patch.dict(os.environ, {"ATLASPM_UW_RIGHTS": "", "UW_TOKEN": "test-key",
                                     "ATLASPM_UW_MAX_REQUESTS": "1"}):
            with self.assertRaisesRegex(ValueError, "ATLASPM_UW_RIGHTS"):
                gateway.fetch("unusual_whales", url)
        with patch.dict(os.environ, {"ATLASPM_UW_RIGHTS": "local-reviewed", "UW_TOKEN": "test-key",
                                     "ATLASPM_UW_MAX_REQUESTS": "1"}):
            with self.assertRaisesRegex(GatewayError, "outside designated"):
                gateway.fetch("unusual_whales", opt.BASE + "/api/stock/SPY/ohlc/1m")
            report = opt.fetch_alerts(ticker="SPY", gateway=gateway)
            self.assertEqual(report["alerts"], [])
            with self.assertRaisesRegex(GatewayError, "budget exhausted"):
                opt.fetch_context("greek_exposure", "SPY", gateway=gateway)
        self.assertEqual(len(calls), 1)
        self.assertNotIn("test-key", json.dumps(gateway.health()))

    def test_massive_realtime_not_enabled(self):
        with self.assertRaisesRegex(GatewayError, "historical"):
            Gateway(opener=lambda *a, **kw: self.fail("network")).fetch(
                "massive", "https://api.massive.com/v3/snapshot/options/SPY", key="test")
