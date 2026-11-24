"""Windows-safe entry point for the isolated synthetic portfolio engine."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from atlaspm.engine.unified import run_unified_portfolio


def register_parser(sub: Any) -> None:
    parser = sub.add_parser("portfolio-engine", help="Unified research-only synthetic portfolio loop")
    parser.add_argument("--file", type=Path, required=True, help="Synthetic input JSON")
    parser.add_argument("--output", type=Path, help="Optional new local report JSON; existing files are never overwritten")


def run(args: argparse.Namespace) -> dict[str, Any]:
    payload = json.loads(args.file.read_text(encoding="utf-8"))
    result = run_unified_portfolio(payload)
    if args.output is not None:
        output = args.output
        if output.exists():
            raise ValueError("REPORT_OUTPUT_ALREADY_EXISTS: refusing overwrite")
        # Never write reports into a market data lake or into the repository's
        # tracked examples/docs. Caller explicitly opts into a local JSON path.
        root = (Path.cwd() / "local_data" / "portfolio_engine").resolve()
        candidate = output.resolve()
        if not candidate.is_relative_to(root) or candidate.suffix.lower() != ".json":
            raise ValueError("REPORT_PATH_MUST_BE_UNDER_LOCAL_DATA_PORTFOLIO_ENGINE")
        candidate.parent.mkdir(parents=True, exist_ok=True)
        with candidate.open("x", encoding="utf-8") as fh:
            json.dump(result, fh, indent=2, sort_keys=True, allow_nan=False)
            fh.write("\n")
    return result
