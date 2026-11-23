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
