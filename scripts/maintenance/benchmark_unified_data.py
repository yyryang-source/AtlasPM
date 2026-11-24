"""Local exploratory scan benchmark; no uploads or model fitting."""
from argparse import ArgumentParser
from contextlib import closing
import json
import sys
from time import perf_counter, process_time
import tracemalloc

from atlaspm.data.unified import CATALOG, iter_batches


def process_peak_rss_bytes():
    """OS process lifetime peak, including native database allocations."""
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        class Counters(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD)] + [
                (name, ctypes.c_size_t) for name in (
                    "PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage",
                    "QuotaPagedPoolUsage", "QuotaPeakNonPagedPoolUsage",
                    "QuotaNonPagedPoolUsage", "PagefileUsage", "PeakPagefileUsage")]
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        psapi.GetProcessMemoryInfo.argtypes = (wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD)
        psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
        counters = Counters()
        counters.cb = ctypes.sizeof(counters)
        if not psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
            raise OSError(ctypes.get_last_error(), "GetProcessMemoryInfo failed")
        return int(counters.PeakWorkingSetSize)
    if sys.platform.startswith("linux") or sys.platform == "darwin":
        import resource
        value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return int(value if sys.platform == "darwin" else value * 1024)
    return None


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", choices=tuple(CATALOG), required=True)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--as-of", help="Required prior-date cutoff for SEC index")
    parser.add_argument("--column", action="append", default=[])
    parser.add_argument("--symbol", action="append", default=[])
    parser.add_argument("--batch-size", type=int, default=2048)
    parser.add_argument("--memory", default="256MB")
    parser.add_argument("--threads", type=int, default=1)
    args = parser.parse_args()
    rows = batches = largest = 0
    rss_before = process_peak_rss_bytes()
    tracemalloc.start()
    wall, cpu = perf_counter(), process_time()
    try:
        with closing(iter_batches(args.dataset, start=args.start, end=args.end,
                                  columns=args.column, symbols=args.symbol,
                                  purpose="exploratory", as_of=args.as_of, batch_size=args.batch_size,
                                  memory=args.memory, threads=args.threads)) as stream:
            for batch in stream:
                size = len(batch.rows)
                rows += size
                batches += 1
                largest = max(largest, size)
        elapsed, cpu_elapsed = perf_counter() - wall, process_time() - cpu
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    rss_after = process_peak_rss_bytes()
    print(json.dumps(dict(dataset=args.dataset, rows=rows, batches=batches,
                          largest_batch=largest, wall_seconds=elapsed,
                          cpu_seconds=cpu_elapsed, rows_per_second=rows / max(elapsed, 1e-9),
                          python_peak_bytes=peak, process_rss_measured=rss_after is not None,
                          process_lifetime_peak_rss_before_bytes=rss_before,
                          process_lifetime_peak_rss_after_bytes=rss_after,
                          memory_config=args.memory, threads=args.threads,
                          coverage_verified=False, approved_for_model_training=False,
                          approved_for_backtest=False, live_execution_enabled=False), indent=2))


if __name__ == "__main__":
    main()
