"""Reproducible bounded synthetic alpha benchmark; never real-data training."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from time import perf_counter
from atlaspm.research.training_benchmark import sessions, peak_bytes
from atlaspm.research.training_registry import FLAGS


def make_request(days=65, assets=32):
    if (type(days) is not int or type(assets) is not int or not 65<=days<=10000
            or not 3<=assets or days*assets>200000):
        raise ValueError("ALPHA_BENCHMARK_DIMENSIONS")
    rows=list(sessions(days,assets))
    return dict(payload=dict(mode="synthetic",synthetic_fixture=True,
                             feature_names=["momentum","variation"],sessions=rows),
                validation_start=rows[days//3+4]["date"],
                holdout_start=rows[2*days//3+1]["date"])


def benchmark(days=65,assets=32):
    from atlaspm.research.autonomous_alpha import research
    start=perf_counter()
    request=make_request(days,assets)
    report=research(**request)
    return dict(days=days,assets=assets,rows=days*assets,
                elapsed_seconds=perf_counter()-start,peak_rss_bytes=peak_bytes(),
                decision=report["decision"],selected_strategy=report["selected_strategy"],
                dataset_sha256=report["dataset_sha256"],
                scope="whole process peak RSS; elapsed includes fixture generation and research",
                **FLAGS)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days",type=int,default=65)
    parser.add_argument("--assets",type=int,default=32)
    parser.add_argument("--request-output",type=Path)
    args=parser.parse_args()
    if args.request_output:
        path=args.request_output.resolve()
        if not path.is_relative_to(Path.cwd().resolve()/"local_data") or path.suffix!=".json":
            parser.error("request output must be an ignored local_data JSON file")
        path.parent.mkdir(parents=True,exist_ok=True)
        with path.open("x",encoding="utf-8") as f:
            json.dump(make_request(args.days,args.assets),f,allow_nan=False)
        print(json.dumps({"synthetic_request_written":True,**FLAGS}))
    else:
        print(json.dumps(benchmark(args.days,args.assets),indent=2,allow_nan=False))


if __name__=="__main__":
    main()
