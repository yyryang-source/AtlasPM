"""Offline foundation regression: CLI, durable lifecycle and scheduling."""
import contextlib
import io
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from atlaspm.cli import main,build_parser
from atlaspm.framework import architecture,lifecycle_journal as j
from atlaspm.framework.trading_lifecycle import Receipt,Step
from atlaspm.research import event_queue
from atlaspm.research.training_registry import digest


class FoundationTests(unittest.TestCase):
    def setUp(self):
        tmp=TemporaryDirectory()
        old=Path.cwd()
        os.chdir(tmp.name)
        self.addCleanup(tmp.cleanup)
        self.addCleanup(os.chdir,old)

    def test_architecture_cli_and_registry(self):
        out=io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(main(["architecture"]),0)
        report=json.loads(out.getvalue())
        self.assertEqual(report["status"],"OFFLINE_FOUNDATION_CONTRACT_PASS")
        self.assertEqual(len(report["cli_commands"]),62)
        self.assertEqual((report["workflow_stages"],report["lifecycle_phases"],report["lifecycle_steps"]),(8,7,21))
        self.assertFalse(report["live_execution_enabled"])
        self.assertFalse(Path("local_data").exists())
        parser=build_parser()
        parser._actions[-1].choices.pop("architecture")
        self.assertIn("CLI_REGISTRY_MISMATCH",architecture.inspect(parser)["errors"])

    def test_durable_journal_compare_and_swap_retry(self):
        start=j.create("review-1")
        receipt=Receipt(Step.MANDATE,"fixture:mandate","reviewed")
        first=j.append("review-1",receipt,expected_revision=start["revision_sha256"])
        self.assertEqual(first,j.inspect("review-1"))
        self.assertEqual(first,j.append("review-1",receipt,expected_revision=start["revision_sha256"]))
        with self.assertRaisesRegex(ValueError,"REVISION_CONFLICT"):
            j.append("review-1",Receipt(Step.INPUTS,"fixture:input","reviewed"),expected_revision=start["revision_sha256"])
        with self.assertRaisesRegex(ValueError,"out-of-order"):
            j.append("review-1",Receipt(Step.RISK,"fixture:risk","reviewed"),expected_revision=first["revision_sha256"])
        self.assertEqual(j.inspect("review-1"),first)
        self.assertFalse(first["trade_authorized"])
        with self.assertRaisesRegex(ValueError,"ALREADY_EXISTS"):
            j.create("review-1")

    def test_terminal_and_rehashed_invalid_transition(self):
        start=j.create("blocked")
        blocked=j.append("blocked",Receipt(Step.MANDATE,"missing","blocked","no mandate"),
                         expected_revision=start["revision_sha256"])
        with self.assertRaises(ValueError):
            j.append("blocked",Receipt(Step.INPUTS,"inputs","reviewed"),expected_revision=blocked["revision_sha256"])
        path=j.ROOT/"blocked.json"
        data=json.loads(path.read_text())
        data["body"]["receipts"][0]["step"]="risk"
        data["sha256"]=digest(data["body"])
        path.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError,"out-of-order"):
            j.inspect("blocked")

    def test_writer_conflict_and_failed_commit_preserve_revision(self):
        start=j.create("review")
        p=j.ROOT/"review.lock"
        p.touch()
        r=Receipt(Step.MANDATE,"fixture","reviewed")
        with self.assertRaises(FileExistsError):
            j.append("review",r,expected_revision=start["revision_sha256"])
        self.assertTrue(p.exists())
        p.unlink()
        with patch.object(j,"_atomic_write",side_effect=OSError("write failed")):
            with self.assertRaises(OSError):
                j.append("review",r,expected_revision=start["revision_sha256"])
        self.assertEqual(j.inspect("review"),start)
        self.assertFalse(p.exists())

    def test_path_and_cli_guards(self):
        for name in ("../escape","CON","com1","Upper","a/b",""):
            with self.subTest(name=name),self.assertRaises(ValueError):
                j.create(name)
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main(["lifecycle-review","create","--review-id","one","--step","risk"]),2)
        self.assertFalse(Path("local_data").exists())
        out=io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(main(["lifecycle-review","create","--review-id","one"]),0)
        first=json.loads(out.getvalue())
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(["lifecycle-review","append","--review-id","one","--step","mandate",
                                   "--outcome","reviewed","--evidence-ref","fixture",
                                   "--expected-revision",first["revision_sha256"]]),0)
        self.assertEqual(j.inspect("one")["receipt_count"],1)

    def test_queue_rejects_older_cutoff_even_same_tick(self):
        path=Path("local_data/queue.json")
        schedule={"tick_id":"same","phase":"market"}
        event_queue.drain(as_of="2020-01-02T10:00:30+00:00",path=path,schedule=schedule)
        old=path.read_bytes()
        with self.assertRaisesRegex(ValueError,"NONMONOTONIC_SYSTEM_TICK"):
            event_queue.drain(as_of="2020-01-02T10:00:10+00:00",path=path,schedule=schedule)
        self.assertEqual(path.read_bytes(),old)

    def test_queue_checks_all_visible_revision_ties(self):
        path=Path("local_data/queue.json")
        # Exercise revision detection before any specialist invocation.
        def event(stamp):
            return {"event":{"event_id":"same","received_at":stamp},"state":"pending"}
        items={"latest":event("2020-01-03T10:00:00+00:00"),
               "older1":event("2020-01-02T10:00:00+00:00"),
               "older2":event("2020-01-02T05:00:00-05:00")}
        path.parent.mkdir()
        path.write_text(json.dumps({"items":items,"sha256":digest(items)}))
        old=path.read_bytes()
        with self.assertRaisesRegex(ValueError,"AMBIGUOUS_EVENT_REVISION_RECEIPT"):
            event_queue.drain(as_of="2020-01-04T10:00:00+00:00",path=path)
        self.assertEqual(path.read_bytes(),old)
