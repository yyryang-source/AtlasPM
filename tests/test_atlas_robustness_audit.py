"""Atlas paired research audit: identical experiment, safe ledger, no hindsight claim."""

from __future__ import annotations

import contextlib
import io
import json
import math
import tempfile
import unittest
from copy import deepcopy
from datetime import date, timedelta
from pathlib import Path

from atlaspm.atlas_robustness_audit import audit_atlas_risknet
from atlaspm.cli import main

ROOT = Path(__file__).resolve().parents[1]


def mocked_report(*,risknet: bool) -> dict:
    """Synthetic only: authored controlled report with matched benchmark curves."""
    n=100
    dates=[(date(2022,1,1)+timedelta(days=i)).isoformat() for i in range(n)]
    def build(epsilon: float) -> tuple[list[dict],dict]:
        equity=1.0
        peak=1.0
        rows=[]
        for i,day in enumerate(dates):
            daily=.0001+epsilon+.005*math.sin(i*.21)
            equity *= 1+daily
            peak=max(peak,equity)
            rows.append({
                "date":day,
                "equity_index":equity,
                "drawdown":equity/peak-1,
                "cash_equity_index_units":.2*equity,
                "gross_holdings_equity_index_units":.8*equity,
            })
        return rows,{"total_return":equity-1}
    ours,op=build(.0002 if risknet else .0001)
    naive,bp=build(.00005)
    return {
        "status":"OFFLINE_TRAINED_RESEARCH_ONLY",
        "live_execution_enabled":False,
        "model_validated_for_live_trading":False,
        "ready_for_institutional_risk_gate":False,
        "IBKR_financing_debt_separately_verified":False,
        "model_family":"atlaspm_original_risknet_v2_single_risk_head" if risknet
            else "atlaspm_original_regimenet_v1_dual_head",
        "architecture_choice":"risknet" if risknet else "dual",
        "return_prediction_head_trained":not risknet,
        "data":{
            "symbols":["AAA","BBB"],
            "first_price_date":"2021-01-01",
            "last_price_date":"2022-12-31",
            "first_execution_date":"2021-12-31",
            "held_out_trading_sessions":n,
            "minimum_training_samples":756,
            "actual_train_window_dates":504,
            "sample_stride":2,
            "training_epochs":40,
            "feature_days":20,
            "forecast_days":5,
            "rebalance_days":21,
        },
        "policy":{
            "return_head_strength":0,
            "target_annual_volatility":.18,
            "maximum_name_weight":.70,
            "maximum_gross_exposure":1.0,
            "uniform_stress_decline":.3,
            "maximum_uniform_stress_loss":.25,
            "maximum_daily_tail_loss":.04,
            "account_drawdown_trigger":.10,
            "drawdown_exposure_cap":.25,
            "fee_bps":5,
            "slippage_bps":5,
            "no_trade_band_l1_account_weight":.05,
        },
        "performance":{
            "atlas_native":op,
            "naive_trailing_vol_risk":bp,
        },
        "equity_curves":{
            "atlas_native":ours,
            "naive_trailing_vol_risk":naive,
        },
    }


def write_json(base: Path, name: str, obj: dict) -> Path:
    path=base/name
    path.write_text(json.dumps(obj),encoding="utf-8")
    return path


class AuditMethodologyTests(unittest.TestCase):
    def test_paired_years_crises_and_block_intervals_are_deterministic(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t)
            a=write_json(root,"risk.json",mocked_report(risknet=True))
            b=write_json(root,"dual.json",mocked_report(risknet=False))
            first=audit_atlas_risknet(a,b,iterations=100,block_days=21)
            second=audit_atlas_risknet(a,b,iterations=100,block_days=21)
        self.assertEqual(first,second)
        self.assertTrue(first["matched_traditional_benchmark_between_native_reports"])
        self.assertEqual(first["paired_trading_sessions"],100)
        self.assertIn("2022",first["diagnostics"]["risknet"]["calendar_years"])
        self.assertEqual(first["diagnostics"]["risknet"]
                         ["predefined_market_windows"]["dotcom_2000_2002"]
                         ["sessions"],0)
        self.assertEqual(first["diagnostics"]["risknet"]
                         ["predefined_market_windows"]["stock_bond_2022"]
                         ["sessions"],100)
        for key in ("risknet_minus_traditional","risknet_minus_dual"):
            boot=first["paired_uncertainty"][key]
            self.assertEqual(boot["resamples"],100)
            self.assertEqual(len(boot["bootstrap_95pct_interval_sharpe_difference"]),2)
            self.assertEqual(
                len(boot["bootstrap_95pct_interval_annualized_arithmetic_spread"]),2
            )
            self.assertIn("NOT a valid untouched final test",boot["warning"])
        self.assertFalse(first["live_execution_enabled"])
        self.assertFalse(first["model_validated_for_live_trading"])
        self.assertFalse(first["ready_for_institutional_risk_gate"])

    def test_fail_closed_on_mismatched_cost_dates_or_baseline(self):
        risk=mocked_report(risknet=True)
        dual=mocked_report(risknet=False)
        mutations=[
            lambda r: r["data"].update({"symbols":["A","C"]}),
            lambda r: r["policy"].update({"fee_bps":8}),
            lambda r: r["policy"].update({"no_trade_band_l1_account_weight":0}),
            lambda r: r["equity_curves"]["atlas_native"][20].update(
                {"date":"2020-01-01"}
            ),
            lambda r: r["equity_curves"]["naive_trailing_vol_risk"][4].update(
                {"equity_index":1.05}
            ),
            lambda r: r["equity_curves"]["atlas_native"][0].update(
                {"cash_equity_index_units":-1}
            ),
            lambda r: r.update({"live_execution_enabled":True}),
            lambda r: r.update({"architecture_choice":"risknet"}),
            lambda r: r["policy"].update({"return_head_strength":1}),
        ]
        with tempfile.TemporaryDirectory() as t:
            root=Path(t)
            a=write_json(root,"r.json",risk)
            for index,mutate in enumerate(mutations):
                altered=deepcopy(dual)
                mutate(altered)
                b=write_json(root,f"d{index}.json",altered)
                with self.subTest(index=index),self.assertRaises(ValueError):
                    audit_atlas_risknet(a,b,iterations=100)

    def test_bootstrap_validation_and_corrupted_data(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t)
            a=write_json(root,"risk.json",mocked_report(risknet=True))
            b=write_json(root,"dual.json",mocked_report(risknet=False))
            for bad in ({"iterations":99},{"iterations":5001},
                        {"block_days":4},{"block_days":253}):
                with self.subTest(bad=bad),self.assertRaises(ValueError):
                    audit_atlas_risknet(a,b,**bad)
            a.write_text("NOT JSON",encoding="utf-8")
            with self.assertRaises(ValueError):
                audit_atlas_risknet(a,b,iterations=100)


class AuditCLITests(unittest.TestCase):
    def test_command_reads_private_reports_and_only_writes_private_summary(self):
        private=ROOT/"local_data"
        private.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=private) as d:
            root=Path(d)
            risk=write_json(root,"risk.json",mocked_report(risknet=True))
            dual=write_json(root,"dual.json",mocked_report(risknet=False))
            outfile=root/"audit.json"
            buf=io.StringIO()
            with contextlib.redirect_stdout(buf):
                code=main([
                    "atlas-native-audit",
                    "--risknet-report",str(risk),
                    "--dual-report",str(dual),
                    "--bootstrap-reps","100",
                    "--block-days","21",
                    "--output",str(outfile),
                ])
            self.assertEqual(code,0)
            summary=json.loads(buf.getvalue())
            report=json.loads(outfile.read_text(encoding="utf-8"))
            self.assertEqual(summary["paired_trading_sessions"],100)
            self.assertFalse(summary["live_execution_enabled"])
            self.assertIn("2022",report["diagnostics"]["traditional"]["calendar_years"])
            self.assertNotIn(str(private.resolve()),buf.getvalue())
            self.assertNotIn("AAA",buf.getvalue())

    def test_public_writes_and_same_input_rejected(self):
        private=ROOT/"local_data"
        private.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=private) as d:
            base=Path(d)
            risk=write_json(base,"risk.json",mocked_report(risknet=True))
            dual=write_json(base,"dual.json",mocked_report(risknet=False))
            public=ROOT/"examples"/"NEVER_WRITE_AUDIT.json"
            with contextlib.redirect_stderr(io.StringIO()):
                code=main([
                    "atlas-native-audit",
                    "--risknet-report",str(risk),
                    "--dual-report",str(dual),
                    "--output",str(public),
                ])
            self.assertEqual(code,2)
            self.assertFalse(public.exists())
            with contextlib.redirect_stderr(io.StringIO()):
                code=main([
                    "atlas-native-audit",
                    "--risknet-report",str(risk),
                    "--dual-report",str(risk),
                    "--output",str(base/"never.json"),
                ])
            self.assertEqual(code,2)
            self.assertFalse((base/"never.json").exists())


if __name__=="__main__":
    unittest.main()
