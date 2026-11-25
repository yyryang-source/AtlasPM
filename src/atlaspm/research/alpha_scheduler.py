"""Explicitly enabled source polling. All adapters feed one AI inbox."""
from argparse import ArgumentParser
from datetime import UTC, datetime
import json
import time

from atlaspm.research.alpha_sources import options_once, news_once, institutional_once
from atlaspm.research import event_queue


def schedule(tasks, *, cycles, clock=time.monotonic, sleep=time.sleep):
    if type(cycles) is not int or not 1 <= cycles <= 86400 or not tasks:
        raise ValueError("Invalid scheduler bounds")
    if any(type(interval) is not int or not 5 <= interval <= 86400 for interval, _ in tasks.values()):
        raise ValueError("Invalid source interval")
    due = dict.fromkeys(tasks, 0.)
    failures = dict.fromkeys(tasks, 0)
    for _ in range(cycles):
        control = event_queue._read_state(event_queue.PATH).get(event_queue.CONTROL_KEY,{})
        if control.get("stopped") or control.get("manual_takeover"):
            yield {"status":"PAUSED"}
            return
        for name,(interval,call) in tasks.items():
            if clock() < due[name]:
                continue
            try:
                added = call()
            except (ValueError, OSError, KeyError, TypeError) as exc:
                failures[name] += 1
                due[name] = clock()+min(3600, max(interval, 30)*2**min(failures[name],6))
                yield {"source":name,"status":"BACKOFF","error_type":type(exc).__name__}
            else:
                failures[name] = 0
                due[name] = clock()+interval
                yield {"source":name,"status":"OK","added":added}
        sleep(max(0.,min(due.values())-clock()))


def main():
    parser=ArgumentParser(description=__doc__)
    parser.add_argument("--options",action="store_true")
    parser.add_argument("--news",action="append",choices=["yahoo_finance","bloomberg"],default=[])
    parser.add_argument("--institutional",action="append",default=[],help="Reviewed subject=SYMBOL mapping")
    parser.add_argument("--cycles",type=int,default=100)
    args=parser.parse_args()
    tasks={}
    if args.options:
        tasks["unusual_whales"]=(5,options_once)
    for provider in args.news:
        def read_news(p=provider):
            now=datetime.now(UTC)
            return news_once(p,now.strftime("%Y-%m"),as_of=now.isoformat())
        tasks[provider]=(30,read_news)
    for item in args.institutional:
        subject,sep,symbol=item.partition("=")
        if not sep or not subject or not symbol:
            parser.error("Expected reviewed subject=SYMBOL")
        tasks["research:"+subject]=(300,lambda s=subject,t=symbol:institutional_once(subject=s,symbol=t,as_of=datetime.now(UTC).isoformat()))
    if not tasks:
        parser.error("Explicitly select sources")
    for report in schedule(tasks,cycles=args.cycles):
        print(json.dumps(report),flush=True)


if __name__=="__main__":
    main()
