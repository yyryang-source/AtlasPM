"""Offline regression tests for explicitly guarded IBKR local Gateway GET capture."""

import copy
import io
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from atlaspm.execution.ibkr_gateway import (
    FixtureGatewayTransport, LocalGatewayTransport,
    collect_ibkr_readonly_account, redacted_console_summary,
    require_private_output, validate_gateway_url,
)


SAMPLE = Path(__file__).resolve().parents[2] / "examples" / "ibkr_gateway_synthetic.json"
ACCOUNT = "DU1234567"


class IBKRGatewayTests(unittest.TestCase):
    def setUp(self):
        self.fixture = json.loads(SAMPLE.read_text(encoding="utf-8"))
        self.transport = FixtureGatewayTransport(copy.deepcopy(self.fixture))

    def capture(self):
        return collect_ibkr_readonly_account(
            self.transport, account_id=ACCOUNT, fixture_mode=True
        )

    def test_offline_capture_has_no_trading_authority(self):
        report = self.capture()
        self.assertEqual(report["source"], "synthetic_ibkr_fixture")
        self.assertFalse(report["gateway_tls_certificate_validated"])
        self.assertFalse(report["live_execution_enabled"])
        self.assertFalse(report["broker_margin_or_collateral_verified"])
        self.assertFalse(report["ready_for_institutional_risk_gate"])
        self.assertEqual(report["position_count"], 2)
        self.assertEqual(report["asset_class_counts"], {"STK": 1, "BOND": 1})
        self.assertEqual(report["USD_ledger_summary"]["net_liquidation_ibkr_field"], 123950)
        self.assertEqual(self.transport.called, [
            "/iserver/auth/status",
            "/portfolio/accounts",
            "/portfolio/DU1234567/ledger",
            "/portfolio2/DU1234567/positions",
        ])

    def test_console_summary_never_prints_full_account_or_dollar_values(self):
        report = self.capture()
        summary = redacted_console_summary(
            report, Path("local_data/ibkr_private_raw.json")
        )
        text = json.dumps(summary)
        self.assertNotIn(ACCOUNT, text)
        self.assertNotIn("123950", text)
        self.assertEqual(summary["account_hint"], "****4567")

    def test_authentication_required(self):
        self.transport.responses["/iserver/auth/status"]["authenticated"] = False
        with self.assertRaisesRegex(ValueError, "not authenticated"):
            self.capture()
        self.assertEqual(self.transport.called, ["/iserver/auth/status"])

    def test_exact_account_selection_required(self):
        self.transport.responses["/portfolio/accounts"] = []
        with self.assertRaisesRegex(ValueError, "not found uniquely"):
            self.capture()
        self.transport.responses["/portfolio/accounts"] = [{
            "id": ACCOUNT, "accountId": ACCOUNT, "currency": "USD"
        }] * 2
        with self.assertRaisesRegex(ValueError, "not found uniquely"):
            self.capture()

    def test_non_usd_account_or_positions_rejected(self):
        self.transport.responses["/portfolio/accounts"][0]["currency"] = "EUR"
        with self.assertRaisesRegex(ValueError, "USD base-currency"):
            self.capture()
        self.transport.responses["/portfolio/accounts"][0]["currency"] = "USD"
        self.transport.responses["/portfolio2/DU1234567/positions"][0]["currency"] = "EUR"
        with self.assertRaisesRegex(ValueError, "Non-USD"):
            self.capture()

    def test_missing_ledger_fields_rejected(self):
        self.transport.responses["/portfolio/DU1234567/ledger"]["USD"].pop("settledcash")
        with self.assertRaisesRegex(ValueError, "lacks settledcash"):
            self.capture()

    def test_missing_security_contract_identity_blocks(self):
        self.transport.responses["/portfolio2/DU1234567/positions"][1].pop("conid")
        with self.assertRaisesRegex(ValueError, "missing contract ID"):
            self.capture()

    def test_invalid_endpoint_and_gateway_origin_rejected(self):
        for url in (
            "http://localhost:5000/v1/api",
            "https://evil.example:5000/v1/api",
            "https://localhost:5000/iserver/api",
            "https://user:pass@localhost:5000/v1/api",
            "https://localhost:5000/v1/api?q=1",
            "https://localhost/v1/api",
        ):
            with self.subTest(url=url), self.assertRaises(ValueError):
                validate_gateway_url(url)
        self.assertEqual(
            validate_gateway_url("https://127.0.0.1:5000/v1/api"),
            "https://127.0.0.1:5000/v1/api"
        )
        client = LocalGatewayTransport("https://localhost:5000/v1/api")
        for bad in ("http://evil.example", "//evil", "/../orders", "/a?b=c"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                client.get(bad)

    def test_gateway_spaces_requests_and_brackets_order_snapshots_at_broker_rate(self):
        clock = 10.0
        sent_at = []
        client = LocalGatewayTransport()

        def advance(delay):
            nonlocal clock
            clock += delay

        def response(request, *, timeout):
            sent_at.append(clock)
            return io.BytesIO(b"{}")

        with (patch("atlaspm.quant.ibkr_gateway.time.monotonic", side_effect=lambda: clock),
              patch("atlaspm.quant.ibkr_gateway.time.sleep", side_effect=advance),
              patch.object(client.opener, "open", side_effect=response)):
            client.get("/iserver/account/orders")
            client.get("/iserver/accounts")
            client.get("/iserver/account/orders")
            client.get("/iserver/account/trades")
            client.get("/iserver/account/trades")

        self.assertGreaterEqual(sent_at[1] - sent_at[0], .10)
        self.assertGreaterEqual(sent_at[2] - sent_at[0], 5.0)
        self.assertGreaterEqual(sent_at[3] - sent_at[2], .10)
        self.assertGreaterEqual(sent_at[4] - sent_at[3], 5.0)

    def test_private_raw_capture_cannot_be_saved_in_repo_root(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            private = root / "local_data"
            private.mkdir()
            good = require_private_output(
                private / "private.json", project_root=root
            )
            self.assertEqual(good.name, "private.json")
            with self.assertRaisesRegex(ValueError, "under local_data"):
                require_private_output(root / "sensitive.json", project_root=root)
            with self.assertRaisesRegex(ValueError, "under local_data"):
                require_private_output(root / "examples" / "secret.json", project_root=root)

    def test_untrusted_account_selector_rejected(self):
        for account in ("", "../orders", "X?account=Y", "ABC/XYZ"):
            with self.subTest(account=account), self.assertRaisesRegex(ValueError, "account_id"):
                collect_ibkr_readonly_account(
                    self.transport, account_id=account, fixture_mode=True
                )


class PaperSyncTests(unittest.TestCase):
    def setUp(self):
        import os
        from atlaspm.execution import ibkr_gateway as gateway
        self.gateway = gateway
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        cwd = Path.cwd()
        os.chdir(temp.name)
        self.addCleanup(os.chdir, cwd)
        self.account = "DUR1234567"
        fixture = json.loads(SAMPLE.read_text(encoding="utf-8"))
        responses = fixture["responses"]
        responses["/iserver/auth/status"].update(established=True, competing=False)
        responses["/sso/validate"] = {"RESULT": True, "LOGIN_TYPE": 2}
        responses["/portfolio/accounts"] = [{"id": self.account, "accountId": self.account,
            "currency": "USD", "type": "DEMO", "brokerageAccess": True}]
        responses[f"/portfolio/{self.account}/ledger"] = responses.pop(f"/portfolio/{ACCOUNT}/ledger")
        responses[f"/portfolio2/{self.account}/positions"] = []
        self.transport = FixtureGatewayTransport(fixture)

    def test_dur_identity_independent_sso_and_account_checks(self):
        report = collect_ibkr_readonly_account(self.transport, account_id=self.account,
                                               fixture_mode=True, paper_only=True)
        self.assertFalse(report["paper_identity_verified"])
        self.assertFalse(report["broker_order_submission_enabled"])
        self.assertEqual(self.transport.called[:3],
                         ["/iserver/auth/status", "/sso/validate", "/portfolio/accounts"])
        for invalid in ({"RESULT":True,"LOGIN_TYPE":1}, {"RESULT":True,"LOGIN_TYPE":True},
                        {"RESULT":True,"LOGIN_TYPE":2,"loginType":1}, {"RESULT":True}):
            self.transport.called.clear()
            self.transport.responses["/sso/validate"] = invalid
            with self.assertRaisesRegex(ValueError, "PAPER_LOGIN_TYPE"):
                collect_ibkr_readonly_account(self.transport, account_id=self.account,
                                              fixture_mode=True, paper_only=True)
            self.assertEqual(self.transport.called, ["/iserver/auth/status", "/sso/validate"])
        self.transport.responses["/sso/validate"] = {"RESULT":True,"loginType":2}
        self.transport.responses["/portfolio/accounts"][0]["type"] = "INDIVIDUAL"
        with self.assertRaisesRegex(ValueError, "PAPER_DEMO_ACCOUNT"):
            collect_ibkr_readonly_account(self.transport, account_id=self.account,
                                          fixture_mode=True, paper_only=True)

    def test_configure_and_sync_stop_without_overwriting_last_snapshot(self):
        g = self.gateway
        cert = Path("local.pem")
        cert.write_text("fixture; no actual TLS transport", encoding="utf-8")
        g.configure_paper_sync(account=self.account, ca_file=cert)
        self.assertNotIn("password", g.PAPER_PROFILE.read_text(encoding="utf-8"))
        self.assertEqual(self.transport.called, [])
        from unittest.mock import Mock
        self.transport.keepalive = Mock(return_value=True)
        with patch.object(g, "LocalGatewayTransport", return_value=self.transport), patch.object(g.time, "sleep"):
            result = g.sync_paper_account(cycles=2)
            self.assertEqual(result["completed_cycles"], 2)
            self.assertEqual(self.transport.keepalive.call_count, 2)
            self.assertFalse(result["broker_order_submission_enabled"])
            self.assertNotIn(self.account, json.dumps(result))
            saved = g.PAPER_SNAPSHOT.read_bytes()
            self.transport.responses["/sso/validate"]["LOGIN_TYPE"] = 1
            with self.assertRaisesRegex(ValueError, "PAPER_LOGIN_TYPE"):
                g.sync_paper_account()
            self.assertEqual(saved, g.PAPER_SNAPSHOT.read_bytes())
            status = json.loads(g.PAPER_SYNC_STATUS.read_text(encoding="utf-8"))
            self.assertEqual(status["state"], "BLOCKED_OPERATOR_REVIEW_REQUIRED")
            self.assertFalse(status["snapshot_current"])
            self.assertFalse(Path("local_data/ibkr_paper_sync.lock").exists())

    def test_http_failure_redacts_endpoint_and_stops(self):
        g = self.gateway
        cert = Path("local.pem")
        cert.write_text("fixture", encoding="utf-8")
        g.configure_paper_sync(account=self.account, ca_file=cert)
        failure = g.GatewayHTTPError(401, endpoint=f"/portfolio/{self.account}/ledger")
        with patch.object(g, "LocalGatewayTransport", return_value=self.transport):
            with patch.object(self.transport, "get", side_effect=failure):
                with self.assertRaisesRegex(ValueError, r"HTTP_401 at /portfolio/\{account\}/ledger"):
                    g.sync_paper_account()
        status = json.loads(g.PAPER_SYNC_STATUS.read_text(encoding="utf-8"))
        self.assertEqual(status["failed_endpoint"], "/portfolio/{account}/ledger")
        self.assertNotIn(self.account, json.dumps(status))
        self.assertFalse(status["snapshot_current"])
        self.assertFalse(g.PAPER_SNAPSHOT.exists())
        self.assertEqual(g.GatewayHTTPError(401, endpoint="/secret/path").endpoint, "unknown")
        self.assertTrue(g._PAPER_SSO_ATTEMPT.exists())

    def test_sso_cooldown_persists_across_invocations(self):
        g = self.gateway
        from datetime import datetime, timezone, timedelta
        from unittest.mock import Mock
        g._write_sync_json(g._PAPER_SSO_ATTEMPT, {
            "attempted_at_utc": (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()})
        with patch.object(g.time, "sleep") as sleeper:
            g._paper_sso_cooldown(Mock())
        delay = sleeper.call_args.args[0]
        self.assertGreater(delay, 50)
        self.assertLessEqual(delay, 55)
        g._write_sync_json(g._PAPER_SSO_ATTEMPT, {
            "attempted_at_utc": (datetime.now(timezone.utc) - timedelta(seconds=90)).isoformat()})
        with patch.object(g.time, "sleep") as sleeper:
            g._paper_sso_cooldown(Mock())
        sleeper.assert_not_called()

    def test_heartbeat_fixed_route_and_competing_session_rejected(self):
        client = LocalGatewayTransport()
        with patch.object(client, "_send_json", return_value={"iserver":{"authStatus":{
                "authenticated": True, "connected": True, "competing": False}}}) as send:
            self.assertTrue(client.keepalive())
            request = send.call_args.args[0]
            self.assertEqual(request.get_method(), "POST")
            self.assertEqual(request.full_url, client.base_url + "/tickle")
            send.return_value["iserver"]["authStatus"]["competing"] = True
            with self.assertRaisesRegex(ValueError, "PAPER_HEARTBEAT"):
                client.keepalive()


if __name__ == "__main__":
    unittest.main()
