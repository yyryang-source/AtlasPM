"""Offline regression tests for explicitly guarded IBKR local Gateway GET capture."""

import copy
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

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


if __name__ == "__main__":
    unittest.main()
