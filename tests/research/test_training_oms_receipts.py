import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from importlib.util import find_spec
from unittest.mock import patch
import sqlite3

from atlaspm.execution import synthetic_oms_receipts as receipts
from atlaspm.engine.unified import SAFETY

EXAMPLE=Path(__file__).resolve().parents[2]/"examples/unified_portfolio_synthetic.json"


class ReceiptTests(unittest.TestCase):
    def setUp(self):
        self.tmp=TemporaryDirectory()
        old=Path.cwd()
        os.chdir(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(os.chdir,old)
        self.db=Path("local_data/paper/receipts.sqlite3")
        self.p={"mode":"synthetic","synthetic_fixture":True}
        self.report={**SAFETY,"decision":"BLOCK","block_reasons":["TEST_FIXTURE"]}

    def test_duplicate_request_and_conflict(self):
        with patch.object(receipts,"run_unified_portfolio",return_value=self.report) as fit:
            first=receipts.execute(self.db,"SYN:1",self.p)
            repeat=receipts.execute(self.db,"SYN:1",self.p)
            with self.assertRaisesRegex(ValueError,"REQUEST_ID_CONFLICT"):
                receipts.execute(self.db,"SYN:1",{**self.p,"different":True})
            fit.assert_called_once()
        self.assertFalse(first["reused"])
        self.assertTrue(repeat["reused"])
        self.assertEqual(first["report_sha256"],repeat["report_sha256"])
        self.assertEqual(receipts.inspect(self.db,"SYN:1"),repeat)

    def test_rollback_on_failed_simulation(self):
        with patch.object(receipts,"run_unified_portfolio",side_effect=RuntimeError("simulated interruption")):
            with self.assertRaises(RuntimeError):
                receipts.execute(self.db,"SYN:1",self.p)
        with self.assertRaisesRegex(ValueError,"RECEIPT_NOT_FOUND"):
            receipts.inspect(self.db,"SYN:1")
        with patch.object(receipts,"run_unified_portfolio",return_value=self.report):
            self.assertFalse(receipts.execute(self.db,"SYN:1",self.p)["reused"])

    def test_tampered_report_detected(self):
        with patch.object(receipts,"run_unified_portfolio",return_value=self.report):
            receipts.execute(self.db,"SYN:1",self.p)
        from contextlib import closing
        with closing(sqlite3.connect(self.db)) as cx,cx:
            cx.execute("UPDATE synthetic_oms_receipts SET report_json=?",
                       (json.dumps({**self.report,"block_reasons":["ALTERED"]}),))
        with self.assertRaisesRegex(ValueError,"INTEGRITY_FAILURE"):
            receipts.inspect(self.db,"SYN:1")

    def test_real_and_permission_override_refused(self):
        for p in ({**self.p,"mode":"real"},{**self.p,"live_execution_enabled":True}):
            with self.assertRaises(ValueError):
                receipts.execute(self.db,"SYN:1",p)
        self.assertFalse(self.db.exists())

    @unittest.skipUnless(find_spec("scipy"),"portfolio extra required")
    def test_actual_oms_partial_fill_duplicate_event(self):
        p=json.loads(EXAMPLE.read_text(encoding="utf-8"))
        p["paper"]["fill_fraction"]=.5
        p["paper"]["duplicate_last_event"]=True
        a=receipts.execute(self.db,"SYN:paper",p)
        b=receipts.execute(self.db,"SYN:paper",p)
        self.assertEqual(a["report"]["decision"],"SYNTHETIC_PAPER_CLOSED_LOOP_COMPLETE",a)
        self.assertTrue(all(a["report"]["reconciliation_checks"].values()))
        self.assertEqual(a["report"]["paper_oms"]["duplicate_events_ignored"],1)
        self.assertEqual(a["report_sha256"],b["report_sha256"])
        self.assertFalse(b["broker_side_effects"])
