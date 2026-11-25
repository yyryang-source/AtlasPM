"""Offline dedicated-session market-data tests; no broker account or orders."""
from datetime import UTC, datetime
import json
import os
import unittest
from unittest.mock import patch

from atlaspm.execution.ibkr_gateway import LocalGatewayTransport
from atlaspm.execution.ibkr_market_data import MarketPool, normalize_snapshot, resolve_us_equities


class Transport:
    def __init__(self):
        self.calls = []
        self.fail_snapshot = False
        self.fail_release = False

    def get(self, endpoint):
        self.calls.append(("GET",endpoint))
        if endpoint == "/iserver/auth/status":
            return {"authenticated":True,"connected":True,"competing":False}
        return {"accounts":["synthetic"]}

    def market_stock_lookup(self, symbols):
        return {s: [{"assetClass": "STK", "contracts": [
            {"conid": i + 10, "isUS": True}, {"conid": i + 100, "isUS": False}]}]
                for i, s in enumerate(symbols)}

    def market_contract_info(self, conid):
        return {"con_id": conid, "symbol": {10:"AAA", 11:"BBB"}[conid],
                "instrument_type": "STK", "currency": "USD"}

    def market_snapshot(self, ids, fields):
        self.calls.append(("snapshot",tuple(ids)))
        if self.fail_snapshot:
            raise ValueError("timeout")
        return [{"conid":c,"6509":"RpB","_updated":int(datetime.now(UTC).timestamp()*1000),
                 "84":"99.9","86":"100.1","31":"100","7762":"12345"} for c in ids]

    def market_history(self, conid, period, bar):
        self.calls.append(("history",conid))
        return {"barLength":300,"volumeFactor":100,"data":[
            {"t":1700000000000,"o":100,"h":102,"l":99,"c":101,"v":10}]}

    def market_unsubscribe(self, c):
        self.calls.append(("release",c))
        return {"success":not self.fail_release}


class MarketPoolTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {"ATLASPM_IBKR_MARKET_DATA_RIGHTS":"fixture"})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.transport = Transport()
        self.pool = MarketPool(self.transport,available_lines=2,dedicated_session=True,
                               clock=lambda:0.0,sleep=lambda seconds:None)

    def test_priority_rotation_never_oversubscribes(self):
        s = self.pool.set_universe({3:2,2:3,1:3})
        self.assertEqual(s["selected"],[1,2])
        self.assertEqual(s["waiting_count"],1)
        self.pool.connect()
        p = self.pool.poll()
        self.assertEqual(p["reserved_lines"],2)
        self.assertTrue(p["quotes"][0]["fresh_realtime_book_candidate"])
        self.assertFalse(p["broker_order_submission_enabled"])
        self.pool.set_universe({3:4,2:3,1:1})
        p = self.pool.poll()
        self.assertEqual(p["selected"],[3,2])
        self.assertLess(self.transport.calls.index(("release",1)),self.transport.calls.index(("snapshot",(3,2))))
        self.assertTrue(self.pool.close()["released_all_owned"])

    def test_timeout_retains_reservations_and_blocks_new_subscriptions(self):
        self.pool.set_universe({1:1})
        self.pool.connect()
        self.transport.fail_snapshot = True
        with self.assertRaises(ValueError):
            self.pool.poll()
        self.pool.set_universe({2:1,3:1})
        self.pool.connect()
        with self.assertRaisesRegex(ValueError,"uncertain"):
            self.pool.poll()
        self.transport.fail_release = True
        self.assertEqual(self.pool.close()["remaining_lines"],1)
        self.assertNotIn(("snapshot",(2,3)),self.transport.calls)
        self.transport.fail_release = False
        self.assertTrue(self.pool.close()["released_all_owned"])

    def test_delayed_missing_crossed_and_future_are_not_fresh(self):
        now = datetime.now(UTC)
        stamp = int(now.timestamp()*1000)
        raw = [{"conid":1,"6509":"D","_updated":stamp,"84":"100","86":"101"},
               {"conid":2,"6509":"R","_updated":stamp,"84":"101","86":"100"},
               {"conid":3,"6509":"R","_updated":stamp+100000,"84":"100","86":"101"}]
        q = normalize_snapshot(raw,[1,2,3,4],received_at=now)
        self.assertTrue(all(not r["fresh_realtime_book_candidate"] for r in q))
        self.assertTrue(all(r["midpoint"] is None for r in q))
        self.assertEqual(q[-1]["availability"],"MISSING")
        with self.assertRaises(ValueError):
            normalize_snapshot([raw[0],raw[0]],[1,2],received_at=now)

    def test_history_uses_existing_transport_and_preserves_unverified_units(self):
        self.pool.set_universe({1:1})
        self.pool.connect()
        report=self.pool.history(1)
        self.assertEqual(report["rows"][0]["v"],10)
        self.assertEqual(report["provider_metadata"]["volumeFactor"],100)
        self.assertFalse(report["timestamp_and_volume_units_verified"])
        self.assertFalse(report["approved_for_model_training"])
        with self.assertRaises(ValueError):
            self.pool.history(2)

    def test_transport_only_scoped_market_post_and_get(self):
        transport = LocalGatewayTransport()
        with patch.object(transport,"_send_json",return_value={"success":True}) as send:
            transport.market_unsubscribe(123)
            req = send.call_args.args[0]
            self.assertTrue(req.full_url.endswith("/iserver/marketdata/unsubscribe"))
            self.assertEqual(req.get_method(),"POST")
            self.assertEqual(json.loads(req.data),{"conid":123})
            transport.market_snapshot([123],["31","84"])
            req = send.call_args.args[0]
            self.assertEqual(req.get_method(),"GET")
            self.assertIn("/iserver/marketdata/snapshot?",req.full_url)
            with self.assertRaises(ValueError):
                transport._request("/iserver/account/DU123/orders",preview={"orders":[]})
            transport.market_history(123,"1d","5min")
            self.assertIn("/iserver/marketdata/history?",send.call_args.args[0].full_url)
            self.assertEqual(send.call_count,3)


    def test_symbol_mapping_checks_us_listing_and_contract_details(self):
        result = resolve_us_equities(self.transport, ["AAA", "BBB"])
        self.assertEqual(result["AAA"]["conid"], 10)
        self.assertEqual(result["BBB"]["currency"], "USD")
        self.assertFalse(result["AAA"]["security_master_identity_verified"])
        with patch.object(self.transport, "market_contract_info", return_value={
                "con_id": 10, "symbol": "AAA", "instrument_type": "STK", "currency": "CAD"}):
            with self.assertRaisesRegex(ValueError, "Contract identity mismatch"):
                resolve_us_equities(self.transport, ["AAA"])
        with patch.object(self.transport, "market_stock_lookup", return_value={
                "AAA": [{"assetClass":"STK", "contracts":[{"conid":10,"isUS":True},
                                                         {"conid":11,"isUS":True}]}]}):
            with self.assertRaisesRegex(ValueError, "ambiguous"):
                resolve_us_equities(self.transport, ["AAA"])
        with self.assertRaises(ValueError):
            resolve_us_equities(self.transport, ["AAA", "AAA"])

    def test_symbol_lookup_routes_are_get_only_and_bounded(self):
        transport = LocalGatewayTransport()
        with patch.object(transport, "_send_json", return_value={}) as send:
            transport.market_stock_lookup(["AAA", "BBB"])
            request = send.call_args.args[0]
            self.assertEqual(request.get_method(), "GET")
            self.assertIn("/trsrv/stocks?symbols=AAA%2CBBB", request.full_url)
            transport.market_contract_info(10)
            self.assertTrue(send.call_args.args[0].full_url.endswith("/iserver/contract/10/info"))
            for bad in (["AAA,BBB"], ["AAA","AAA"], ["../X"]):
                with self.assertRaises(ValueError):
                    transport.market_stock_lookup(bad)
            self.assertEqual(send.call_count, 2)
