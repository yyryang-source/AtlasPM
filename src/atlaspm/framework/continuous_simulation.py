"""Bounded continuous synthetic research-to-replay, with resumable prefix receipts.

Uses existing decision/fusion, historical replay, execution policy and risk
controls. No broker, external AI, formal backtest or training permission.
"""
from __future__ import annotations
from copy import deepcopy
from pathlib import Path
from atlaspm.engine.autonomous_decision import decide
from atlaspm.engine.replay import replay
from atlaspm.research.research_contract import contract
from atlaspm.research.financial_events import FLAGS
from atlaspm.research.training_registry import digest
from atlaspm.research.event_queue import _transaction
from atlaspm.engine.unified import SAFETY


def continuous(payload, *, weights, horizon_days=1, cost_fraction=.01, checkpoint=None):
    if (not isinstance(payload,dict) or payload.get("mode")!="synthetic" or payload.get("synthetic_fixture") is not True
        or any(k in payload and payload[k] is not v for k,v in {**FLAGS,**SAFETY}.items())
        or "margin_policy" in payload):
        raise ValueError("CONTINUOUS_SYNTHETIC_CASH_ONLY")
    sessions=payload["sessions"]
    if not isinstance(sessions,list) or not 2<=len(sessions)<=60:
        raise ValueError("CONTINUOUS_SESSION_BUDGET")
    if not all(k in payload for k in ("risk_policy","position_lifecycle","execution_policy")):
        raise ValueError("CONTINUOUS_RISK_AND_EXECUTION_REQUIRED")
    if not isinstance(payload["execution_policy"],dict):
        raise ValueError("CONTINUOUS_EXECUTION_POLICY_REQUIRED")
    config={k:v for k,v in payload.items() if k!="sessions"}
    policy_id=digest([config,weights,horizon_days,cost_fraction])
    fingerprints=[digest(s) for s in sessions]
    if len(set(fingerprints))!=len(fingerprints):
        raise ValueError("DUPLICATE_CONTINUOUS_SESSION")
    def compute():
        adapted=deepcopy(config)
        adapted["missing_signal_policy"]="hold"
        adapted["sessions"]=[]
        reports=[]
        for session in sessions:
            raw=deepcopy(session)
            research=raw.pop("research")
            if "signals" in raw or raw["source"]!="synthetic":
                raise ValueError("CONTINUOUS_REQUIRES_RESEARCH_NOT_CALLER_SIGNALS")
            symbols=set(raw["close"])
            if not 1<=len(symbols)<=20 or set(raw["open"])!=symbols:
                raise ValueError("CONTINUOUS_UNIVERSE_LIMIT")
            sectors=raw.pop("sectors")
            if set(sectors)!=symbols:
                raise ValueError("CONTINUOUS_SECTOR_COVERAGE")
            cutoff=raw["close_at"]
            raw["signals"]=[]
            try:
                if not isinstance(research,list) or not 1<=len(research)<=128:
                    raise ValueError("MISSING_OR_OVERSIZED_RESEARCH")
                result=decide(research,as_of=cutoff,weights=weights,horizon_days=horizon_days,cost_fraction=cost_fraction)
                candidates=result["candidates"]
                # All-or-nothing universe coverage; a missing/HOLD/SELL
                # forecast cannot silently liquidate a held name.
                if (len(candidates)!=len(symbols) or {g["security_id"] for g in candidates}!={"SYN:"+s for s in symbols}
                    or any(g["action"]!="BUY" or g["asset_type"] not in {"stock","etf"} for g in candidates)):
                    raise ValueError("NO_COMPLETE_POSITIVE_UNIVERSE")
                checked=[contract(row,as_of=cutoff) for row in research]
                for rank,g in enumerate(candidates,1):
                    symbol=g["security_id"][4:]
                    evidence=[v for v in checked if v["numeric_eligible"] and v["research_id"] in g["research_ids"]]
                    if not evidence:
                        raise ValueError("NO_VALID_RESEARCH_EVIDENCE")
                    oldest=min(v["data_available_at"] for v in evidence)
                    raw["signals"].append({"symbol":symbol,"security_id":g["security_id"],"sector":sectors[symbol],
                        "model_version":digest(g),"identity_source":"synthetic","data_source":"synthetic",
                        "data_quality":"synthetic_test_only","research_eligible":True,"model_approved_for_training":False,
                        "generated_at":cutoff,"available_at":cutoff,"data_available_at":oldest,"expires_at":cutoff,
                        "factor_score":g["robust_net_edge"],"model_rank":rank})
                reports.append({"date":raw["date"],"status":"SYNTHETIC_RESEARCH_ACCEPTED","decision_sha256":digest(result)})
            except (ValueError,KeyError,TypeError,OverflowError):
                raw["signals"]=[]
                reports.append({"date":raw["date"],"status":"NO_TRADE_RESEARCH_BLOCKED"})
            adapted["sessions"].append(raw)
        result=replay(adapted)
        # No fabricated final flat or settlement; require actual ledger state.
        final=result["curve"][-1]
        return {**FLAGS,"mode":"synthetic","decision":"CONTINUOUS_REPLAY_REVIEWED",
                "sessions":len(sessions),"research_reviews":reports,"simulation":result,
                "flat_and_settled":not final["holdings"] and abs(final["unsettled_cash"])<.01,
                "trained_model_used":False,"broker_side_effects":False}
    if checkpoint is None:
        return compute()
    # Existing atomic state writer, not a second scheduler. Bounded full
    # prefix recomputation on restart; no external execution or double fills.
    def commit(items):
        old=items.get("continuous")
        if old and (old["policy_sha256"]!=policy_id or fingerprints[:len(old["session_hashes"])]!=old["session_hashes"]):
            raise ValueError("CONTINUOUS_CHECKPOINT_PREFIX_CONFLICT")
        result=compute()
        result_hash=digest(result)
        if old and old["session_hashes"]==fingerprints and old["result_sha256"]!=result_hash:
            raise ValueError("CONTINUOUS_REPLAY_NONDETERMINISTIC")
        items["continuous"]={"policy_sha256":policy_id,"session_hashes":fingerprints,
                             "result_sha256":result_hash,"flat_and_settled":result["flat_and_settled"]}
        return {**result,"checkpoint_reused":bool(old and old["session_hashes"]==fingerprints),
                "result_sha256":result_hash}
    return _transaction(Path(checkpoint),commit)


def main():
    import argparse,json
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--file",required=True,type=Path)
    p.add_argument("--checkpoint")
    args=p.parse_args()
    if args.file.stat().st_size>2*1024*1024:
        raise ValueError("CONTINUOUS_INPUT_LIMIT")
    data=json.loads(args.file.read_text(encoding="utf-8"))
    out=continuous(data["payload"],weights=data["weights"],checkpoint=args.checkpoint,
                   horizon_days=data.get("horizon_days",1),cost_fraction=data.get("cost_fraction",.01))
    print(json.dumps(out,indent=2,allow_nan=False))


if __name__=="__main__":
    main()
