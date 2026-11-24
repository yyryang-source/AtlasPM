"""Single-command full optional-dependency release acceptance.

Runs offline fixture tests and source audit, not market downloads or broker calls.
Reports executed, passed and skipped counts independently. Never treats skips
as passes. This gate does not certify trading readiness.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import unittest
from time import perf_counter

from audit_source import audit


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--minimum-passed",type=int,default=732)
    parser.add_argument("--allow-skips",action="store_true")
    args=parser.parse_args()
    if args.minimum_passed<1:
        parser.error("minimum-passed must be positive")
    root=Path(__file__).resolve().parents[2]
    if Path.cwd().resolve()!=root:
        parser.error("Run from the checkout root")
    start=perf_counter()
    suite=unittest.defaultTestLoader.discover(str(root/"tests"))
    result=unittest.TextTestRunner(verbosity=1).run(suite)
    passed=result.testsRun-len(result.failures)-len(result.errors)-len(result.skipped)-len(result.expectedFailures)-len(result.unexpectedSuccesses)
    structure=audit(root)
    ok=(result.wasSuccessful() and passed>=args.minimum_passed and not structure["errors"]
        and (args.allow_skips or not result.skipped)
        and not result.expectedFailures and not result.unexpectedSuccesses)
    report=dict(status="PASS" if ok else "FAIL",tests_run=result.testsRun,
                passed=passed,failures=len(result.failures),errors=len(result.errors),
                skipped=len(result.skipped),expected_failures=len(result.expectedFailures),
                unexpected_successes=len(result.unexpectedSuccesses),
                minimum_passed=args.minimum_passed,elapsed_seconds=perf_counter()-start,
                source_audit_errors=structure["errors"],
                duplicate_function_candidates=structure["duplicate_function_candidates"],
                source_modules=structure["source_modules"],test_files=structure["test_files"],
                research_only=True,approved_for_model_training=False,live_execution_enabled=False,
                broker_order_submission_enabled=False)
    print(json.dumps(report,indent=2,allow_nan=False))
    return 0 if ok else 1


if __name__=="__main__":
    raise SystemExit(main())
