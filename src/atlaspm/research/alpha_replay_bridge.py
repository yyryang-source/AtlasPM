"""Synthetic locked multi-horizon rank fusion into existing cash replay.

Rank weights are fixed before holdout; no future labels or actual returns
are consumed by the bridge. This is not a calibrated return forecast,
production model admission or actual execution.
"""
from __future__ import annotations

from copy import deepcopy
from itertools import groupby
from math import fsum
from atlaspm.engine.contracts import finite, timestamp
from atlaspm.engine.replay import replay
from atlaspm.research.training_engine import _ranks
from atlaspm.research.training_registry import FLAGS, digest

SCHEMA="atlaspm_synthetic_alpha_replay_bridge_v1"


class RankReplay:
    def __init__(self,panel,holdout_start,horizons,weights,execution,*,strategy_review=False):
        self.strategy_review=strategy_review
        if not isinstance(execution,dict) or set(execution)-{
                "mode","synthetic_fixture","sessions","initial_cash","fee_bps",
                "slippage_bps","cash_reserve","top_k","rebalance_sessions",
                "settlement_sessions","risk_policy","position_lifecycle",
                "execution_policy","optimizer",*FLAGS}:
            raise ValueError("INVALID_ALPHA_REPLAY_CONFIG")
        if (execution.get("mode")!="synthetic" or execution.get("synthetic_fixture") is not True
                or any(k in execution and execution[k] is not v for k,v in FLAGS.items())):
            raise ValueError("ALPHA_REPLAY_SYNTHETIC_ONLY")
        if not all(isinstance(execution.get(k),dict) for k in
                   ("risk_policy","execution_policy","position_lifecycle")):
            raise ValueError("ALPHA_REPLAY_RISK_EXECUTION_LIFECYCLE_REQUIRED")
        # Missing forecasts never trigger a thesis liquidation; independent
        # protective risk exits remain active.
        if "thesis_score_floor" in execution["position_lifecycle"]:
            raise ValueError("ALPHA_REPLAY_RANK_NOT_THESIS_VALUE")
        if not isinstance(weights,dict) or set(weights)!={str(h) for h in horizons}:
            raise ValueError("ALPHA_REPLAY_EXACT_HORIZON_WEIGHTS")
        clean={int(h):finite(v,"rank_weight",minimum=.000001) for h,v in weights.items()}
        if any(v>1000 for v in clean.values()):
            raise ValueError("ALPHA_REPLAY_WEIGHT_BUDGET")
        total=fsum(clean.values())
        self.weights={h:v/total for h,v in clean.items()}
        self.execution=deepcopy(execution)
        self.execution["missing_signal_policy"]="hold"
        sessions=panel["sessions"][holdout_start:]
        marks=self.execution.get("sessions")
        if not isinstance(marks,list) or not 2<=len(marks)<=250 or len(marks)!=len(sessions):
            raise ValueError("ALPHA_REPLAY_EXACT_HOLDOUT_SESSIONS")
        self.days={}
        self.ranks={}
        self.ids=None
        self.sectors=None
        for s,m in zip(sessions,marks):
            if (not isinstance(m,dict) or set(m)!={"date","open_at","close_at","source",
                                                  "open","close","volume","sectors"}
                    or m["date"]!=s["date"] or m["close_at"]!=s["close_at"]
                    or m["source"]!="synthetic"):
                raise ValueError("ALPHA_REPLAY_MARK_SESSION_MISMATCH")
            assets={a["security_id"]:a for a in s["assets"]}
            if len(assets)!=len(s["assets"]) or not 2<=len(assets)<=20:
                raise ValueError("ALPHA_REPLAY_UNIVERSE_BUDGET")
            if any(not isinstance(sid,str) or not sid.startswith("SYN:") or not sid[4:]
                   for sid in assets):
                raise ValueError("ALPHA_REPLAY_INVALID_IDENTITY")
            ids=sorted(assets)
            if self.ids is None:
                self.ids=ids
            if ids!=self.ids:
                raise ValueError("ALPHA_REPLAY_FIXED_UNIVERSE_REQUIRED")
            symbols={sid[4:] for sid in ids}
            if any(not isinstance(m[k],dict) or set(m[k])!=symbols
                   for k in ("open","close","volume","sectors")):
                raise ValueError("ALPHA_REPLAY_MARK_UNIVERSE_MISMATCH")
            for sid,a in assets.items():
                if finite(m["close"][sid[4:]],"close",minimum=.000001)!=a["close"]:
                    raise ValueError("ALPHA_REPLAY_TRAINING_CLOSE_MISMATCH")
                finite(m["open"][sid[4:]],"open",minimum=.000001)
                finite(m["volume"][sid[4:]],"volume",minimum=0)
                if not isinstance(m["sectors"][sid[4:]],str) or not m["sectors"][sid[4:]].strip():
                    raise ValueError("ALPHA_REPLAY_SECTOR_REQUIRED")
            if timestamp(m["open_at"],"open_at")>=timestamp(m["close_at"],"close_at"):
                raise ValueError("ALPHA_REPLAY_MARK_TIME")
            if self.sectors is None:
                self.sectors=dict(m["sectors"])
            if self.strategy_review and self.sectors!=m["sectors"]:
                raise ValueError("ALPHA_REPLAY_REVIEW_FIXED_SECTORS_REQUIRED")
            self.days[s["date"]]=assets
        top=execution.get("top_k",2)
        if type(top) is not int or not 1<=top<=len(self.ids):
            raise ValueError("ALPHA_REPLAY_TOP_K")
        self.top=top
        # Defined before fitting or reading outcomes. End of common sample is
        # fixed by the longest label, not selected by retrospective performance.
        self.common_days=list(self.days)[:-max(horizons)]
        self.policy_sha256=digest({"weights":self.weights,
            "execution_policy":{k:v for k,v in self.execution.items() if k!="sessions"},
            "rank_method":"mean_tie_aware_percentile_v1",
            "common_days":self.common_days,"strategy_review":self.strategy_review})

    def add(self,horizon,rows):
        if horizon not in self.weights or horizon in self.ranks:
            raise ValueError("ALPHA_REPLAY_DUPLICATE_OR_UNKNOWN_HORIZON")
        ranks={}
        common=set(self.common_days)
        last=None
        for day,group in groupby(rows,key=lambda row:row["date"]):
            if last is not None and day<=last:
                raise ValueError("ALPHA_REPLAY_PREDICTION_ORDER")
            last=day
            if day not in common:
                continue
            values={}
            for row in group:
                sid=row["security_id"]
                if sid not in self.ids or sid in values:
                    raise ValueError("ALPHA_REPLAY_PREDICTION_IDENTITY")
                values[sid]=finite(row["prediction"],"prediction")
            # No use of row.actual or future label validity. A missing current
            # forecast blocks this cohort, rather than silently dropping it.
            if set(values)==set(self.ids):
                ranks[day]=dict(zip(self.ids,(r/(len(self.ids)-1) for r in
                    _ranks([values[sid] for sid in self.ids]))))
        self.ranks[horizon]=ranks

    def finish(self,selection_lock):
        if set(self.ranks)!=set(self.weights):
            raise ValueError("ALPHA_REPLAY_MISSING_HORIZON")
        version=digest([selection_lock,self.policy_sha256])
        review=[]
        common=set(self.common_days)
        for m in self.execution["sessions"]:
            day=m["date"]
            sectors=m.pop("sectors")
            m["signals"]=[]
            status="OUTSIDE_COMMON_FORECAST_WINDOW"
            if day in common:
                if any(day not in self.ranks[h] for h in self.weights):
                    status="INCOMPLETE_FORECAST_COHORT"
                else:
                    values={sid:fsum(self.weights[h]*self.ranks[h][day][sid]
                                      for h in sorted(self.weights)) for sid in self.ids}
                    ranked=sorted(self.ids,key=lambda sid:(-values[sid],sid))
                    if (len(set(values.values()))==1 or
                            (self.top<len(ranked) and values[ranked[self.top-1]]==values[ranked[self.top]])):
                        status="TIED_SELECTION_BOUNDARY"
                    else:
                        stamp=m["close_at"]
                        for rank,sid in enumerate(ranked,1):
                            m["signals"].append({"symbol":sid[4:],"security_id":sid,
                                "sector":sectors[sid[4:]],"model_version":version,
                                "identity_source":"synthetic","data_source":"synthetic",
                                "data_quality":"synthetic_test_only","research_eligible":True,
                                "model_approved_for_training":False,"generated_at":stamp,
                                "available_at":stamp,"data_available_at":self.days[day][sid]["available_at"],
                                "expires_at":stamp,"factor_score":2*values[sid]-1,"model_rank":rank})
                        status="SYNTHETIC_RANK_COHORT"
            review.append({"date":day,"status":status,"signals":len(m["signals"])})
        result=replay(self.execution)
        evaluation={}
        if self.strategy_review:
            from atlaspm.engine.replay_review import review as evaluate_review
            evaluation["strategy_review"]=evaluate_review(self.execution,result,common_days=self.common_days,
                                                 sectors=self.sectors)
        return {"schema":SCHEMA,**FLAGS,"approved_for_backtest":False,
                "selection_lock_sha256":selection_lock,"policy_sha256":self.policy_sha256,
                "normalized_weights":{str(h):w for h,w in self.weights.items()},
                "rank_method":"fixed_weight_percentile_rank_not_expected_return",
                "cohorts":review,"simulation":result,"broker_side_effects":False,**evaluation,
                "production_model_approved":False,
                "warning":"Synthetic next-open cash replay only. No end-of-sample forced exit, calibrated return forecast or evidence of real alpha."}
