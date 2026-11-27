"""Crash, concurrency and operator-control regressions; no provider network."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import UTC, datetime, timedelta
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
import unittest
from unittest.mock import patch

from atlaspm.research import disclosure_jobs as jobs, event_queue, alpha_inbox
from atlaspm.research.alpha_scheduler import schedule
from atlaspm.research.financial_events import normalize, _digest


class DisclosureRecoveryTests(unittest.TestCase):
    def setUp(self):
        old = Path.cwd()
        temporary = TemporaryDirectory()
        os.chdir(temporary.name)
        self.addCleanup(temporary.cleanup)
        self.addCleanup(os.chdir, old)
        self.path = Path("local_data/jobs.sqlite3")
        self.inbox = Path("local_data/inbox.sqlite3")
        self.queue = Path("local_data/events.json")
        self.stamp = datetime.now(UTC).isoformat()
        self.receipt = {"text": "A reported event", "retrieved_at": self.stamp}

    def discovery(self, number=1):
        accession = f"0000320193-24-{number:06d}"
        metadata = {"accession": accession, "archive_cik": "0000320193"}
        event = normalize({"provider": "sec", "source_id": accession, "kind": "sec_disclosure",
            "subjects": [], "published_at": None, "first_available_at": None, "effective_at": None,
            "evidence_sha256": _digest(metadata), "delivery": "poll", "expiry_seconds": 86400},
            received_at=self.stamp)
        return {"events": [event], "metadata": [metadata]}

    def state(self):
        with closing(jobs._connect(self.path)) as db:
            return db.execute("SELECT state,attempts,lease_owner FROM jobs ORDER BY id").fetchall()

    def work(self):
        return jobs.work_once(path=self.path, queue_path=self.queue, inbox_path=self.inbox)

    def test_duplicate_discovery_and_successful_handoff(self):
        report = self.discovery()
        self.assertEqual(jobs.register("sec", report, path=self.path), 1)
        self.assertEqual(jobs.register("sec", report, path=self.path), 0)
        with patch.object(jobs, "capture", return_value=self.receipt):
            self.assertEqual(self.work()["status"], "CAPTURED")
        self.assertEqual(self.work()["status"], "IDLE")
        self.assertEqual(self.state(), [("captured", 1, None)])
        with closing(alpha_inbox.connect(self.inbox)) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM evidence").fetchone()[0], 1)

    def test_crash_after_inbox_commit_retries_without_duplicate(self):
        jobs.register("sec", self.discovery(), path=self.path)
        def crash(**kwargs):
            alpha_inbox.ingest(**kwargs)
            raise KeyboardInterrupt("simulated process termination")
        with patch.object(jobs, "capture", return_value=self.receipt), patch.object(jobs, "ingest", side_effect=crash):
            with self.assertRaises(KeyboardInterrupt):
                self.work()
        self.assertEqual(self.state()[0][:2], ("working", 1))
        self.assertEqual(jobs.recover(path=self.path)["expired_requeued"], 0)
        with closing(jobs._connect(self.path)) as db, db:
            db.execute("UPDATE jobs SET lease_expires=?", ((datetime.now(UTC)-timedelta(seconds=1)).isoformat(),))
        with patch.object(jobs, "capture", return_value=self.receipt):
            self.assertEqual(self.work()["status"], "CAPTURED")
        self.assertEqual(self.state(), [("captured", 2, None)])
        with closing(alpha_inbox.connect(self.inbox)) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM evidence").fetchone()[0], 1)

    def test_active_worker_cannot_be_recovered_or_duplicated(self):
        jobs.register("sec", self.discovery(), path=self.path)
        entered, release = Event(), Event()
        def capture(*args):
            entered.set()
            if not release.wait(5):
                raise TimeoutError("test worker not released")
            return self.receipt
        with patch.object(jobs, "capture", side_effect=capture), ThreadPoolExecutor(max_workers=2) as pool:
            pending = pool.submit(self.work)
            self.assertTrue(entered.wait(5))
            try:
                self.assertEqual(jobs.recover(path=self.path)["expired_requeued"], 0)
                self.assertEqual(self.work()["status"], "IDLE")
            finally:
                release.set()
            self.assertEqual(pending.result(timeout=5)["status"], "CAPTURED")

    def test_fenced_owner_does_not_publish_after_lease_stolen(self):
        jobs.register("sec", self.discovery(), path=self.path)
        def lost_claim(*args):
            with closing(jobs._connect(self.path)) as db, db:
                db.execute("UPDATE jobs SET lease_owner='replacement'")
            return self.receipt
        with patch.object(jobs, "capture", side_effect=lost_claim), patch.object(jobs, "ingest") as ingest:
            self.assertEqual(self.work()["status"], "LEASE_LOST")
            ingest.assert_not_called()
        self.assertEqual(self.state()[0][2], "replacement")

    def test_pause_after_download_does_not_ingest(self):
        jobs.register("sec", self.discovery(), path=self.path)
        with patch.object(event_queue, "_read_state", side_effect=[{}, {event_queue.CONTROL_KEY: {"stopped": True}}]), \
                patch.object(jobs, "capture", return_value=self.receipt), patch.object(jobs, "ingest") as ingest:
            self.assertEqual(self.work()["status"], "PAUSED")
            ingest.assert_not_called()
        self.assertEqual(self.state(), [("pending", 1, None)])

    def test_failure_budget_and_sanitized_diagnostics(self):
        jobs.register("sec", self.discovery(), path=self.path)
        with patch.object(jobs, "capture", side_effect=OSError("secret-provider-response")):
            for _ in range(jobs.MAX_ATTEMPTS):
                result = self.work()
                self.assertEqual(result["status"], "FAILED")
                self.assertNotIn("secret", str(result))
                jobs.recover(path=self.path)
        self.assertEqual(jobs.recover(path=self.path)["attempts_exhausted"], 1)
        self.assertEqual(self.work()["status"], "IDLE")
        with closing(jobs._connect(self.path)) as db:
            self.assertEqual(db.execute("SELECT error FROM jobs").fetchone()[0], "OSError")

    def test_capacity_rolls_back_batch_and_rejects_unbound_metadata(self):
        first = self.discovery()
        jobs.register("sec", first, path=self.path)
        with patch.object(jobs, "MAX_JOBS", 1), self.assertRaisesRegex(ValueError, "CAPACITY"):
            jobs.register("sec", self.discovery(2), path=self.path)
        self.assertEqual(len(self.state()), 1)
        first["metadata"][0]["archive_cik"] = "0000000001"
        with self.assertRaisesRegex(ValueError, "digest mismatch"):
            jobs.register("sec", first, path=self.path)
        self.assertEqual(len(self.state()), 1)

    def test_legacy_working_rows_are_not_reset(self):
        jobs.register("sec", self.discovery(), path=self.path)
        with closing(jobs._connect(self.path)) as db, db:
            db.execute("UPDATE jobs SET state='working'")
        report = jobs.recover(path=self.path)
        self.assertEqual(report["status"], "RECOVERY_REVIEW_REQUIRED")
        self.assertEqual(report["legacy_working_blocked"], 1)
        self.assertEqual(self.work()["status"], "IDLE")


class SchedulerControlTests(unittest.TestCase):
    @patch.object(event_queue, "_read_state", return_value={})
    def test_no_sleep_after_final_cycle(self, _):
        with patch("atlaspm.research.alpha_scheduler.time.sleep") as sleeper:
            reports = list(schedule({"slow": (86400, lambda: 1)}, cycles=1, sleep=sleeper))
            sleeper.assert_not_called()
        self.assertEqual(reports[0]["status"], "OK")

    def test_stop_interrupts_backoff_before_next_source(self):
        elapsed, control, calls = [0.], {}, []
        def sleep(seconds):
            self.assertLessEqual(seconds, 1)
            elapsed[0] += seconds
            control[event_queue.CONTROL_KEY] = {"manual_takeover": True}
        def fail():
            calls.append("failed")
            raise OSError("provider failure")
        with patch.object(event_queue, "_read_state", side_effect=lambda _: control):
            reports = list(schedule({"source": (86400, fail)}, cycles=3,
                                    clock=lambda: elapsed[0], sleep=sleep))
        self.assertEqual([r["status"] for r in reports], ["BACKOFF", "PAUSED"])
        self.assertEqual(calls, ["failed"])
        self.assertEqual(elapsed[0], 1)

    def test_stop_between_sources_prevents_next_call(self):
        control = {}
        def stop():
            control[event_queue.CONTROL_KEY] = {"stopped": True}
            return 0
        with patch.object(event_queue, "_read_state", side_effect=lambda _: control):
            reports = list(schedule({"first": (5, stop), "second": (5, lambda: self.fail("network"))}, cycles=1))
        self.assertEqual([r["status"] for r in reports], ["OK", "PAUSED"])
