import unittest
from datetime import UTC, datetime, timedelta
from atlaspm.research.alpha_technical import metrics
from atlaspm.research.unified_alpha_ai import context, validate_output
from atlaspm.research.financial_events import _digest


class UnifiedAlphaTests(unittest.TestCase):
    def test_constant_bars(self):
        result = metrics([{"o":100,"h":101,"l":99,"c":100} for _ in range(50)])
        self.assertEqual(result["rsi_14"],50)
        self.assertEqual(result["atr_14"],2)
        self.assertEqual(result["macd_histogram"],0)
        self.assertIsNone(result["sma_200"])

    def test_invalid_bar(self):
        with self.assertRaises(ValueError):
            metrics([{"o":100,"h":90,"l":80,"c":100}])

    def row(self, provider, text, symbols, now):
        row = {"provider":provider,"text":text,"symbols":symbols,
               "received_at":(now-timedelta(seconds=10)).isoformat(),
               "expires_at":(now+timedelta(minutes=5)).isoformat()}
        row["evidence_id"] = _digest(row)
        return row

    def test_context_diversity_and_no_trade(self):
        now=datetime.now(UTC)
        rows=[self.row("sec",str(i),["AAPL"],now) for i in range(40)]
        rows.append(self.row("federal_reserve","macro",[],now))
        snapshot=context(rows,as_of=now.isoformat(),universe=["AAPL"])
        self.assertEqual(set(snapshot["providers"]),{"sec","federal_reserve"})
        self.assertLessEqual(len(snapshot["evidence"]),24)
        self.assertEqual(validate_output({"opportunities":[]},snapshot),[])

    def test_unrelated_symbol_rejected(self):
        now=datetime.now(UTC)
        rows=[self.row("sec","a",["AAPL"],now),self.row("sec","b",["MSFT"],now)]
        snapshot=context(rows,as_of=now.isoformat(),universe=["AAPL","MSFT"])
        item={"symbol":"AAPL","action":"LONG","evidence_ids":[r["evidence_id"] for r in rows],
              "reason":"example","valid_seconds":60}
        with self.assertRaises(ValueError):
            validate_output({"opportunities":[item]},snapshot)

    def test_digest_rejected(self):
        now=datetime.now(UTC)
        row=self.row("sec","original",["AAPL"],now)
        row["text"]="changed"
        with self.assertRaises(ValueError):
            context([row],as_of=now.isoformat(),universe=["AAPL"])
