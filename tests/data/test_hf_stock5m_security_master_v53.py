"""Offline V5.3 reviewed claim ledger regressions."""
import json
import os
from pathlib import Path
import tempfile
import unittest

from atlaspm.data import hf_stock5m_security_master_v53 as m
from atlaspm.data import hf_stock5m_evidence_v51 as ev


class SecurityMasterTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        old=Path.cwd()
        os.chdir(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(os.chdir,old)
        Path("local_data").mkdir()
        for family in ("A","B","C"):
            Path(f"local_data/{family}.txt").write_text("fixture "+family,encoding="utf-8")

    def claim(self,key,payload,family,known="2020-01-01",kind="IDENTITY",state="ASSERTED"):
        p=Path(f"local_data/{family}.txt")
        return {"record_id":key,"record_type":kind,"source_family":family,
                "source_url":f"https://example.org/{family}","evidence_path":str(p),
                "evidence_sha256":ev._sha(p.read_bytes()),"known_date":known,
                "reviewer":"fixture","state":state,"payload":payload}

    def pair(self,key,payload,known="2020-01-01",kind="IDENTITY",state="ASSERTED"):
        return [self.claim(key,payload,s,known,kind,state) for s in ("A","B")]

    def identity(self,security="ID1",symbol="ABC",start="2020-01-01",end=None):
        return {"security_id":security,"symbol":symbol,"exchange":"XNAS",
                "valid_from":start,"valid_to":end}

    def action(self,kind="SPLIT",value="2",effective="2020-03-01"):
        return {"security_id":"ID1","kind":kind,"effective_date":effective,
                "value":value,"currency":None if kind=="SPLIT" else "USD",
                "successor_id":None,"new_symbol":None}

    def ingest(self,rows):
        p=Path("local_data/claims.jsonl")
        p.write_text("\n".join(json.dumps(r) for r in rows),encoding="utf-8")
        return m.ingest(p)

    def test_empty_inventory_no_approval(self):
        r=m.inventory()
        self.assertEqual(r["claim_counts"],{})
        self.assertFalse(r["approved_for_model_training"])
        self.assertFalse(r["live_execution_enabled"])

    def test_pit_revision_and_same_day(self):
        rows=self.pair("listing1",self.identity())
        rows+=self.pair("listing1",self.identity(end="2020-06-01"),"2020-06-02")
        self.assertEqual(self.ingest(rows)["claims_added"],4)
        self.assertEqual(self.ingest(rows)["claims_reused"],4)
        self.assertEqual(m.snapshot("2020-01-01")["members"],[])
        self.assertEqual(len(m.snapshot("2020-06-02")["members"]),1)
        self.assertEqual(m.snapshot("2020-06-03")["members"],[])
        self.assertEqual(len(m.snapshot("2020-06-03","2020-05-31")["members"]),1)

    def test_conflict_retraction_and_same_source_tie(self):
        rows=self.pair("listing1",self.identity())
        rows.append(self.claim("listing1",self.identity(symbol="XYZ"),"A","2020-02-01"))
        self.ingest(rows)
        self.assertEqual(m.snapshot("2020-02-02")["unresolved"][0]["reason"],"CONFLICTING_VISIBLE_ASSERTIONS")
        self.ingest(self.pair("listing1",None,"2020-02-03",state="RETRACTED"))
        r=m.snapshot("2020-02-04")
        self.assertEqual(r["retracted_records"],1)
        self.assertEqual(r["members"],[])
        self.ingest(self.pair("listing2",self.identity(),"2020-03-01")+
                    [self.claim("listing2",self.identity(symbol="XYZ"),"A","2020-03-01")])
        self.assertEqual(m.snapshot("2020-03-02")["unresolved"][0]["reason"],"CONFLICTING_VISIBLE_ASSERTIONS")

    def test_ticker_reuse_and_overlap(self):
        self.ingest(self.pair("first",self.identity(end="2020-06-01"))+
                    self.pair("second",self.identity("ID2",start="2020-06-01")))
        self.assertEqual(m.snapshot("2020-06-02")["members"][0]["security_id"],"ID2")
        self.ingest(self.pair("third",self.identity("ID3",start="2020-05-01")))
        r=m.snapshot("2020-06-03")
        self.assertEqual(len(r["unresolved"]),3)
        self.assertEqual(r["members"],[])

    def test_hash_corruption_and_duplicate_source_bytes(self):
        Path("local_data/B.txt").write_bytes(Path("local_data/A.txt").read_bytes())
        self.ingest(self.pair("listing",self.identity()))
        self.assertEqual(m.snapshot("2020-01-02")["unresolved"][0]["reason"],"INSUFFICIENT_DECLARED_CORROBORATION")
        Path("local_data/B.txt").write_text("tampered",encoding="utf-8")
        self.assertEqual(m.snapshot("2020-01-02")["unresolved"][0]["reason"],"EVIDENCE_HASH_MISMATCH")

    def test_ledger_metadata_tampering_before_date_filter(self):
        from contextlib import closing
        self.ingest(self.pair("listing",self.identity()))
        with closing(m._db()) as cx,cx:
            cx.execute("UPDATE claims SET known_date='2030-01-01'")
        with self.assertRaisesRegex(ValueError,"ledger integrity failure"):
            m.snapshot("2020-01-02")

    def test_derived_report_cannot_be_primary_evidence(self):
        path=m.ROOT/"identity_v56"/"latest.json"
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text("derived report",encoding="utf-8")
        claim=self.claim("listing",self.identity(),"A")
        claim.update(evidence_path=str(path),evidence_sha256=ev._sha(path.read_bytes()))
        with self.assertRaisesRegex(ValueError,"not primary source evidence"):
            self.ingest([claim])
        self.assertEqual(m.inventory()["claim_counts"],{})

    def test_atomic_rejection(self):
        bad=self.claim("bad",self.identity(),"B")
        bad["evidence_sha256"]="0"*64
        with self.assertRaises(ValueError):
            self.ingest([self.claim("good",self.identity(),"A"),bad])
        self.assertEqual(m.inventory()["claim_counts"],{})

    def test_split_and_duplicate_economic_key(self):
        self.ingest(self.pair("listing",self.identity())+
                    self.pair("split",self.action(),known="2020-02-20",kind="ACTION"))
        r=m.adjustment("ID1","2020-02-01","2020-04-01",raw_unadjusted=True)
        self.assertEqual(r["price_multiplier"],"0.5")
        self.assertEqual(r["volume_multiplier"],"2")
        self.assertFalse(r["completeness_verified"])
        with self.assertRaises(ValueError):
            m.adjustment("ID1","2020-02-01","2020-04-01")
        self.ingest(self.pair("duplicate",self.action(value="3"),kind="ACTION"))
        self.assertEqual(len(m.snapshot("2020-04-01")["unresolved"]),2)
        with self.assertRaises(ValueError):
            m.adjustment("ID1","2020-02-01","2020-04-01",raw_unadjusted=True)

    def test_visible_returns_uses_actual_ledger(self):
        from atlaspm.data import hf_stock5m_visible_returns_v59 as returns
        self.ingest(self.pair("listing",self.identity())+
                    self.pair("split",self.action(),known="2020-02-20",kind="ACTION"))
        path=Path("local_data/marks.jsonl")
        path.write_text(json.dumps(dict(security_id="ID1",start_date="2020-02-01",
            end_date="2020-03-31",start_symbol="ABC",end_symbol="ABC",
            start_close="100",end_close="55")),encoding="utf-8")
        with self.assertRaises(ValueError):
            returns.run(path,"2020-04-01")
        result=returns.run(path,"2020-04-01",raw_unadjusted=True)
        self.assertEqual(result["rows_computed"],1)
        self.assertEqual(result["results"][0]["visible_action_return_no_reinvestment"],"0.1")
        self.assertFalse(result["approved_for_model_training"])
        self.ingest([self.claim("conflict",self.identity("ID2"),"A")])
        blocked=returns.run(path,"2020-04-01",raw_unadjusted=True)
        self.assertEqual(blocked["rows_computed"],0)
        self.assertEqual(blocked["results"][0]["reason"],"UNRESOLVED_VISIBLE_LEDGER_CLAIMS")

    def test_cash_exit_and_unknown_identity(self):
        self.ingest(self.pair("listing",self.identity(end="2020-03-01"))+
                    self.pair("exit",self.action("CASH_EXIT","12.50"),kind="ACTION"))
        r=m.snapshot("2020-04-01")
        self.assertEqual(r["actions"][0]["value"],"12.5")
        self.assertEqual(r["members"],[])
        with self.assertRaises(ValueError):
            m.adjustment("ID1","2020-02-01","2020-04-01",raw_unadjusted=True)
        x=self.action()
        x["security_id"]="UNKNOWN"
        self.ingest(self.pair("unknown",x,kind="ACTION"))
        self.assertEqual(m.snapshot("2020-04-01")["unresolved"][0]["reason"],"NO_CORROBORATED_EFFECTIVE_IDENTITY")
