"""Strict offline IBKR conid mapping diagnostics and privacy regressions."""

import copy
import json
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path

from atlaspm.ibkr_gateway import FixtureGatewayTransport, collect_ibkr_readonly_account
from atlaspm.ibkr_mapping import diagnose_ibkr_contract_mapping, mapping_redacted_summary


ROOT = Path(__file__).resolve().parents[1] / "examples"


class IBKRMappingTests(unittest.TestCase):
    def setUp(self):
        self.fixture = json.loads(
            (ROOT / "ibkr_gateway_synthetic.json").read_text(encoding="utf-8")
        )
        self.mapping = json.loads(
            (ROOT / "ibkr_mapping_synthetic.json").read_text(encoding="utf-8")
        )
        self.capture = collect_ibkr_readonly_account(
            FixtureGatewayTransport(self.fixture), account_id="DU1234567",
            fixture_mode=True,
        )

    def check(self):
        return diagnose_ibkr_contract_mapping(self.capture, self.mapping)

    def test_exact_fixture_conid_mapping_does_not_grant_risk_gate(self):
        r = self.check()
        self.assertEqual(r["mapping_status"], "IDENTITIES_MAPPED_REVIEW_REQUIRED")
        self.assertEqual(r["mapped_contract_count"], 2)
        self.assertEqual(r["mapping_block_reasons"], [])
        self.assertFalse(r["ready_for_institutional_risk_gate"])
        self.assertFalse(r["source_authentication_independently_proven"])
        self.assertFalse(r["broker_treasury_collateral_verified"])
        self.assertFalse(r["IBKR_financing_debt_separately_verified"])
        self.assertFalse(r["live_execution_enabled"])
        self.assertEqual(
            [p["atlaspm_type"] for p in r["positions"]],
            ["equity", "treasury"],
        )

    def test_signed_cash_and_interest_not_guessed_as_margin_debt(self):
        usd = self.capture["raw"]["USD_ledger"]["USD"]
        usd["settledcash"] = -50000
        usd["interest"] = 0.065
        r = self.check()
        self.assertEqual(r["mapping_status"], "IDENTITIES_MAPPED_REVIEW_REQUIRED")
        self.assertEqual(r["IBKR_settledcash_field_usd"], -50000)
        self.assertIsNone(r.get("calculated_margin_debt"))
        self.assertFalse(r["IBKR_interest_field_interpreted_as_accrued_debt"])
        self.assertIn(
            "IBKR_INTEREST_FIELD_IS_RATE_NOT_ACCRUED_DEBT", r["warnings"]
        )
        self.assertIn(
            "NEGATIVE_SETTLED_CASH_REQUIRES_MARGIN_DEBT_RECONCILIATION",
            r["warnings"]
        )

    def test_unmapped_and_unused_conids_block(self):
        self.mapping["contracts"][1]["conid"] = 333333
        r = self.check()
        self.assertEqual(r["mapping_status"], "BLOCK_MAPPING")
        self.assertEqual(r["unmapped_ibkr_conids"], [202020])
        self.assertEqual(r["manifest_conids_not_held"], [333333])
        self.assertIn("UNMAPPED_IBKR_CONTRACTS", r["mapping_block_reasons"])
        self.assertIn("MAPPED_CONTRACT_NOT_HELD", r["mapping_block_reasons"])

    def test_asset_class_mismatch_blocks(self):
        self.capture["raw"]["positions"][0]["assetClass"] = "OPT"
        r = self.check()
        self.assertIn("IBKR_ASSET_CLASS_MISMATCH", r["mapping_block_reasons"])

    def test_unconfirmed_bond_units_are_blocked(self):
        self.mapping["contracts"][1]["units_confirmed"] = False
        r = self.check()
        self.assertEqual(r["mapping_status"], "BLOCK_MAPPING")
        self.assertIn("IBKR_POSITION_UNITS_NOT_CONFIRMED", r["mapping_block_reasons"])

    def test_unreviewed_manual_identity_is_blocked(self):
        self.mapping["contracts"][0]["manually_reviewed"] = False
        self.assertIn("CONTRACT_IDENTITY_NOT_REVIEWED", self.check()["mapping_block_reasons"])

    def test_bond_is_not_automatically_assigned_a_cusip(self):
        self.mapping["contracts"] = self.mapping["contracts"][:1]
        r = self.check()
        self.assertIn("UNMAPPED_IBKR_CONTRACTS", r["mapping_block_reasons"])
        self.assertEqual(r["mapped_contract_count"], 1)

    def test_market_value_alias_and_conflict(self):
        p = self.capture["raw"]["positions"][0]
        p["mktValue"] = 60000
        self.assertEqual(self.check()["mapping_status"], "IDENTITIES_MAPPED_REVIEW_REQUIRED")
        p["mktValue"] = 69000
        with self.assertRaisesRegex(ValueError, "conflicting market-value fields"):
            self.check()
        del p["mktValue"]
        del p["marketValue"]
        r = self.check()
        self.assertIn("IBKR_POSITION_MARKET_VALUE_MISSING", r["mapping_block_reasons"])

    def test_duplicate_ibkr_contract_fails(self):
        self.capture["raw"]["positions"].append(
            copy.deepcopy(self.capture["raw"]["positions"][0])
        )
        with self.assertRaisesRegex(ValueError, "Duplicate IBKR conid"):
            self.check()

    def test_duplicate_local_security_mapping_fails(self):
        new = copy.deepcopy(self.mapping["contracts"][0])
        new["conid"] = 303030
        self.mapping["contracts"].append(new)
        with self.assertRaisesRegex(ValueError, "same AtlasPM instrument"):
            self.check()

    def test_stale_snapshot_blocks(self):
        self.capture["captured_at_utc"] = "2020-01-01T00:00:00Z"
        self.assertIn("IBKR_CAPTURE_STALE", self.check()["mapping_block_reasons"])

    def test_future_snapshot_rejected(self):
        self.capture["captured_at_utc"] = (
            datetime.now(timezone.utc) + timedelta(minutes=5)
        ).isoformat()
        with self.assertRaisesRegex(ValueError, "future"):
            self.check()

    def test_foreign_currency_ledger_or_position_blocks(self):
        self.capture["raw"]["USD_ledger"]["EUR"] = {"settledcash": 2}
        self.assertIn("IBKR_ADDITIONAL_CURRENCY_LEDGERS_REQUIRE_FX",
                      self.check()["mapping_block_reasons"])
        del self.capture["raw"]["USD_ledger"]["EUR"]
        self.capture["raw"]["positions"][0]["currency"] = "EUR"
        self.assertIn("IBKR_POSITION_CURRENCY_UNSUPPORTED",
                      self.check()["mapping_block_reasons"])

    def test_explicit_option_legs_require_full_terms(self):
        self.capture["raw"]["positions"].append({
            "conid": 505050, "assetClass": "OPT", "currency": "USD",
            "position": -2, "mktValue": -1250,
        })
        opt = {
            "conid": 505050, "ibkr_asset_class": "OPT",
            "atlaspm_type": "option", "identifier": "QQQ-2027-01-15-C-350",
            "quantity_units": "CONTRACTS", "manually_reviewed": True,
            "units_confirmed": True,
        }
        self.mapping["contracts"].append(opt)
        with self.assertRaisesRegex(ValueError, "must include underlying"):
            self.check()
        opt.update({
            "underlying": "QQQ", "right": "call", "strike": 350,
            "expiration_date": "2027-01-15", "multiplier": 100,
        })
        r = self.check()
        self.assertEqual(r["mapping_status"], "IDENTITIES_MAPPED_REVIEW_REQUIRED")
        self.assertEqual(r["mapped_contract_count"], 3)
        self.assertFalse(r["ready_for_institutional_risk_gate"])
        self.assertEqual(r["positions"][-1]["signed_market_value_usd"], -1250)

    def test_account_id_mismatch_rejected(self):
        self.mapping["account_id"] = "DIFFERENT"
        with self.assertRaisesRegex(ValueError, "must match"):
            self.check()

    def test_console_is_redacted(self):
        r = self.check()
        summary = mapping_redacted_summary(r, "local_data/ibkr_mapping_check.json")
        data = json.dumps(summary)
        for private in ("DU1234567", "101010", "202020", "123950", "97900", "QQQ"):
            self.assertNotIn(private, data)
        self.assertEqual(summary["account_hint"], "****4567")
        self.assertFalse(summary["ready_for_institutional_risk_gate"])


if __name__ == "__main__":
    unittest.main()
