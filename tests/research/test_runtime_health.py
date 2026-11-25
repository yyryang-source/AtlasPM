"""Operational health and recovery copy regression tests; no real broker."""
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import os
import unittest
from unittest.mock import patch

from atlaspm.framework import runtime_health as h,orchestrator
from atlaspm.research import event_queue as queue
from atlaspm.research.training_registry import digest


class RuntimeHealthTests(unittest.TestCase):
    def setUp(self):
        tmp=TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        prior=Path.cwd()
        os.chdir(tmp.name)
        self.addCleanup(os.chdir,prior)
        self.path=queue.PATH
        self.now="2025-01-02T12:00:00Z"

    def write(self,items):
        self.path.parent.mkdir(parents=True,exist_ok=True)
        self.path.write_text(json.dumps({"items":items,"sha256":digest(items)}),encoding="utf-8")

    def task(self,stamp,state="pending"):
        event={"event_id":stamp,"revision":"r1","received_at":stamp,"provider":"fixture"}
        return digest([event["event_id"],event["revision"]]),{"event":event,"state":state,
                       "attempts":3 if state=="failed" else 0,"report":None}

    def test_missing_and_corrupt_health_is_not_healthy(self):
        r=h.health(as_of=self.now)
        self.assertIn("QUEUE_NOT_INITIALIZED",r["alerts"])
        self.assertFalse(self.path.exists())
        self.path.parent.mkdir(parents=True)
        self.path.write_text("{}",encoding="utf-8")
        r=h.health(as_of=self.now)
        self.assertIn("QUEUE_INTEGRITY_OR_READ_FAILURE",r["alerts"])
        self.assertFalse(r["production_ready"])

    def test_health_distinguishes_pending_quarantine_and_future(self):
        self.write(dict([self.task("2025-01-02T11:00:00Z"),
                         self.task("2025-01-02T13:00:00Z"),
                         self.task("2025-01-02T10:00:00Z","failed"),
                         (queue.CONTROL_KEY,{"last_as_of":"2025-01-02T11:00:00Z"})]))
        self.path.with_suffix(".lock").touch()
        before=self.path.read_bytes()
        r=h.health(as_of=self.now)
        self.assertEqual(r["visible_pending"],1)
        self.assertEqual(r["future_received_pending"],1)
        self.assertEqual(r["queue_counts"]["failed"],1)
        self.assertIn("QUEUE_TICK_STALE",r["alerts"])
        self.assertIn("QUEUE_PENDING_AGE_EXCEEDED",r["alerts"])
        self.assertIn("QUEUE_WRITER_LOCK_PRESENT",r["alerts"])
        self.assertEqual(self.path.read_bytes(),before)

    def test_paused_backup_and_restore_are_isolated(self):
        orchestrator.set_control("stop")
        before=self.path.read_bytes()
        a=h.backup("copy")
        b=h.restore_isolated("copy","restore")
        self.assertEqual(a["sha256"],b["sha256"])
        self.assertEqual(h.verify_copy("restore")["sha256"],a["sha256"])
        self.assertEqual(self.path.read_bytes(),before)
        self.assertTrue(orchestrator.status()["control"]["stopped"])
        self.assertFalse(b["automatic_resume"])
        with self.assertRaises(FileExistsError):
            h.restore_isolated("copy","restore")

    def test_active_lock_corruption_and_invalid_names_fail_closed(self):
        orchestrator.set_control("resume")
        with self.assertRaisesRegex(ValueError,"PAUSED"):
            h.backup("active")
        orchestrator.set_control("stop")
        self.path.with_suffix(".lock").touch()
        with self.assertRaises(FileExistsError):
            h.backup("locked")
        self.path.with_suffix(".lock").unlink()
        h.backup("copy")
        p=h.ROOT/"copy.json"
        p.write_text("{}",encoding="utf-8")
        with self.assertRaises(ValueError):
            h.restore_isolated("copy","restore")
        self.assertFalse((h.ROOT/"restore.json").exists())
        for key in ("../x","CON","UPPER"):
            with self.assertRaises(ValueError):
                h.backup(key)

    def test_backup_requires_current_retention_rights(self):
        key,task=self.task("2025-01-02T11:00:00Z")
        task["event"]["provider"]="ibkr"
        self.write({key:task,queue.CONTROL_KEY:{"stopped":True}})
        with patch("atlaspm.data.provider_policy.local_rights",side_effect=ValueError("RIGHTS")) as rights:
            with self.assertRaisesRegex(ValueError,"RIGHTS"):
                h.backup("denied")
            rights.assert_called_once_with("ibkr",retention=True)
        self.assertFalse((h.ROOT/"denied.json").exists())


    def test_redirected_recovery_directory_rejected(self):
        orchestrator.set_control("stop")
        actual=h.private_path
        def redirected(p,*args,**kwargs):
            if p==h.ROOT:
                return actual(queue.PATH.parent)
            return actual(p,*args,**kwargs)
        with patch.object(h,"private_path",side_effect=redirected):
            with self.assertRaisesRegex(ValueError,"REDIRECTED_DIRECTORY"):
                h.backup("financial_event_queue")
