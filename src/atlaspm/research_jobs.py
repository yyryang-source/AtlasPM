"""Persist local research inputs and run the existing CLI in a background process."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Thread
from tempfile import NamedTemporaryFile
from uuid import uuid4


COMMANDS = {
    "stock-screen": (
        {"prices", "universe-history", "fundamentals"},
        {"as-of", "top", "max-fundamental-age-days", "require-decision-close"},
    ),
    "stock-backtest": (
        {"prices", "benchmark-prices", "universe-history", "fundamentals", "delistings"},
        {"start", "end", "top", "rebalance-days", "fee-bps", "slippage-bps",
         "max-fundamental-age-days", "quant", "quant-max-weight", "quant-turnover-limit"},
    ),
}
RUN_TIMEOUT = 1800
MAX_INPUT_BYTES = 2 * 1024**3
MAX_REPORT_BYTES = 64 * 1024**2
MAX_STATE_BYTES = 65536


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _save_state(folder: Path, state: dict) -> None:
    raw=json.dumps(state, ensure_ascii=False, indent=2, allow_nan=False).encode("utf-8")
    if len(raw)>MAX_STATE_BYTES:
        raise ValueError("RESEARCH_JOB_STATE_BYTE_BUDGET")
    with NamedTemporaryFile(dir=folder,prefix="job-",suffix=".tmp",delete=False) as handle:
        temporary=Path(handle.name)
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())
    try:
        for attempt in range(5):
            try:
                temporary.replace(folder / "job.json")
                return
            except PermissionError:
                if attempt == 4:
                    raise
                time.sleep(.05)
    finally:
        temporary.unlink(missing_ok=True)


def _folder(root: Path, job_id: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{32}", job_id):
        raise ValueError("无效任务编号")
    base=Path(root).resolve()
    # Refuse path redirection even if the target is within the same checkout.
    parts=(base/"local_data",base/"local_data"/"runs",base/"local_data"/"runs"/job_id)
    if any(p.is_symlink() or p.resolve()!=p for p in parts):
        raise ValueError("RESEARCH_JOB_REDIRECTED_PATH")
    return parts[-1]


def _file(folder,name):
    path=folder/name
    if path.is_symlink() or path.resolve()!=path:
        raise ValueError("RESEARCH_JOB_REDIRECTED_FILE")
    return path


def _hash_file(path,budget):
    h=hashlib.sha256()
    count=0
    with path.open("rb") as handle:
        for chunk in iter(lambda:handle.read(1024*1024),b""):
            count+=len(chunk)
            if count>budget:
                raise ValueError("RESEARCH_JOB_ARTIFACT_BYTE_BUDGET")
            h.update(chunk)
    return h.hexdigest(),count


def _validate_request(command,label,inputs,options):
    if (command not in COMMANDS or not isinstance(label,str) or not 1<=len(label)<=200
            or not isinstance(inputs,dict) or not isinstance(options,dict)):
        raise ValueError("RESEARCH_JOB_REQUEST")
    files,flags=COMMANDS[command]
    if not inputs.keys()<=files or not options.keys()<=flags:
        raise ValueError("输入与研究命令不匹配")
    # Values must be single scalar CLI arguments, not additional switches.
    for value in options.values():
        if (type(value) not in (str,int,float,bool) or len(str(value))>200
                or (isinstance(value,str) and value.startswith("--"))):
            raise ValueError("RESEARCH_JOB_OPTION")
    json.dumps(options,allow_nan=False)


def _request_hash(state):
    return hashlib.sha256(json.dumps(
        {k:state[k] for k in ("id","command","label","inputs","options")},
        ensure_ascii=False,sort_keys=True,separators=(",",":"),allow_nan=False
    ).encode("utf-8")).hexdigest()


def _verified_inputs(folder,state):
    _validate_request(state["command"],state["label"],state["inputs"],state["options"])
    if state.get("request_sha256")!=_request_hash(state):
        raise ValueError("RESEARCH_JOB_REQUEST_INTEGRITY")
    remaining=MAX_INPUT_BYTES
    sources={}
    for flag,record in state["inputs"].items():
        if not isinstance(record,dict) or record.get("file")!=flag+".csv":
            raise ValueError("RESEARCH_JOB_INPUT_PATH")
        path=_file(folder,flag+".csv")
        sha,size=_hash_file(path,remaining)
        remaining-=size
        if sha!=record.get("sha256") or size!=record.get("bytes"):
            raise ValueError("RESEARCH_JOB_INPUT_INTEGRITY")
        sources[flag]=path
    return sources


def read_job(root: Path, job_id: str) -> dict:
    folder=_folder(root,job_id)
    with _file(folder,"job.json").open("rb") as handle:
        raw=handle.read(MAX_STATE_BYTES+1)
    if len(raw)>MAX_STATE_BYTES:
        raise ValueError("RESEARCH_JOB_STATE_BYTE_BUDGET")
    from atlaspm.data.api_gateway import _strict_json
    state=_strict_json(raw)
    if (not isinstance(state,dict) or state.get("id")!=job_id
            or state.get("status") not in {"queued","running","completed","failed"}):
        raise ValueError("RESEARCH_JOB_STATE_SCHEMA")
    age = datetime.now(timezone.utc) - datetime.fromisoformat(state["updated_at"])
    if state["status"] in {"queued", "running"}:
        limit = 120 if state["status"] == "queued" else RUN_TIMEOUT + 60
        if age > timedelta(seconds=limit):
            return state | {"status": "failed", "recovery_required":True,
                            "error": "任务进程可能中断或超时；先确认进程已停止。"} 
    return state


def list_jobs(root: Path, command: str | None = None) -> list[dict]:
    records = []
    for path in (root / "local_data" / "runs").glob("*/job.json"):
        if not re.fullmatch(r"[0-9a-f]{32}", path.parent.name):
            continue
        try:
            state = read_job(root, path.parent.name)
        except (OSError, ValueError, KeyError):
            continue
        if command is None or command == state["command"]:
            records.append(state)
    return sorted(records, key=lambda job: job["created_at"], reverse=True)


def job_file(root: Path, job_id: str, name: str) -> Path:
    if name not in {"job.json", "report.json", "run.log"}:
        raise ValueError("未知任务文件")
    return _file(_folder(root,job_id),name)


def verified_report(root,job_id):
    """Return a verified completed artifact path; old unsealed jobs need rerun."""
    state=read_job(root,job_id)
    if state["status"]!="completed" or state.get("request_sha256")!=_request_hash(state):
        raise ValueError("RESEARCH_JOB_COMPLETED_EVIDENCE_REQUIRED")
    path=job_file(root,job_id,"report.json")
    sha,size=_hash_file(path,MAX_REPORT_BYTES)
    if (sha,size)!=(state.get("report_sha256"),state.get("report_bytes")):
        raise ValueError("RESEARCH_JOB_REPORT_INTEGRITY")
    return path


def retry_job(root,job_id):
    """Explicit fresh research job, never overwrite an earlier run."""
    folder=_folder(root,job_id)
    state=read_job(root,job_id)
    if state["status"]!="failed" or (folder/"worker.lock").exists():
        raise ValueError("RESEARCH_JOB_RETRY_REQUIRES_STOPPED_FAILED_WORKER")
    sources=_verified_inputs(folder,state)
    return queue_job(root,state["command"],state["label"],sources,state["options"])


def queue_job(
    root: Path, command: str, label: str, inputs: dict[str, Path | bytes], options: dict,
) -> str:
    _validate_request(command,label,inputs,options)
    job_id = uuid4().hex
    folder = _folder(root, job_id)
    folder.mkdir(parents=True,exist_ok=False)
    state = {
        "id": job_id, "command": command, "label": label, "status": "queued",
        "created_at": _now(), "updated_at": _now(), "inputs": {}, "options": options,
    }
    try:
        total=0
        for flag, source in inputs.items():
            snapshot = folder / f"{flag}.csv"
            source_stream = source.open("rb") if isinstance(source, Path) else io.BytesIO(source)
            digest = hashlib.sha256()
            size=0
            with source_stream, snapshot.open("xb") as output:
                for chunk in iter(lambda: source_stream.read(1024 * 1024), b""):
                    size+=len(chunk)
                    total+=len(chunk)
                    if total>MAX_INPUT_BYTES:
                        raise ValueError("RESEARCH_JOB_INPUT_BYTE_BUDGET")
                    output.write(chunk)
                    digest.update(chunk)
            state["inputs"][flag] = {
                "file": snapshot.name, "sha256": digest.hexdigest(), "bytes":size,
                "source": source.name if isinstance(source, Path) else "页面导入或内置样本",
            }
        state["request_sha256"]=_request_hash(state)
        _save_state(folder, state)
        argv = [sys.executable, "-X", "utf8", "-m", "atlaspm.research_jobs", job_id]
        platform = (
            {"creationflags": subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP}
            if os.name == "nt" else {"start_new_session": True}
        )
        with (folder / "run.log").open("wb") as log:
            process = subprocess.Popen(
                argv, cwd=root, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                **platform,
            )
        Thread(target=process.wait, daemon=True).start()
    except (OSError,ValueError,TypeError) as exc:
        _save_state(folder, state | {"status": "failed", "updated_at": _now(), "error": type(exc).__name__})
    return job_id


def _execute(root: Path, job_id: str) -> None:
    folder=_folder(root,job_id)
    lock=_file(folder,"worker.lock")
    try:
        fd=os.open(lock,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    except FileExistsError:
        return
    os.close(fd)
    try:
        _execute_locked(root,job_id,folder)
    finally:
        lock.unlink(missing_ok=True)


def _execute_locked(root,job_id,folder):
    state=read_job(root,job_id)
    if state["status"]!="queued":
        return
    state.update(status="running",updated_at=_now())
    _save_state(folder,state)
    try:
        sources=_verified_inputs(folder,state)
        report=_file(folder,"report.json")
        if report.exists():
            raise ValueError("RESEARCH_JOB_PREEXISTING_REPORT")
        argv=[sys.executable,"-X","utf8","-m","atlaspm.cli",state["command"]]
        for flag,path in sorted(sources.items()):
            argv.extend((f"--{flag}",str(path)))
        for flag,value in sorted(state["options"].items()):
            if value is True:
                argv.append(f"--{flag}")
            elif value is not False:
                argv.extend((f"--{flag}",str(value)))
        result=subprocess.run([*argv,"--output",str(report)],cwd=root,
                              timeout=RUN_TIMEOUT,check=False)
        if result.returncode==0 and report.is_file():
            # Recheck frozen inputs after the command and seal its output.
            _verified_inputs(folder,state)
            sha,size=_hash_file(_file(folder,"report.json"),MAX_REPORT_BYTES)
            if not size:
                raise ValueError("RESEARCH_JOB_EMPTY_REPORT")
            state.update(status="completed",report_sha256=sha,report_bytes=size)
        else:
            state.update(status="failed",error=f"研究命令结束，退出码 {result.returncode}。请查看运行日志。")
    except subprocess.TimeoutExpired:
        state.update(status="failed",error="研究运行超过 30 分钟，进程已停止。")
    except (OSError,ValueError,TypeError,KeyError) as exc:
        state.update(status="failed",error=str(exc)[:500] if isinstance(exc,ValueError) else type(exc).__name__)
    finally:
        state["updated_at"]=_now()
        _save_state(folder,state)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("job_id")
    _execute(Path.cwd(), parser.parse_args().job_id)
