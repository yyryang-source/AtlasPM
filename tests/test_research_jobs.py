from __future__ import annotations

import hashlib
import json
import tempfile
import time
import unittest
from datetime import date, timedelta
from pathlib import Path
from subprocess import Popen
from unittest.mock import patch

from atlaspm.research_jobs import job_file, list_jobs, queue_job, read_job
from atlaspm import research_jobs as jobs


class ResearchJobsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def wait_for(self, job_id):
        cutoff = time.monotonic() + 15
        while time.monotonic() < cutoff:
            job = read_job(self.root, job_id)
            if job["status"] in {"completed", "failed"}:
                return job
            time.sleep(.05)
        self.fail(f"Research task did not finish: {read_job(self.root, job_id)}")

    def queue(self, *args):
        def launch(*argv, **kwargs):
            process = Popen(*argv, **kwargs)
            # On Windows the worker can hold the log open until it exits.
            self.addCleanup(process.wait, timeout=15)
            return process

        with patch("atlaspm.research_jobs.subprocess.Popen", side_effect=launch):
            return queue_job(self.root, *args)

    def test_child_process_records_result_and_frozen_inputs(self):
        start = date(2023, 1, 2)
        data = "date,symbol,adjusted_close\n" + "".join(
            f"{start + timedelta(days=row)},XYZ,{100 + row * .1:.2f}\n" for row in range(253)
        )
        original = self.root / "source.csv"
        original.write_bytes(data.encode("utf-8"))
        job_id = self.queue(
            "stock-screen", "sample", {"prices": original},
            {"as-of": (start + timedelta(days=252)).isoformat(), "top": 1},
        )
        original.write_text("replaced", encoding="utf-8")
        job = self.wait_for(job_id)
        self.assertEqual(job["status"], "completed", job)
        self.assertEqual(job["inputs"]["prices"]["sha256"], hashlib.sha256(data.encode()).hexdigest())
        path = job_file(self.root, job_id, "report.json")
        report = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(report["selected"][0]["symbol"], "XYZ")
        self.assertEqual(path.with_name("prices.csv").read_bytes(), data.encode())
        self.assertEqual(jobs.verified_report(self.root,job_id),path)
        path.write_text("{}",encoding="utf-8")
        with self.assertRaisesRegex(ValueError,"REPORT_INTEGRITY"):
            jobs.verified_report(self.root,job_id)
        self.assertEqual(list_jobs(self.root, "stock-screen")[0]["id"], job_id)

    def test_child_process_failure_retains_log_without_report(self):
        job_id = self.queue(
            "stock-screen", "broken", {"prices": b"invalid csv"},
            {"as-of": "2023-12-29"},
        )
        job = self.wait_for(job_id)
        self.assertEqual(job["status"], "failed")
        self.assertFalse(job_file(self.root, job_id, "report.json").exists())
        self.assertIn("CSV", job_file(self.root, job_id, "run.log").read_text(encoding="utf-8"))


    def queued_fixture(self):
        with patch.object(jobs.subprocess,"Popen") as launch,patch.object(jobs,"Thread"):
            job=queue_job(self.root,"stock-screen","sample",{"prices":b"test"},
                          {"as-of":"2023-12-29"})
        self.assertEqual(read_job(self.root,job)["status"],"queued")
        return job,job_file(self.root,job,"job.json").parent

    def test_input_mutation_blocks_worker_before_cli(self):
        job,folder=self.queued_fixture()
        (folder/"prices.csv").write_bytes(b"tampered")
        with patch.object(jobs.subprocess,"run") as run:
            jobs._execute(self.root,job)
            run.assert_not_called()
        self.assertEqual(read_job(self.root,job)["status"],"failed")
        self.assertIn("INPUT_INTEGRITY",read_job(self.root,job)["error"])
        self.assertFalse((folder/"worker.lock").exists())

    def test_request_mutation_and_preexisting_report_block(self):
        job,folder=self.queued_fixture()
        state=read_job(self.root,job)
        state["options"]["top"]=10
        jobs._save_state(folder,state)
        with patch.object(jobs.subprocess,"run") as run:
            jobs._execute(self.root,job)
            run.assert_not_called()
        self.assertIn("REQUEST_INTEGRITY",read_job(self.root,job)["error"])
        job,folder=self.queued_fixture()
        (folder/"report.json").write_text("{}",encoding="utf-8")
        with patch.object(jobs.subprocess,"run") as run:
            jobs._execute(self.root,job)
            run.assert_not_called()
        self.assertIn("PREEXISTING_REPORT",read_job(self.root,job)["error"])

    def test_exclusive_worker_and_explicit_retry(self):
        job,folder=self.queued_fixture()
        lock=folder/"worker.lock"
        lock.touch()
        with patch.object(jobs.subprocess,"run") as run:
            jobs._execute(self.root,job)
            run.assert_not_called()
        self.assertEqual(read_job(self.root,job)["status"],"queued")
        state=read_job(self.root,job)
        state["status"]="failed"
        jobs._save_state(folder,state)
        with self.assertRaisesRegex(ValueError,"STOPPED_FAILED_WORKER"):
            jobs.retry_job(self.root,job)
        lock.unlink()
        with patch.object(jobs.subprocess,"Popen"),patch.object(jobs,"Thread"):
            retry=jobs.retry_job(self.root,job)
        self.assertNotEqual(retry,job)
        self.assertEqual(read_job(self.root,retry)["inputs"]["prices"]["sha256"],
                         state["inputs"]["prices"]["sha256"])
        self.assertEqual(read_job(self.root,job)["status"],"failed")

    def test_copy_budget_and_invalid_argument(self):
        with patch.object(jobs,"MAX_INPUT_BYTES",2),patch.object(jobs.subprocess,"Popen") as launch:
            job=queue_job(self.root,"stock-screen","sample",{"prices":b"123"},
                          {"as-of":"2023-12-29"})
            launch.assert_not_called()
        self.assertEqual(read_job(self.root,job)["status"],"failed")
        with self.assertRaisesRegex(ValueError,"OPTION"):
            queue_job(self.root,"stock-screen","sample",{},{"top":"--output"})

    def test_change_during_run_does_not_seal_result(self):
        job,folder=self.queued_fixture()
        def mutate(*args,**kwargs):
            (folder/"prices.csv").write_bytes(b"altered")
            (folder/"report.json").write_text("{}",encoding="utf-8")
            from types import SimpleNamespace
            return SimpleNamespace(returncode=0)
        with patch.object(jobs.subprocess,"run",side_effect=mutate):
            jobs._execute(self.root,job)
        self.assertEqual(read_job(self.root,job)["status"],"failed")
        with self.assertRaisesRegex(ValueError,"COMPLETED_EVIDENCE"):
            jobs.verified_report(self.root,job)


    def test_retry_copy_must_match_reviewed_snapshot(self):
        job,folder=self.queued_fixture()
        state=read_job(self.root,job)
        state["status"]="failed"
        jobs._save_state(folder,state)
        original=jobs._verified_inputs
        def change(*args):
            sources=original(*args)
            sources["prices"].write_bytes(b"new")
            return sources
        with patch.object(jobs,"_verified_inputs",side_effect=change),patch.object(jobs.subprocess,"Popen") as launch:
            retry=jobs.retry_job(self.root,job)
            launch.assert_not_called()
        self.assertEqual(read_job(self.root,retry)["status"],"failed")
