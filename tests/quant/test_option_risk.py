"""European option risk diagnostics with actual normalized local quote fixtures."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from atlaspm.strategies.options import option_risk as risk
from atlaspm.data.option_history import OptionHistory


class OptionRiskTests(unittest.TestCase):
    def test_known_price_parity_and_greeks(self):
        call=risk.european("call",100,100,1,.05,0,.2)
        put=risk.european("put",100,100,1,.05,0,.2)
        self.assertAlmostEqual(call["price_per_share"],10.450583572,places=7)
        self.assertAlmostEqual(put["price_per_share"],5.573526022,places=7)
        self.assertAlmostEqual(call["delta"]-put["delta"],1.)
        self.assertAlmostEqual(call["gamma"],put["gamma"])
        up=risk.european("call",100,100,1,.05,0,.20001)["price_per_share"]
        down=risk.european("call",100,100,1,.05,0,.19999)["price_per_share"]
        self.assertAlmostEqual(call["vega_per_vol_point"],(up-down)/.00002/100,places=6)

    def test_iv_roundtrip_and_no_boundary_extrapolation(self):
        for right in ("call","put"):
            p=risk.european(right,100,110,.5,.03,.01,.4)["price_per_share"]
            self.assertAlmostEqual(risk.implied_volatility(right,p,100,110,.5,.03,.01),.4)
        self.assertIsNone(risk.implied_volatility("call",150,100,100,1,.05,0))
        self.assertIsNone(risk.implied_volatility("put",0,100,100,1,.05,0))

    def history(self,root):
        def write(name,body):
            p=Path(root)/name
            p.write_text(body,encoding="utf-8")
            return p
        terms=write("terms.csv","contract_id,underlying,right,strike,last_trade_at,expiration_at,multiplier,exercise_style,settlement_type,exercise_threshold_usd\n"
            "C,ABC,call,100,2025-01-02T20:59Z,2025-01-02T21:00Z,100,european,cash,1\n"
            "A,ABC,put,100,2025-01-02T20:59Z,2025-01-02T21:00Z,100,american,physical,1\n"
            "M,ABC,call,100,2025-01-02T20:59Z,2025-01-02T21:00Z,100,european,cash,1\n")
        quotes=write("quotes.csv","contract_id,quote_at,bid,ask,bid_size,ask_size\n"
            "C,2024-01-02T20:59Z,10,11,5,5\nC,2024-01-02T21:01Z,15,16,5,5\n")
        settlement=write("settlement.csv","contract_id,reference_at,available_at,cash_at,settlement_value\n")
        return OptionHistory(terms,quotes,settlement)

    def test_quote_cutoff_and_unsupported_contracts(self):
        with TemporaryDirectory() as folder:
            h=self.history(folder)
            q=risk.review_chain(h,["C","A","M"],as_of="2024-01-02T21:00Z",
                 spot=100,spot_available_at="2024-01-02T21:00Z",rate=.05,dividend=0)
        rows={r["contract_id"]:r for r in q["contracts"]}
        self.assertEqual(rows["C"]["status"],"EUROPEAN_MODEL_DIAGNOSTIC")
        self.assertEqual(rows["C"]["ask"],11)
        self.assertEqual(rows["A"]["status"],"AMERICAN_STYLE_NOT_MODELED")
        self.assertEqual(rows["M"]["status"],"FRESH_QUOTE_REQUIRED")
        self.assertAlmostEqual(rows["C"]["greeks_per_contract"]["delta"],
                               100*rows["C"]["greeks_per_share"]["delta"])
        self.assertFalse(q["approved_for_model_training"])
        self.assertFalse(q["all_contracts_modeled"])

    def test_stale_and_future_spot_rejected(self):
        with TemporaryDirectory() as folder:
            h=self.history(folder)
            for stamp in ("2024-01-02T21:01Z","2024-01-02T20:00Z"):
                with self.assertRaisesRegex(ValueError,"SPOT_LOOKAHEAD_OR_STALE"):
                    risk.review_chain(h,["C"],as_of="2024-01-02T21:00Z",
                        spot=100,spot_available_at=stamp,rate=.05,dividend=0)

    def test_reject_invalid_model_inputs(self):
        for args in (("other",100,100,1,.05,0,.2),("call",100,100,0,.05,0,.2),
                     ("call",100,100,1,.05,0,6),("call",float("nan"),100,1,.05,0,.2)):
            with self.assertRaises(ValueError):
                risk.european(*args)
