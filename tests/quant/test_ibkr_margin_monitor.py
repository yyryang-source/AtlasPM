"""Zero-network regression suite for IBKR v0.4.10 margin monitoring."""

import copy
import json
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from atlaspm.execution.ibkr_gateway import FixtureGatewayTransport, collect_ibkr_readonly_account
from atlaspm.risk.ibkr_margin_monitor import (
    collect_ibkr_margin_monitor, normalize_ibkr_margin_summary, redacted_margin_console,
)

ROOT = Path(__file__).resolve().parents[2]
ACCOUNT = "DU1234567"
ENDPOINT = f"/portfolio/{ACCOUNT}/summary"


class IBKRMarginMonitorTests(unittest.TestCase):
    def setUp(self):
        self.fixture = json.loads(
            (ROOT / "examples/ibkr_gateway_synthetic.json").read_text(encoding="utf-8")
        )

    def run_monitor(self, fixture=None, **kwargs):
        transport = FixtureGatewayTransport(copy.deepcopy(fixture or self.fixture))
        result = collect_ibkr_margin_monitor(
            transport, account_id=ACCOUNT, fixture_mode=True, **kwargs
        )
        return result, transport.called

    def test_complete_synthetic_request_sequence_and_trust_boundary(self):
        report, calls = self.run_monitor()
        self.assertEqual(calls, [
            "/iserver/auth/status", "/portfolio/accounts",
            "/portfolio/DU1234567/ledger", "/portfolio2/DU1234567/positions",
            ENDPOINT,
        ])
        self.assertEqual(report["monitor_state"], "NORMAL")
        self.assertEqual(report["data_integrity_block_reasons"], [])
        self.assertEqual(report["risk_alerts"], [])
        self.assertFalse(report["broker_observed_summary_fields"])
        self.assertFalse(report["IBKR_financing_debt_separately_verified"])
        self.assertFalse(report["ready_for_institutional_risk_gate"])
        self.assertFalse(report["live_execution_enabled"])
        self.assertGreater(report["margin_summary_usd_claim"]["excess_liquidity"], 0)

    def test_negative_liquidity_is_critical(self):
        fixture = copy.deepcopy(self.fixture)
        summary = fixture["responses"][ENDPOINT]
        summary["equitywithloanvalue-s"]["amount"] = 25000
        summary["availablefunds-s"]["amount"] = -25000
        summary["excessliquidity-s"]["amount"] = -5000
        report, _ = self.run_monitor(fixture)
        self.assertEqual(report["monitor_state"], "CRITICAL")
        self.assertIn("EXCESS_LIQUIDITY_DEPLETED", report["risk_alerts"])

    def test_margin_warning_threshold(self):
        report, _ = self.run_monitor(min_buffer_ratio=0.80)
        self.assertEqual(report["monitor_state"], "WATCH")
        self.assertIn("MARGIN_BUFFER_BELOW_POLICY_FLOOR", report["risk_alerts"])

    def test_equation_mismatch_blocks_data(self):
        fixture = copy.deepcopy(self.fixture)
        fixture["responses"][ENDPOINT]["availablefunds-s"]["amount"] += 5
        report, _ = self.run_monitor(fixture)
        self.assertEqual(report["monitor_state"], "BLOCKED_DATA")
        self.assertIn("AVAILABLE_FUNDS_NOT_RECONCILED", report["data_integrity_block_reasons"])

    def test_gateway_nlv_difference_blocks(self):
        fixture = copy.deepcopy(self.fixture)
        fixture["responses"][ENDPOINT]["netliquidation"]["amount"] += 100
        report, _ = self.run_monitor(fixture)
        self.assertIn("PORTFOLIO_NLV_VS_LEDGER_MISMATCH", report["data_integrity_block_reasons"])

    def test_commodity_segment_blocks(self):
        fixture = copy.deepcopy(self.fixture)
        fixture["responses"][ENDPOINT]["netliquidation-c"]["amount"] = 500
        report, _ = self.run_monitor(fixture)
        self.assertIn("NONZERO_COMMODITIES_SEGMENT_NOT_SUPPORTED", report["data_integrity_block_reasons"])

    def test_stale_timestamp_blocks(self):
        fixture = copy.deepcopy(self.fixture)
        old = datetime.now(timezone.utc) - timedelta(hours=2)
        ms = int(old.timestamp() * 1000)
        for row in fixture["responses"][ENDPOINT].values():
            row["timestamp"] = ms
        report, _ = self.run_monitor(fixture)
        self.assertIn("MARGIN_DATA_STALE", report["data_integrity_block_reasons"])

    def test_stale_one_field_and_timestamp_divergence_block(self):
        fixture = copy.deepcopy(self.fixture)
        fixture["responses"][ENDPOINT]["maintmarginreq-s"]["timestamp"] = int(
            (datetime.now(timezone.utc) - timedelta(minutes=4)).timestamp() * 1000
        )
        report, _ = self.run_monitor(fixture)
        self.assertIn("MARGIN_FIELD_TIMESTAMPS_DIVERGE", report["data_integrity_block_reasons"])

    def test_account_identity_and_authentication_enforced(self):
        fixture = copy.deepcopy(self.fixture)
        fixture["responses"][ENDPOINT]["accountcode"]["value"] = "OTHER"
        with self.assertRaisesRegex(ValueError, "account code mismatch"):
            self.run_monitor(fixture)
        fixture = copy.deepcopy(self.fixture)
        fixture["responses"]["/iserver/auth/status"]["authenticated"] = False
        with self.assertRaisesRegex(ValueError, "not authenticated"):
            self.run_monitor(fixture)

    def test_missing_null_nonfinite_and_bad_severity_rejected(self):
        for bad in (None, True, float("nan"), "123"):
            with self.subTest(value=str(bad)):
                fixture = copy.deepcopy(self.fixture)
                fixture["responses"][ENDPOINT]["excessliquidity-s"]["amount"] = bad
                with self.assertRaises(ValueError):
                    self.run_monitor(fixture)
        fixture = copy.deepcopy(self.fixture)
        fixture["responses"][ENDPOINT]["initmarginreq-s"]["isNull"] = True
        with self.assertRaisesRegex(ValueError, "null"):
            self.run_monitor(fixture)
        fixture = copy.deepcopy(self.fixture)
        fixture["responses"][ENDPOINT]["initmarginreq-s"]["severity"] = 2
        with self.assertRaisesRegex(ValueError, "severity"):
            self.run_monitor(fixture)

    def test_synthetic_timestamp_token_forbidden_in_real_mode(self):
        fixture = copy.deepcopy(self.fixture)
        transport = FixtureGatewayTransport(fixture)
        capture = collect_ibkr_readonly_account(transport, account_id=ACCOUNT, fixture_mode=True)
        capture["source"] = "local_authenticated_ibkr_gateway"
        capture["gateway_tls_certificate_validated"] = True
        with self.assertRaisesRegex(ValueError, "epoch-millisecond"):
            normalize_ibkr_margin_summary(
                fixture["responses"][ENDPOINT], capture, fixture_mode=False
            )

    def test_real_mode_does_not_invent_verified_financing(self):
        fixture = copy.deepcopy(self.fixture)
        transport = FixtureGatewayTransport(fixture)
        capture = collect_ibkr_readonly_account(transport, account_id=ACCOUNT, fixture_mode=True)
        capture["source"] = "local_authenticated_ibkr_gateway"
        capture["gateway_tls_certificate_validated"] = True
        now = datetime.now(timezone.utc)
        raw = fixture["responses"][ENDPOINT]
        for field in raw.values():
            field["timestamp"] = int(now.timestamp() * 1000)
        result = normalize_ibkr_margin_summary(raw, capture, fixture_mode=False, now_utc=now)
        self.assertTrue(result["broker_observed_summary_fields"])
        self.assertFalse(result["IBKR_financing_debt_separately_verified"])
        self.assertFalse(result["ready_for_institutional_risk_gate"])

    def foreign_ledger(self, **overrides):
        fields = (
            "cashbalance cashbalancefxsegment settledcash commoditymarketvalue "
            "corporatebondsmarketvalue cryptocurrencyvalue dividends funds "
            "futuremarketvalue futureoptionmarketvalue futuresonlypnl issueroptionsmarketvalue "
            "moneyfunds realizedpnl stockmarketvalue stockoptionmarketvalue tbillsmarketvalue "
            "tbondsmarketvalue unrealizedpnl warrantsmarketvalue"
        ).split()
        row = dict.fromkeys(fields, 0.0)
        row.update(currency="CNH", acctcode=ACCOUNT, secondkey="CNH", severity=0,
                   exchangerate=7.0, interest=0.02, netliquidationvalue=0.0)
        row.update(overrides)
        self.fixture["responses"][f"/portfolio/{ACCOUNT}/ledger"]["CNH"] = row
        return row

    def test_zero_foreign_ledger_does_not_duplicate_usd_nlv(self):
        self.foreign_ledger()
        report, _ = self.run_monitor()
        self.assertEqual(report["monitor_state"], "NORMAL")
        self.assertEqual(report["margin_summary_usd_claim"]["total_nlv"], 123950)
        self.assertTrue(report["foreign_ledger_risk_clear"])
        self.assertFalse(report["ready_for_institutional_risk_gate"])

    def test_nonzero_foreign_nlv_not_silently_excluded_even_if_fx_matches(self):
        self.foreign_ledger(netliquidationvalue=123950 * 7)
        report, _ = self.run_monitor()
        self.assertEqual(report["monitor_state"], "BLOCKED_DATA")
        self.assertIn("FOREIGN_NLV_UNEXPLAINED", report["data_integrity_block_reasons"])
        self.assertTrue(report["foreign_ledger_diagnostics"][0]["possible_base_to_foreign_nlv_translation"])
        self.assertEqual(report["margin_summary_usd_claim"]["total_nlv"], 123950)
        self.assertFalse(report["foreign_ledger_risk_clear"])
        self.assertFalse(report["ready_for_institutional_risk_gate"])

    def test_foreign_exposure_blocks_even_if_net_zero(self):
        self.foreign_ledger(settledcash=-5, stockmarketvalue=5)
        report, _ = self.run_monitor()
        self.assertEqual(report["monitor_state"], "BLOCKED_DATA")
        self.assertIn("NONZERO_FOREIGN_LEDGER_EXPOSURE", report["data_integrity_block_reasons"])

    def test_foreign_missing_unknown_and_invalid_fields_block(self):
        row = self.foreign_ledger()
        row.pop("cashbalance")
        report, _ = self.run_monitor()
        self.assertIn("FOREIGN_LEDGER_EXPOSURE_FIELDS_INCOMPLETE_OR_UNKNOWN", report["data_integrity_block_reasons"])
        self.foreign_ledger(unknown_amount=0)
        report, _ = self.run_monitor()
        self.assertEqual(report["monitor_state"], "BLOCKED_DATA")
        for invalid in (True, float("nan"), "0", None):
            self.foreign_ledger(settledcash=invalid)
            report, _ = self.run_monitor()
            self.assertIn("FOREIGN_LEDGER_NUMERIC_FIELDS_INVALID", report["data_integrity_block_reasons"])
        self.foreign_ledger(acctcode="OTHER")
        report, _ = self.run_monitor()
        self.assertIn("FOREIGN_LEDGER_IDENTITY_OR_SCHEMA_UNVERIFIED", report["data_integrity_block_reasons"])

    def test_foreign_ledger_base_disagreement_blocks(self):
        self.foreign_ledger()
        ledger = self.fixture["responses"][f"/portfolio/{ACCOUNT}/ledger"]
        ledger["BASE"] = dict(ledger["USD"], currency="BASE", netliquidationvalue=123960)
        report, _ = self.run_monitor()
        self.assertIn("BASE_VS_USD_LEDGER_MISMATCH", report["data_integrity_block_reasons"])
        ledger["BASE"]["netliquidationvalue"] = 123950
        report, _ = self.run_monitor()
        self.assertEqual(report["monitor_state"], "NORMAL")

    def test_console_redaction(self):
        report, _ = self.run_monitor()
        summary = redacted_margin_console(report, "local_data/monitor.json")
        data = json.dumps(summary)
        for secret in ("DU1234567", "123950", "110000", "80000"):
            self.assertNotIn(secret, data)
        self.assertEqual(summary["account_hint"], "****4567")


if __name__ == "__main__":
    unittest.main()
