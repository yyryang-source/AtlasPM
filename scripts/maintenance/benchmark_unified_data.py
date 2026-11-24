"""Local exploratory scan benchmark; no uploads or model fitting."""
from argparse import ArgumentParser
from contextlib import closing
import json
from pathlib import Path
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


def process_io_counters():
    """OS counters, not a measurement of physical device activity."""
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        class Counters(ctypes.Structure):
            _fields_ = [(name, ctypes.c_uint64) for name in (
                "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        kernel.GetProcessIoCounters.argtypes = (wintypes.HANDLE, ctypes.POINTER(Counters))
        kernel.GetProcessIoCounters.restype = wintypes.BOOL
        values = Counters()
        if not kernel.GetProcessIoCounters(kernel.GetCurrentProcess(), ctypes.byref(values)):
            raise OSError(ctypes.get_last_error(), "GetProcessIoCounters failed")
        return {"kind": "windows_process_transfer_bytes_includes_non_disk_io",
                "read_bytes": int(values.ReadTransferCount),
                "write_bytes": int(values.WriteTransferCount),
                "read_operations": int(values.ReadOperationCount),
                "write_operations": int(values.WriteOperationCount)}
    if sys.platform.startswith("linux"):
        try:
            values = dict(line.split(":", 1) for line in Path("/proc/self/io").read_text().splitlines())
        except (OSError, ValueError):
            return None
        return {"kind": "linux_process_io_accounting",
                **{k: int(values[k]) for k in ("rchar", "wchar", "read_bytes", "write_bytes",
                                               "syscr", "syscw") if k in values}}
    return None


def io_delta(before, after):
    if before is None or after is None or before["kind"] != after["kind"]:
        return None
    return {"kind": before["kind"], **{k: after[k] - v for k, v in before.items()
                                        if k != "kind" and k in after}}


def offline_benchmark():
    from atlaspm.framework.orchestrator import offline_release
    before=process_io_counters()
    wall,cpu=perf_counter(),process_time()
    report=offline_release()
    metrics={"scope":"synthetic_offline_fixture_only","wall_seconds":perf_counter()-wall,
             "cpu_seconds":process_time()-cpu,"process_lifetime_peak_rss_bytes":process_peak_rss_bytes(),
             "process_io_delta":io_delta(before,process_io_counters()),
             "production_performance_verified":False}
    print(json.dumps({"status":report["status"],"checks":report["checks"],"metrics":metrics,
                      "synthetic_simulation":report.get("synthetic_simulation"),
                      "mixed_asset_fixture":report.get("mixed_asset_fixture"),
                      "network_calls":0,"broker_side_effects":False},indent=2,allow_nan=False))
    return 0 if report["status"]=="OFFLINE_FIXTURE_PASS" else 1


def main():
    if sys.argv[1:]==["--offline"]:
        return offline_benchmark()
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
    parser.add_argument("--no-tracemalloc", action="store_true", help="Measure throughput without Python allocation tracing overhead")
    args = parser.parse_args()
    rows = batches = largest = 0
    emitted_sources = {}
    rss_before = process_peak_rss_bytes()
    if not args.no_tracemalloc:
        tracemalloc.start()
    io_before = process_io_counters()
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
                emitted_sources[batch.origin] = batch.source_version_hint[0]
        elapsed, cpu_elapsed = perf_counter() - wall, process_time() - cpu
        io_after = process_io_counters()
        peak = tracemalloc.get_traced_memory()[1] if not args.no_tracemalloc else None
    finally:
        if not args.no_tracemalloc:
            tracemalloc.stop()
    rss_after = process_peak_rss_bytes()
    print(json.dumps(dict(dataset=args.dataset, start=args.start, end=args.end,
                          columns=args.column, symbols=args.symbol, as_of=args.as_of,
                          batch_size=args.batch_size, platform=sys.platform,
                          python_version=sys.version.split()[0], rows=rows, batches=batches,
                          largest_batch=largest, wall_seconds=elapsed,
                          emitted_source_files=len(emitted_sources),
                          emitted_source_file_bytes=sum(emitted_sources.values()),
                          cpu_seconds=cpu_elapsed, rows_per_second=rows / max(elapsed, 1e-9),
                          cpu_seconds_per_wall_second=cpu_elapsed / max(elapsed, 1e-9),
                          python_peak_bytes=peak, tracemalloc_enabled=not args.no_tracemalloc,
                          process_io_delta=io_delta(io_before, io_after),
                          process_rss_measured=rss_after is not None,
                          process_lifetime_peak_rss_before_bytes=rss_before,
                          process_lifetime_peak_rss_after_bytes=rss_after,
                          memory_config=args.memory, threads=args.threads,
                          coverage_verified=False, approved_for_model_training=False,
                          approved_for_backtest=False, live_execution_enabled=False), indent=2))


if __name__ == "__main__":
    raise SystemExit(main())
