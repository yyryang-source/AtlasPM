"""Workstation setup/control and honest offline diagnostics regression tests."""
import argparse
from copy import deepcopy
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from atlaspm.commands import runtime_cli
from atlaspm.framework import configuration as config, system_runtime as runtime
from atlaspm.framework.orchestrator import set_control


class RuntimeSystemTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        old = Path.cwd()
        os.chdir(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(os.chdir, old)

    def settings(self, **runtime_settings):
        c = deepcopy(config.DEFAULTS)
        c["runtime"].update(cycles=1, **runtime_settings)
        return c

    def test_strict_config_rejects_coercion_unknown_secrets_and_escaping_paths(self):
        for data in ({"version": True}, {"secret": "do not print"},
                     {"runtime": {"cycles": True}}, {"sources": {"sec": 1}},
                     {"local_ai": {"enabled": True}},
                     {"paths": {"checkpoint": "outside.json"}},
                     {"providers": {"invented": True}}):
            with self.subTest(data=data), self.assertRaises(ValueError):
                config.validate(data)
        self.assertEqual(config.validate({}), config.DEFAULTS)

    def test_setup_initializes_existing_schemas_and_preserves_operator_pause(self):
        first = runtime.setup()
        set_control("takeover")
        second = runtime.setup()
        self.assertTrue(first["config_created"])
        self.assertFalse(second["config_created"])
        self.assertEqual(config.load(), config.DEFAULTS)
        self.assertTrue(runtime._paused())
        self.assertEqual(runtime._database_health(runtime.DB_PATH)["status"], "LOCAL_INTEGRITY_PASS")
        self.assertEqual(first["network_calls"], 0)

    def test_env_is_literal_redacted_and_scoped_with_os_precedence(self):
        Path(".env").write_text("FRED_API_KEY=file-secret\nUW_TOKEN='literal$()`'\n", encoding="utf-8")
        with patch.dict(os.environ, {"FRED_API_KEY": "vault-secret"}, clear=True):
            before = os.environ.get("UW_TOKEN")
            with config.environment(config.DEFAULTS, env_file=".env"):
                self.assertEqual(os.environ["FRED_API_KEY"], "vault-secret")
                self.assertEqual(os.environ["UW_TOKEN"], "literal$()`")
            self.assertEqual(os.environ.get("UW_TOKEN"), before)
        for text in ("PYTHONPATH=evil", "FRED_API_KEY=a\nFRED_API_KEY=b",
                     "ATLASPM_ALLOW_PAPER_ORDERS=1", "ATLASPM_ALLOW_EXTERNAL_AI=1"):
            Path(".env").write_text(text, encoding="utf-8")
            with self.assertRaises(ValueError) as caught:
                config.read_environment(".env")
            self.assertNotIn(text, str(caught.exception))

    def test_doctor_has_no_network_or_initialization_side_effect(self):
        with patch("atlaspm.data.api_gateway.Gateway.fetch", side_effect=AssertionError("network")):
            result = runtime.doctor(config.DEFAULTS)
        self.assertEqual(result["network_calls"], 0)
        self.assertEqual(result["market_database"]["status"], "NOT_INITIALIZED")
        self.assertFalse(Path("local_data").exists())
        self.assertTrue(all(not p["connection_verified"] for p in result["providers"]))
        self.assertEqual(next(p for p in result["providers"] if p["provider"] == "bloomberg")["mode"], "LICENSED_FILE_DELIVERY")

    def test_monitor_explicit_optins_before_any_task(self):
        c = self.settings()
        c["sources"]["sec"] = True
        with self.assertRaisesRegex(ValueError, "NETWORK_OPTIN"):
            runtime.monitor(c)
        c = self.settings()
        c["local_ai"].update(enabled=True, model="local:small", universe="local_data/universe.json")
        with self.assertRaisesRegex(ValueError, "LOCAL_AI_OPTIN"):
            runtime.monitor(c)
        self.assertFalse(Path("local_data").exists())

    def test_existing_source_routes_join_one_worker_with_bound_parameters(self):
        c = self.settings()
        c["sources"].update(sec=True, fed=True, documents=True, queue=False)
        c["research"].update(treasury=True, options=True, fred_series=["GDP"],
            news_providers=["bloomberg"], credit_requests=["issuer:cusip:123456789:AAA"],
            institutional_requests=["US:AAA=AAA"])
        with patch("atlaspm.research.disclosure_monitor.poll_once", return_value={"status": "DISCOVERED"}) as poll, \
             patch("atlaspm.research.disclosure_jobs.work_once", return_value={"status": "IDLE"}) as jobs, \
             patch("atlaspm.research.alpha_sources.treasury_once", return_value=1) as treasury, \
             patch("atlaspm.research.alpha_sources.options_once", return_value=1) as options, \
             patch("atlaspm.research.alpha_sources.fred_once", return_value=1) as fred, \
             patch("atlaspm.research.alpha_sources.news_once", return_value=1) as news, \
             patch("atlaspm.research.alpha_sources.credit_once", return_value=1) as credit, \
             patch("atlaspm.research.alpha_sources.institutional_once", return_value=1) as institutional:
            result = runtime.monitor(c, allow_network=True, resource_check=lambda _: {})
        self.assertEqual(result["status"], "COMPLETED")
        self.assertEqual(poll.call_count, 2)
        jobs.assert_called_once_with()
        treasury.assert_called_once_with()
        options.assert_called_once_with()
        fred.assert_called_once_with("GDP")
        self.assertEqual(news.call_args.args[:2][0], "bloomberg")
        credit.assert_called_once_with(scope="issuer", identifier_type="cusip", identifier="123456789", symbol="AAA")
        self.assertEqual(institutional.call_args.kwargs["subject"], "US:AAA")
        self.assertEqual(len(result["services"]), 9)

    def test_parent_provider_optout_closes_all_feed_routes_and_restores(self):
        c = self.settings()
        c["providers"].update(sec=False, sec_feed=True, federal_reserve=False)
        with patch.dict(os.environ, {}, clear=True):
            with config.environment(c):
                for name in ("SEC", "SEC_FEED", "SEC_DOCUMENT", "FEDERAL_RESERVE", "FED_DOCUMENT"):
                    self.assertEqual(os.environ["ATLASPM_ENABLE_" + name], "0")
            self.assertNotIn("ATLASPM_ENABLE_SEC", os.environ)

    def test_malformed_service_status_is_blocked_without_leaking_body(self):
        result = runtime.monitor(self.settings(), tasks={"fixture": (1, lambda: {"status": "unsafe secret response"})},
                                 resource_check=lambda _: {})
        self.assertEqual(result["status"], "DEGRADED")
        self.assertNotIn("unsafe secret", runtime.HEARTBEAT.read_text())

    @unittest.skipUnless(importlib.util.find_spec("pandas_market_calendars"), "optional calendar dependency")
    def test_real_queue_service_and_database_resource_measurement(self):
        runtime.setup()
        result = runtime.monitor(self.settings(max_memory_mib=65536))
        self.assertEqual(result["status"], "COMPLETED")
        self.assertIn("queue", result["services"])
        self.assertGreater(result["resources"]["monitored_database_bytes"], 0)
        self.assertGreater(result["resources"]["process_lifetime_peak_rss_bytes"], 0)

    def test_monitor_single_cycle_no_final_sleep_and_heartbeat(self):
        calls = []
        result = runtime.monitor(self.settings(), tasks={"fixture": (1, lambda: calls.append(1) or {"status": "OK"})},
                                 sleep=lambda _: self.fail("final sleep"), resource_check=lambda _: {})
        self.assertEqual(calls, [1])
        self.assertEqual(result["status"], "COMPLETED")
        self.assertFalse(result["broker_side_effects"])
        self.assertEqual(runtime.status()["heartbeat"]["run_id"], result["run_id"])
        self.assertTrue((runtime.ROOT / "runtime.jsonl").stat().st_size > 0)

    def test_failed_source_backoff_does_not_block_healthy_source(self):
        now = [0.]
        calls = {"bad": 0, "good": 0}
        def bad():
            calls["bad"] += 1
            raise ValueError("secret provider body must not appear")
        def good():
            calls["good"] += 1
            return {"status": "OK"}
        c = self.settings(interval_seconds=1)
        c["runtime"]["cycles"] = 3
        result = runtime.monitor(c, tasks={"bad": (1, bad), "good": (1, good)},
                                 clock=lambda: now[0], sleep=lambda seconds: now.__setitem__(0, now[0]+seconds),
                                 resource_check=lambda _: {})
        self.assertEqual(calls, {"bad": 1, "good": 3})
        self.assertEqual(result["status"], "DEGRADED")
        self.assertNotIn("secret provider", runtime.HEARTBEAT.read_text())
        self.assertEqual(result["exit_code"], 1)

    def test_pause_during_wait_prevents_next_service_cycle(self):
        c = self.settings()
        c["runtime"]["cycles"] = 3
        calls = []
        result = runtime.monitor(c, tasks={"fixture": (1, lambda: calls.append(1) or {"status": "OK"})},
                                 sleep=lambda _: set_control("stop"), resource_check=lambda _: {})
        self.assertEqual(result["status"], "PAUSED")
        self.assertEqual(calls, [1])

    def test_resource_failure_prevents_work_and_releases_worker(self):
        def exceed(_):
            raise ValueError("RUNTIME_MEMORY_BUDGET_EXCEEDED")
        result = runtime.monitor(self.settings(), tasks={"fixture": (1, lambda: self.fail("work started"))},
                                 resource_check=exceed)
        self.assertEqual(result["status"], "RESOURCE_OR_STATE_BLOCKED")
        with runtime.WorkerLease():
            self.assertTrue(runtime.LOCK.exists())

    def test_worker_lease_conflict_and_automatic_release_after_exit(self):
        with runtime.WorkerLease():
            with self.assertRaisesRegex(ValueError, "ALREADY_RUNNING"):
                with runtime.WorkerLease():
                    self.fail("second worker entered")
        with runtime.WorkerLease():
            self.assertTrue(runtime.LOCK.exists())

    def test_worker_process_death_releases_os_lock(self):
        source = str(Path(runtime.__file__).resolve().parents[2])
        env = {**os.environ, "PYTHONPATH": source}
        code = "from atlaspm.framework.system_runtime import WorkerLease; import os; x=WorkerLease(); x.__enter__(); os._exit(0)"
        child = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, timeout=15)
        self.assertEqual(child.returncode, 0, child.stderr.decode())
        with runtime.WorkerLease():
            self.assertTrue(runtime.LOCK.exists())

    def test_cli_routes_control_even_without_config_and_rejects_egress_flags(self):
        parser = argparse.ArgumentParser()
        runtime_cli.register_parsers(parser.add_subparsers(dest="command"))
        self.assertEqual(runtime_cli.run(parser.parse_args(["system", "stop"]))["status"], "STOP")
        with self.assertRaises(ValueError):
            runtime_cli.run(parser.parse_args(["system", "doctor", "--allow-network"]))
        result = runtime_cli.run(parser.parse_args(["system", "setup"]))
        self.assertEqual(result["status"], "LOCAL_SETUP_COMPLETE")
        with patch("atlaspm.research.disclosure_jobs.recover", return_value={"recovered": 0}) as recover:
            result = runtime_cli.run(parser.parse_args(["system", "recover"]))
        recover.assert_called_once_with()
        self.assertFalse(result["orders_replayed"])


if __name__ == "__main__":
    unittest.main()
