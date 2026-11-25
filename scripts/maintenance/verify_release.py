"""Single-command full optional-dependency release acceptance.

Runs offline fixture tests and source audit, not market downloads or broker calls.
Reports executed, passed and skipped counts independently. Never treats skips
as passes. This gate does not certify trading readiness.
"""
from __future__ import annotations

import argparse
from contextlib import redirect_stdout
import json
from pathlib import Path
import sys
import unittest
from time import perf_counter
from tempfile import TemporaryDirectory

from audit_source import audit


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--minimum-passed",type=int,default=1252)
    parser.add_argument("--allow-skips",action="store_true")
    parser.add_argument("--integrated",action="store_true",help="Also execute the isolated offline system fixture")
    args=parser.parse_args()
    if args.minimum_passed<1:
        parser.error("minimum-passed must be positive")
    root=Path(__file__).resolve().parents[2]
    if Path.cwd().resolve()!=root:
        parser.error("Run from the checkout root")
    start=perf_counter()
    # Preserve machine-readable stdout even when fixtures print diagnostics.
    with redirect_stdout(sys.stderr):
        suite=unittest.defaultTestLoader.discover(str(root/"tests"))
        result=unittest.TextTestRunner(verbosity=1).run(suite)
    passed=result.testsRun-len(result.failures)-len(result.errors)-len(result.skipped)-len(result.expectedFailures)-len(result.unexpectedSuccesses)
    structure=audit(root)
    from atlaspm.framework.architecture import inspect
    foundation=inspect()
    ok=(result.wasSuccessful() and passed>=args.minimum_passed and not structure["errors"]
        and (args.allow_skips or not result.skipped)
        and not result.expectedFailures and not result.unexpectedSuccesses
        and not structure["duplicate_function_candidates"] and not foundation["errors"])
    integrated=None
    integrated_metrics=None
    if args.integrated and ok:
        from atlaspm.framework.orchestrator import offline_release
        from atlaspm.data.market_db import private_path
        from benchmark_unified_data import process_peak_rss_bytes, process_io_counters, io_delta
        before_io=process_io_counters()
        run_root=private_path(Path("local_data/runs"))
        run_root.mkdir(parents=True,exist_ok=True)
        with TemporaryDirectory(prefix="release-acceptance-",dir=run_root) as folder:
            integrated=offline_release(checkpoint=Path(folder)/"accept.continuous.json")
        integrated_metrics={"wall_seconds":integrated["wall_seconds"],
                            "cpu_seconds":integrated["cpu_seconds"],
                            "process_lifetime_peak_rss_bytes":process_peak_rss_bytes(),
                            "process_io_delta":io_delta(before_io,process_io_counters()),
                            "production_performance_verified":False}
        ok=integrated["status"]=="OFFLINE_FIXTURE_PASS" and all(integrated["checks"].values())
    report=dict(status="PASS" if ok else "FAIL",tests_run=result.testsRun,
                passed=passed,failures=len(result.failures),errors=len(result.errors),
                skipped=len(result.skipped),
                skipped_tests=[{"test":case.id(),"reason":reason} for case,reason in result.skipped],
                expected_failures=len(result.expectedFailures),
                unexpected_successes=len(result.unexpectedSuccesses),
                minimum_passed=args.minimum_passed,elapsed_seconds=perf_counter()-start,
                source_audit_errors=structure["errors"],
                foundation_status=foundation["status"],foundation_errors=foundation["errors"],
                duplicate_function_candidates=structure["duplicate_function_candidates"],
                source_modules=structure["source_modules"],test_files=structure["test_files"],
                integrated_status=integrated["status"] if integrated else "NOT_RUN",
                integrated_checks=integrated["checks"] if integrated else {},
                integrated_wall_seconds=integrated["wall_seconds"] if integrated else None,
                integrated_metrics=integrated_metrics,
                research_only=True,approved_for_model_training=False,live_execution_enabled=False,
                broker_order_submission_enabled=False)
    print(json.dumps(report,indent=2,allow_nan=False))
    return 0 if ok else 1


if __name__=="__main__":
    raise SystemExit(main())
