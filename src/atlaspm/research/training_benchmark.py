"""Synthetic benchmark; measure actual process peak RSS in isolated workers."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from datetime import date,timedelta
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from time import perf_counter


def sessions(days,assets):
    for i in range(days):
        d=str(date(2000,1,3)+timedelta(days=i))
        t=d+"T16:00:00-05:00"
        yield dict(date=d,close_at=t,assets=[
            dict(security_id=f"SYN:{j:07}",source="synthetic",observed_at=t,available_at=t,
                 close=100*(1+((j%17)+1)*.0001)**i,
                 features={"momentum":float(j%17),"variation":float(i%3)})
            for j in range(assets)])


def peak_bytes():
    if os.name=="nt":
        import ctypes
        from ctypes import wintypes
        class Counters(ctypes.Structure):
            _fields_=[("cb",wintypes.DWORD),("PageFaultCount",wintypes.DWORD)]+[
                (name,ctypes.c_size_t) for name in (
                "PeakWorkingSetSize","WorkingSetSize","QuotaPeakPagedPoolUsage","QuotaPagedPoolUsage",
                "QuotaPeakNonPagedPoolUsage","QuotaNonPagedPoolUsage","PagefileUsage","PeakPagefileUsage")]
        counters=Counters()
        counters.cb=ctypes.sizeof(counters)
        kernel=ctypes.windll.kernel32
        kernel.GetCurrentProcess.restype=wintypes.HANDLE
        fn=ctypes.windll.psapi.GetProcessMemoryInfo
        fn.argtypes=[wintypes.HANDLE,ctypes.POINTER(Counters),wintypes.DWORD]
        fn.restype=wintypes.BOOL
        if not fn(kernel.GetCurrentProcess(),ctypes.byref(counters),counters.cb):
            raise OSError("GetProcessMemoryInfo failed")
        return counters.PeakWorkingSetSize
    import resource
    value=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return int(value if sys.platform=="darwin" else value*1024)


def worker(mode,days,assets,length,budget,root):
    from atlaspm.research.training_engine import train,train_parquet
    start=perf_counter()
    p=dict(mode="synthetic",synthetic_fixture=True,feature_names=["momentum","variation"])
    config=dict(horizon=5,minimum_train_sessions=10,test_sessions=10,sequence_length=length)
    if mode=="memory":
        p["sessions"]=list(sessions(days,assets))
        out=train(p,**config)
    else:
        import pyarrow as pa
        import pyarrow.parquet as pq
        path=Path(root)/"input.parquet"
        parts=[]
        # Write one session at a time in a separate input generation step.
        for session in sessions(days,assets):
            rows=[dict(date=session["date"],close_at=session["close_at"],
                       **{k:v for k,v in a.items() if k!="features"},**a["features"])
                  for a in session["assets"]]
            path=Path(root)/(session["date"]+".parquet")
            pq.write_table(pa.Table.from_pylist(rows),path)
            parts.append(dict(path=path.name,sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
        p["partitions"]=parts
        out=train_parquet(root,p,feature_cache_dir=str(Path(root)/"features"),
                          prediction_archive_path=str(Path(root)/"predictions.sqlite3"),
                          array_budget_mib=budget,spill_dir=str(Path(root)/"spill"),**config)
    return dict(mode=mode,peak_rss_bytes=peak_bytes(),elapsed_seconds=perf_counter()-start,
                metrics=out["metrics"],sample_numeric_bytes=out["compact_sample_numeric_bytes"],
                peak_buffered_sessions=out["peak_buffered_sessions"])


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days",type=int,default=60)
    parser.add_argument("--assets",type=int,default=1000)
    parser.add_argument("--sequence-length",type=int,default=1)
    parser.add_argument("--array-budget-mib",type=int,default=32)
    parser.add_argument("--worker",choices=["memory","parquet"])
    parser.add_argument("--root")
    a=parser.parse_args()
    if not 30<=a.days<=3000 or not 2<=a.assets<=100000 or not 1<=a.sequence_length<=min(252,a.days-22) or a.array_budget_mib<1:
        parser.error("invalid synthetic benchmark dimensions")
    if a.worker:
        print(json.dumps(worker(a.worker,a.days,a.assets,a.sequence_length,a.array_budget_mib,a.root),allow_nan=False))
        return
    root=Path.cwd()/"local_data"
    root.mkdir(exist_ok=True)
    with TemporaryDirectory(prefix="benchmark-",dir=root) as tmp:
        results=[]
        for mode in ("memory","parquet"):
            args=[sys.executable,"-m","atlaspm.research.training_benchmark",
                  "--worker",mode,"--days",str(a.days),"--assets",str(a.assets),
                  "--sequence-length",str(a.sequence_length),"--array-budget-mib",str(a.array_budget_mib),
                  "--root",tmp]
            results.append(json.loads(subprocess.check_output(args,text=True)))
    print(json.dumps(dict(synthetic=True,days=a.days,assets=a.assets,results=results,
          metrics_equal=results[0]["metrics"]==results[1]["metrics"],
          scope="whole worker including input generation and model dependencies",
          approved_for_model_training=False,live_execution_enabled=False),indent=2,allow_nan=False))


if __name__=="__main__":
    main()
