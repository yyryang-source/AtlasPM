"""Read-only offline OMS state machine, fill ledger, idempotency and recovery."""

import copy
import json
import unittest
from pathlib import Path

from atlaspm.execution.paper_oms import replay_paper_orders


ROOT = Path(__file__).resolve().parents[2] / "examples"


class PaperOMSTests(unittest.TestCase):
    def setUp(self):
        self.ledger = json.loads(
            (ROOT / "institutional_batch_ledger.json").read_text(encoding="utf-8")
        )
        self.plan = json.loads(
            (ROOT / "institutional_batch_plan.json").read_text(encoding="utf-8")
        )
        self.journal = json.loads(
            (ROOT / "paper_oms_events.json").read_text(encoding="utf-8")
        )

    def replay(self):
        return replay_paper_orders(self.ledger, self.plan, self.journal)

    def test_four_orders_partial_fills_and_no_live_execution(self):
        output = self.replay()
        self.assertEqual(output["decision"], "PAPER_REPLAY_COMPLETED")
        self.assertFalse(output["live_execution_enabled"])
        self.assertFalse(output["broker_preview_is_authenticated"])
        self.assertEqual(len(output["orders"]), 4)
        self.assertEqual(len(output["audit_events"]), 13)
        self.assertEqual([x["status"] for x in output["orders"]], ["FILLED"] * 4)
        self.assertEqual(output["orders"][0]["filled_quantity"], 10)
        self.assertEqual(output["orders"][0]["fees_paid"], 1)
        self.assertEqual(output["duplicate_events_ignored"], 0)
        account = output["account_replay"]
        self.assertAlmostEqual(account["opening_net_liquidation"], 123950)
        self.assertAlmostEqual(account["marked_net_liquidation"], 123945)
        self.assertAlmostEqual(account["expected_net_liquidation_change_from_fills_and_fees"], -5)
        self.assertAlmostEqual(account["net_liquidation_reconciliation_gap"], 0)
        self.assertAlmostEqual(account["settled_cash"], 3706)
        self.assertAlmostEqual(account["unsettled_receivable"], 3499)
        self.assertAlmostEqual(account["margin_debt"], 48000)
        self.assertEqual(account["broker_recognized_treasury_credit_report_only"], 90000)
        positions = {(x["type"], x["identifier"]): x for x in account["positions"]}
        self.assertEqual(positions["equity", "QQQ"]["shares"], 190)
        self.assertEqual(positions["treasury", "91282CDE0"]["face_value"], 110000)
        self.assertEqual(positions["option", "QQQ-2027-01-15-P-280"]["contracts"], 1)

    def test_settle_sale_only_after_fill_and_later_date(self):
        output = self.replay()
        original_equity = output["account_replay"]["marked_net_liquidation"]
        self.journal["events"].append({
            "sequence": 14, "event_id": "SYN-SETTLEMENT-1",
            "timestamp": "2026-11-23T14:00:00Z", "type": "SETTLE_SALE",
            "leg_id": "SELL-QQQ-10", "execution_id": "SYN-FILL-SELL-1",
        })
        self.journal["events"].append({
            "sequence": 15, "event_id": "SYN-SETTLEMENT-2",
            "timestamp": "2026-11-23T14:01:00Z", "type": "SETTLE_SALE",
            "leg_id": "SELL-QQQ-10", "execution_id": "SYN-FILL-SELL-2",
        })
        settled = self.replay()
        self.assertAlmostEqual(settled["account_replay"]["settled_cash"], 6705)
        self.assertAlmostEqual(settled["account_replay"]["unsettled_receivable"], 500)
        self.assertAlmostEqual(settled["account_replay"]["marked_net_liquidation"], original_equity)
        self.assertTrue(settled["fill_records"]["SYN-FILL-SELL-1"]["settled"])
        self.assertTrue(settled["fill_records"]["SYN-FILL-SELL-2"]["settled"])

    def test_unified_lifecycle_uses_existing_option_and_treasury_histories(self):
        from datetime import date,timedelta
        from hashlib import sha256
        from tempfile import TemporaryDirectory
        from atlaspm.execution.oms_controls import verify_audit_chain
        local=Path("local_data")
        local.mkdir(exist_ok=True)
        with TemporaryDirectory(dir=local) as temp:
            root=Path(temp)
            def source(name,text):
                path=root/name
                path.write_text(text,encoding="utf-8")
                return {"path":str(path),"sha256":sha256(path.read_bytes()).hexdigest()}
            opt="QQQ-2027-01-15-P-280"
            cusip="91282CDE0"
            option_contract="contract_id,underlying,right,strike,last_trade_at,expiration_at,multiplier,exercise_style,settlement_type,exercise_threshold_usd\n"
            self.journal["lifecycle_sources"]={
                "option":{
                    "contracts":source("options.csv",option_contract+opt+",QQQ,put,280,2027-01-15T20:59Z,2027-01-15T21:00Z,100,american,physical,1\n"),
                    "quotes":source("option_quotes.csv","contract_id,quote_at,bid,ask,bid_size,ask_size\n"),
                    "settlements":source("option_settlement.csv","contract_id,reference_at,available_at,cash_at,settlement_value\n"+
                        opt+",2027-01-15T21:00Z,2027-01-15T21:02Z,2027-01-19T14:30Z,270\n")},
                "treasury":{
                    "contracts":source("treasury.csv","cusip,kind,dated_on,issued_on,maturity,annual_coupon_rate,face_increment_usd\n"+
                        cusip+",note,2026-11-15,2026-11-16,2031-11-15,0.04,100\n"),
                    "quotes":source("treasury_quotes.csv","cusip,observed_on,clean_close_per_100\n"+
                        cusip+",2026-11-20,97.5\n"+cusip+",2027-05-14,97.5\n"+cusip+",2027-05-17,97.5\n")}}
            schedule=[]
            for year in range(2027,2032):
                for month in (5,11):
                    due=date(year,month,15)
                    paid=due
                    while paid.weekday()>=5:
                        paid+=timedelta(days=1)
                    schedule.append((due,paid))
            self.journal["lifecycle_sources"]["treasury"]["payments"]=source("payments.csv",
                "cusip,scheduled_on,paid_on\n"+"".join(f"{cusip},{due},{paid}\n" for due,paid in schedule))
            for i in (1,2):
                self.journal["events"].append({"sequence":13+i,"event_id":"SALE-"+str(i),
                    "timestamp":f"2026-11-23T14:0{i}:00Z","type":"SETTLE_SALE","leg_id":"SELL-QQQ-10",
                    "execution_id":"SYN-FILL-SELL-"+str(i)})
            def add(kind,stamp,identifier):
                row={"sequence":len(self.journal["events"])+1,"event_id":"LIFE-"+str(len(self.journal["events"])),
                     "timestamp":stamp,"type":kind,"identifier":identifier}
                self.journal["events"].append(row)
                return row
            add("OPTION_EXPIRY","2027-01-15T21:02:00Z",opt)
            expired=self.replay()
            self.assertEqual(expired["option_deliveries"][opt]["state"],"PENDING")
            self.assertEqual(expired["account_replay"]["settled_cash"],6705)
            self.assertEqual(expired["account_replay"]["unsettled_receivable"],28500)
            add("OPTION_DELIVERY","2027-01-19T14:30:00Z",opt)
            add("TREASURY_LIFECYCLE","2027-05-15T23:59:00Z",cusip)
            coupon=self.replay()["account_replay"]
            self.assertAlmostEqual(coupon["unsettled_receivable"],2700)
            self.assertAlmostEqual(coupon["settled_cash"],34705)
            add("TREASURY_LIFECYCLE","2027-05-17T23:59:00Z",cusip)
            final_due,final_paid=schedule[-1]
            add("TREASURY_LIFECYCLE",final_due.isoformat()+"T23:59:00Z",cusip)
            if final_paid!=final_due:
                final=add("TREASURY_LIFECYCLE",final_paid.isoformat()+"T23:59:00Z",cusip)
            else:
                final=self.journal["events"][-1]
            result=self.replay()
            account=result["account_replay"]
            self.assertEqual(result["decision"],"PAPER_REPLAY_COMPLETED",result["post_fill_risk_breaches"])
            self.assertAlmostEqual(account["settled_cash"],166705)
            self.assertAlmostEqual(account["unsettled_receivable"],500)
            self.assertEqual(account["broker_recognized_treasury_credit_report_only"],0)
            self.assertEqual(account["released_treasury_collateral_report_only"],90000)
            self.assertEqual([(p["identifier"],p["shares"]) for p in account["positions"]],[("QQQ",90)])
            self.assertAlmostEqual(account["net_liquidation_reconciliation_gap"],0)
            self.assertTrue(verify_audit_chain(result["audit_events"],result["audit_root_sha256"]))
            self.journal["events"].append(copy.deepcopy(final))
            self.assertEqual(self.replay()["account_replay"],account)
            path=Path(self.journal["lifecycle_sources"]["option"]["contracts"]["path"])
            path.write_text("tampered",encoding="utf-8")
            with self.assertRaisesRegex(ValueError,"SOURCE_HASH_MISMATCH"):
                self.replay()

    def test_same_event_id_duplicate_is_idempotent(self):
        self.journal["events"].append(copy.deepcopy(self.journal["events"][-1]))
        result = self.replay()
        self.assertEqual(result["duplicate_events_ignored"], 1)
        self.assertEqual(len(result["audit_events"]), 13)
        self.assertAlmostEqual(result["account_replay"]["marked_net_liquidation"], 123945)

    def test_retry_with_different_event_id_same_execution_is_idempotent(self):
        repeated = copy.deepcopy(self.journal["events"][-1])
        repeated.update({
            "event_id": "SYN-EVENT-014", "sequence": 14,
            "timestamp": "2026-11-20T21:14:00Z",
        })
        self.journal["events"].append(repeated)
        result = self.replay()
        self.assertEqual(result["duplicate_events_ignored"], 1)
        self.assertAlmostEqual(result["account_replay"]["margin_debt"], 48000)

    def test_conflicting_duplicate_execution_rejected(self):
        repeated = copy.deepcopy(self.journal["events"][-1])
        repeated.update({
            "event_id": "SYN-EVENT-014", "sequence": 14,
            "timestamp": "2026-11-20T21:14:00Z", "quantity": 1800,
        })
        self.journal["events"].append(repeated)
        with self.assertRaisesRegex(ValueError, "Conflicting duplicate execution_id"):
            self.replay()

    def test_cancel_after_partial_stock_fill_leaves_unfilled_quantity(self):
        self.journal["events"] = self.journal["events"][:3] + [
            {"sequence": 4, "event_id": "SYN-CANCEL-1",
             "timestamp": "2026-11-20T21:04:00Z", "type": "CANCEL_REQUEST",
             "leg_id": "SELL-QQQ-10"},
            {"sequence": 5, "event_id": "SYN-CANCEL-2",
             "timestamp": "2026-11-20T21:05:00Z", "type": "CANCELED",
             "leg_id": "SELL-QQQ-10"},
        ]
        result = self.replay()
        self.assertEqual(result["orders"][0]["status"], "CANCELED")
        self.assertEqual(result["orders"][0]["filled_quantity"], 4)
        self.assertAlmostEqual(result["account_replay"]["unsettled_receivable"], 1699.6)
        self.assertAlmostEqual(result["account_replay"]["marked_net_liquidation"], 123949.6)

    def test_unknown_broker_outcome_freezes_replay(self):
        self.journal["events"] = self.journal["events"][:1] + [{
            "sequence": 2, "event_id": "SYN-UNKNOWN",
            "timestamp": "2026-11-20T21:02:00Z",
            "type": "UNKNOWN_OUTCOME", "leg_id": "SELL-QQQ-10",
        }]
        result = self.replay()
        self.assertEqual(result["decision"], "HALT_RECONCILIATION")
        self.assertEqual(result["unresolved_orders"], ["SELL-QQQ-10"])
        self.journal["events"].append({
            "sequence": 3, "event_id": "SYN-ACK-LATE",
            "timestamp": "2026-11-20T21:03:00Z",
            "type": "ACK", "leg_id": "SELL-QQQ-10",
        })
        with self.assertRaisesRegex(ValueError, "freeze event replay"):
            self.replay()

    def test_invalid_overfill_and_worse_price_rejected(self):
        self.journal["events"][3]["quantity"] = 7
        with self.assertRaisesRegex(ValueError, "exceeds remaining"):
            self.replay()
        self.setUp()
        treasury_fill = next(
            e for e in self.journal["events"]
            if e["type"] == "FILL" and e["leg_id"] == "BUY-UST-10K"
        )
        treasury_fill["price"] = 98.0
        with self.assertRaisesRegex(ValueError, "worse than approved"):
            self.replay()

    def test_treasury_price_improvement_reconciles_without_extra_credit(self):
        treasury_fill = next(
            e for e in self.journal["events"]
            if e["type"] == "FILL" and e["leg_id"] == "BUY-UST-10K"
        )
        treasury_fill["price"] = 97.8
        result = self.replay()
        self.assertAlmostEqual(result["account_replay"]["marked_net_liquidation"], 123955)
        self.assertAlmostEqual(result["account_replay"]["net_liquidation_reconciliation_gap"], 0)
        self.assertEqual(
            result["account_replay"]["broker_recognized_treasury_credit_report_only"], 90000
        )

    def test_rejected_order_cannot_be_filled(self):
        self.journal["events"] = self.journal["events"][:2] + [{
            "sequence": 3, "event_id": "SYN-REJECT-1",
            "timestamp": "2026-11-20T21:03:00Z",
            "type": "REJECT", "leg_id": "SELL-QQQ-10",
        }]
        result = self.replay()
        self.assertEqual(result["orders"][0]["status"], "REJECTED")
        self.assertAlmostEqual(result["account_replay"]["marked_net_liquidation"], 123950)
        self.journal["events"].append({
            "sequence": 4, "event_id": "SYN-ILLEGAL-FILL",
            "timestamp": "2026-11-20T21:04:00Z",
            "type": "FILL", "leg_id": "SELL-QQQ-10",
            "execution_id": "SYN-FILL-SELL-X",
            "quantity": 1, "price": 300, "fee": 0.1,
        })
        with self.assertRaisesRegex(ValueError, "non-open"):
            self.replay()

    def test_unapproved_basket_never_replays(self):
        self.plan["basket_preview"]["accepted"] = False
        with self.assertRaisesRegex(ValueError, "Batch risk preflight is blocked"):
            self.replay()

    def test_wrong_batch_and_out_of_order_events_rejected(self):
        self.journal["batch_id"] = "DIFFERENT"
        with self.assertRaisesRegex(ValueError, "batch_id"):
            self.replay()
        self.journal["batch_id"] = "SYN-BASKET-0001"
        self.journal["events"][2]["sequence"] = 1
        with self.assertRaisesRegex(ValueError, "strictly increasing"):
            self.replay()

    def test_sale_settlement_on_same_day_rejected(self):
        self.journal["events"].append({
            "sequence": 14, "event_id": "SYN-SETTLE-TOO-EARLY",
            "timestamp": "2026-11-20T22:00:00Z",
            "type": "SETTLE_SALE", "leg_id": "SELL-QQQ-10",
            "execution_id": "SYN-FILL-SELL-1",
        })
        with self.assertRaisesRegex(ValueError, "cannot settle"):
            self.replay()


if __name__ == "__main__":
    unittest.main()
