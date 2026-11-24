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


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _save_state(folder: Path, state: dict) -> None:
    temporary = folder / "job.tmp"
    temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    for attempt in range(5):
        try:
            temporary.replace(folder / "job.json")
            return
        except PermissionError:
            if attempt == 4:
                raise
            time.sleep(.05)


def _folder(root: Path, job_id: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{32}", job_id):
        raise ValueError("无效任务编号")
    return root / "local_data" / "runs" / job_id


def read_job(root: Path, job_id: str) -> dict:
    state = json.loads((_folder(root, job_id) / "job.json").read_text(encoding="utf-8"))
    age = datetime.now(timezone.utc) - datetime.fromisoformat(state["updated_at"])
    if state["status"] in {"queued", "running"}:
        limit = 120 if state["status"] == "queued" else RUN_TIMEOUT + 60
        if age > timedelta(seconds=limit):
            return state | {"status": "failed", "error": "任务进程已中断或超时。请查看日志并重新运行。"}
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
    return _folder(root, job_id) / name


def queue_job(
    root: Path, command: str, label: str, inputs: dict[str, Path | bytes], options: dict,
) -> str:
    files, flags = COMMANDS[command]
    if not inputs.keys() <= files or not options.keys() <= flags:
        raise ValueError("输入与研究命令不匹配")
    job_id = uuid4().hex
    folder = _folder(root, job_id)
    folder.mkdir(parents=True)
    state = {
        "id": job_id, "command": command, "label": label, "status": "queued",
        "created_at": _now(), "updated_at": _now(), "inputs": {}, "options": options,
    }
    try:
        for flag, source in inputs.items():
            snapshot = folder / f"{flag}.csv"
            source_stream = source.open("rb") if isinstance(source, Path) else io.BytesIO(source)
            digest = hashlib.sha256()
            with source_stream, snapshot.open("wb") as output:
                for chunk in iter(lambda: source_stream.read(1024 * 1024), b""):
                    output.write(chunk)
                    digest.update(chunk)
            state["inputs"][flag] = {
                "file": snapshot.name, "sha256": digest.hexdigest(),
                "source": source.name if isinstance(source, Path) else "页面导入或内置样本",
            }
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
    except OSError as exc:
        _save_state(folder, state | {"status": "failed", "updated_at": _now(), "error": str(exc)})
    return job_id


def _execute(root: Path, job_id: str) -> None:
    folder = _folder(root, job_id)
    state = read_job(root, job_id)
    if state["status"] != "queued":
        return
    command = state["command"]
    files, flags = COMMANDS[command]
    state.update(status="running", updated_at=_now())
    _save_state(folder, state)
    try:
        argv = [sys.executable, "-X", "utf8", "-m", "atlaspm.cli", command]
        for flag in sorted(files.intersection(state["inputs"])):
            argv.extend((f"--{flag}", str(folder / f"{flag}.csv")))
        for flag in sorted(flags.intersection(state["options"])):
            value = state["options"][flag]
            if value is True:
                argv.append(f"--{flag}")
            elif value is not False:
                argv.extend((f"--{flag}", str(value)))
        result = subprocess.run(
            [*argv, "--output", str(folder / "report.json")],
            cwd=root, timeout=RUN_TIMEOUT, check=False,
        )
        if result.returncode == 0 and (folder / "report.json").is_file():
            state.update(status="completed")
        else:
            state.update(status="failed", error=f"研究命令结束，退出码 {result.returncode}。请查看运行日志。")
    except subprocess.TimeoutExpired:
        state.update(status="failed", error="研究运行超过 30 分钟，进程已停止。")
    except OSError as exc:
        state.update(status="failed", error=str(exc))
    finally:
        state["updated_at"] = _now()
        _save_state(folder, state)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("job_id")
    _execute(Path.cwd(), parser.parse_args().job_id)
