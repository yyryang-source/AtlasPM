"""Foreground disclosure poller. Durable queue; no automatic external AI or trading."""
from argparse import ArgumentParser
from datetime import UTC, datetime
import json
import time

from atlaspm.data.api_gateway import GatewayError
from atlaspm.data.fed_disclosures import recent_events
from atlaspm.data.sec_latest_filings import recent_filings
from atlaspm.research import event_queue
from atlaspm.research.disclosure_jobs import register, work_once


def poll_once(source, *, path=event_queue.PATH, fetcher=None, documents=False):
    if source not in {"sec", "fed"}:
        raise ValueError("Unknown disclosure source")
    control = event_queue._read_state(path).get(event_queue.CONTROL_KEY, {})
    if control.get("stopped") or control.get("manual_takeover"):
        return {"source": source, "status": "PAUSED"}
    fetch = fetcher or (recent_filings if source == "sec" else
                        lambda: recent_events(monetary_only=False))
    report = fetch()
    if documents:
        register(source, report)
    queued = event_queue.enqueue(report["events"], path=path)
    return {"source": source, "status": "DISCOVERED", "observed": len(report["events"]),
            "added": queued["added"], "retained": queued["retained"],
            "transport": report["transport"], "coverage_complete": False}


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("--cycles", type=int, default=10)
    parser.add_argument("--interval", type=int, default=1)
    parser.add_argument("--fed-interval", type=int, default=30)
    parser.add_argument("--documents", action="store_true")
    parser.add_argument("--local-model", help="Explicit installed local Ollama model")
    args = parser.parse_args()
    if args.local_model and not args.documents:
        parser.error("--local-model requires --documents")
    if not 1 <= args.cycles <= 86400 or not 1 <= args.interval <= 300 or not 15 <= args.fed_interval <= 3600:
        parser.error("Invalid polling limits")
    next_fed = 0.
    for index in range(args.cycles):
        started = time.monotonic()
        sources = ["sec"]
        if started >= next_fed:
            sources.append("fed")
            next_fed = started + args.fed_interval
        for source in sources:
            try:
                report = poll_once(source, documents=args.documents)
            except (GatewayError, ValueError, OSError) as exc:
                print(json.dumps({"source": source, "status": "BLOCKED",
                                  "error_type": type(exc).__name__}), flush=True)
                # Fail closed on unexpected schemas, full queue, or transport error.
                return 1
            print(json.dumps(report), flush=True)
            if report["status"] == "PAUSED":
                return 0
        print(json.dumps(event_queue.drain(as_of=datetime.now(UTC).isoformat())), flush=True)
        if args.documents:
            print(json.dumps(work_once(model=args.local_model)), flush=True)
        if index + 1 < args.cycles:
            time.sleep(max(0., args.interval - (time.monotonic() - started)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
