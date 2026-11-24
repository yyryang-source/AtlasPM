"""Local exploratory scan benchmark; no uploads or model fitting."""
from argparse import ArgumentParser
from contextlib import closing
import json
from time import perf_counter, process_time
import tracemalloc

from atlaspm.data.unified import CATALOG, iter_batches


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", choices=tuple(CATALOG), required=True)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--as-of", help="Required prior-date cutoff for SEC index")
    parser.add_argument("--column", action="append", default=[])
    parser.add_argument("--symbol", action="append", default=[])
    parser.add_argument("--batch-size", type=int, default=2048)
    args = parser.parse_args()
    rows = batches = largest = 0
    tracemalloc.start()
    wall, cpu = perf_counter(), process_time()
    try:
        with closing(iter_batches(args.dataset, start=args.start, end=args.end,
                                  columns=args.column, symbols=args.symbol,
                                  purpose="exploratory", as_of=args.as_of, batch_size=args.batch_size)) as stream:
            for batch in stream:
                size = len(batch.rows)
                rows += size
                batches += 1
                largest = max(largest, size)
        elapsed, cpu_elapsed = perf_counter() - wall, process_time() - cpu
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    print(json.dumps(dict(dataset=args.dataset, rows=rows, batches=batches,
                          largest_batch=largest, wall_seconds=elapsed,
                          cpu_seconds=cpu_elapsed, rows_per_second=rows / max(elapsed, 1e-9),
                          python_peak_bytes=peak, process_rss_measured=False,
                          coverage_verified=False, approved_for_model_training=False,
                          approved_for_backtest=False, live_execution_enabled=False), indent=2))


if __name__ == "__main__":
    main()
