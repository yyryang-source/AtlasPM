"""Bounded whole-universe scan using the single existing local AI worker."""
from argparse import ArgumentParser
from datetime import UTC, datetime
import json
import time

from atlaspm.research.alpha_universe import load_universe, batches
from atlaspm.research.alpha_inbox import run_once, PATH
from atlaspm.research.financial_events import FLAGS, _time
from atlaspm.research import event_queue


def scan(universe, *, model, batch_size=20, path=PATH, worker=run_once):
    groups = list(batches(universe, size=batch_size))
    candidates = {}
    completed = 0
    failures = 0
    for symbols in groups:
        control = event_queue._read_state(event_queue.PATH).get(event_queue.CONTROL_KEY, {})
        if control.get("stopped") or control.get("manual_takeover"):
            return {**FLAGS, "status":"PAUSED", "opportunities":[], "batches_completed":completed}
        try:
            report = worker(universe=symbols, model=model, path=path)
            if report["status"] == "PAUSED":
                return {**FLAGS, "status":"PAUSED", "opportunities":[], "batches_completed":completed}
            if report["status"] not in {"CANDIDATES","UNCHANGED","NO_TRADE"}:
                failures += 1
                continue
            batch_candidates = {}
            for item in report["opportunities"]:
                symbol = item["symbol"]
                if symbol not in symbols or symbol in batch_candidates or item.get("candidate_only") is not True:
                    raise ValueError("Invalid batch candidate")
                if item["action"] not in {"LONG","SHORT","BUY_CALL","BUY_PUT"}:
                    raise ValueError("Invalid candidate action")
                _time(item["expires_at"])
                batch_candidates[symbol] = item
            candidates.update(batch_candidates)
            completed += 1
        except (ValueError, OSError, KeyError, TypeError):
            failures += 1
    now = datetime.now(UTC)
    control = event_queue._read_state(event_queue.PATH).get(event_queue.CONTROL_KEY, {})
    if control.get("stopped") or control.get("manual_takeover"):
        return {**FLAGS, "status":"PAUSED", "opportunities":[], "batches_completed":completed}
    active = [item for _,item in sorted(candidates.items()) if _time(item["expires_at"]) > now]
    return {**FLAGS, "status":"PARTIAL" if failures else "CANDIDATES" if active else "NO_TRADE",
            "model":model,"universe_sha256":universe["sha256"],"as_of":now.isoformat(),
            "batches_total":len(groups),"batches_completed":completed,"batches_failed":failures,
            "coverage_complete":False,"opportunities":active}


def main():
    parser=ArgumentParser(description=__doc__)
    parser.add_argument("--universe",required=True)
    parser.add_argument("--model",required=True)
    parser.add_argument("--batch-size",type=int,default=20)
    parser.add_argument("--cycles",type=int,default=1)
    parser.add_argument("--interval",type=int,default=5)
    args=parser.parse_args()
    if not 1<=args.cycles<=86400 or not 1<=args.interval<=300:
        parser.error("Invalid runtime bounds")
    for i in range(args.cycles):
        report=scan(load_universe(args.universe),model=args.model,batch_size=args.batch_size)
        print(json.dumps(report,ensure_ascii=False),flush=True)
        if report["status"]=="PAUSED":
            break
        if i+1<args.cycles:
            time.sleep(args.interval)


if __name__=="__main__":
    main()
