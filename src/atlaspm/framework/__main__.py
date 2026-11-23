"""Print the AtlasPM workflow contract without loading any private market data."""

from __future__ import annotations

import argparse
import json

from atlaspm.framework import mermaid_flowchart, workflow_report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m atlaspm.framework",
        description="Inspect the research-only AtlasPM standard quant workflow",
    )
    parser.add_argument("--format", choices=("json", "mermaid"), default="json")
    args = parser.parse_args(argv)
    if args.format == "mermaid":
        print(mermaid_flowchart())
    else:
        print(json.dumps(workflow_report(), indent=2, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
