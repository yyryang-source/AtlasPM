"""Synthetic point-in-time panel contracts; real data remains blocked."""
from __future__ import annotations
from datetime import date
from array import array
from bisect import bisect_left
from collections import deque
from atlaspm.research.training_compact import CompactSamples, IndexedRows, IndexPrefix
from atlaspm.engine.contracts import finite, timestamp

HORIZONS = (1, 5, 10, 21, 63)


def validate_panel_metadata(payload,horizon,sequence_length):
    if type(sequence_length) is not int or not 1<=sequence_length<=252:
        raise ValueError("INVALID_SEQUENCE_LENGTH")
    if payload.get("mode")!="synthetic" or payload.get("synthetic_fixture") is not True:
        from atlaspm.research.data_admission import require_real_training_admission
        require_real_training_admission()
    if type(horizon) is not int or horizon not in HORIZONS:
        raise ValueError("INVALID_LABEL_HORIZON")
    features=payload["feature_names"]
    if not isinstance(features,list) or not features:
        raise ValueError("INVALID_FEATURE_SCHEMA")
    if any(not isinstance(f,str) or not f or f.lower().startswith(("label","future","target")) for f in features):
        raise ValueError("FORBIDDEN_FEATURE_NAME")
    if len(set(features))!=len(features):
        raise ValueError("INVALID_FEATURE_SCHEMA")
    return features


def validated_sessions(sessions,features):
    """Single validation contract shared by JSON and Parquet panel builders."""
    prior=None
    previous_day=None
    feature_set=set(features)
    for row in sessions:
        day=date.fromisoformat(row["date"])
        close=timestamp(row["close_at"],"close_at")
        if close.date()!=day or (prior is not None and (close<=prior or day<=previous_day)):
            raise ValueError("SESSION_ORDER")
        prior,previous_day=close,day
        marks={}
        for item in row["assets"]:
            sid=item["security_id"]
            if not isinstance(sid,str) or not sid.startswith("SYN:") or sid in marks:
                raise ValueError("INVALID_OR_DUPLICATE_SYNTHETIC_ID")
            if item.get("source")!="synthetic":
                raise ValueError("UNVERIFIED_SOURCE")
            if not timestamp(item["observed_at"],"observed_at")<=timestamp(item["available_at"],"available_at")<=close:
                raise ValueError("FEATURE_LOOKAHEAD")
            if set(item["features"])!=feature_set:
                raise ValueError("FEATURE_SCHEMA_MISMATCH")
            marks[sid]=(finite(item["close"],"close",minimum=.000001),
                        [finite(item["features"][f],f) for f in features])
        if not marks:
            raise ValueError("EMPTY_SESSION")
        yield str(day),marks


class MarketRegimes:
    """Close-observed equal-weight shared-security proxy; synthetic only."""
    def __init__(self, *, lookback=20, trend_threshold=.02, volatility_threshold=.25):
        from collections import deque
        if type(lookback) is not int or not 2<=lookback<=252:
            raise ValueError("INVALID_REGIME_LOOKBACK")
        self.trend=finite(trend_threshold,"trend_threshold",minimum=0)
        self.volatility=finite(volatility_threshold,"volatility_threshold",minimum=.000001)
        self.returns=deque(maxlen=lookback)
        self.previous={}
        self.states={}

    def observe(self,day,marks):
        from statistics import fmean,stdev
        from math import prod,sqrt
        current={sid:v[0] for sid,v in marks.items()}
        shared=sorted(current.keys() & self.previous.keys())
        if not shared:
            self.returns.clear()
        else:
            self.returns.append(fmean(current[s]/self.previous[s]-1 for s in shared))
        self.previous=current
        state="warmup"
        if len(self.returns)==self.returns.maxlen:
            growth=prod(1+r for r in self.returns)-1
            volatility=stdev(self.returns)*sqrt(252)
            if volatility>=self.volatility:
                state="high_volatility"
            elif growth>self.trend:
                state="uptrend"
            elif growth<-self.trend:
                state="downtrend"
            else:
                state="sideways"
        self.states[day]=state


class WindowLabels:
    """Generate matured labels in one pass over the global session sequence.

    A current consecutive-presence count of h+1 proves that every required
    future session exists. Eligibility is frozen at the signal session, before
    any future close is observed. Retain only h+1 raw sessions.
    """
    def __init__(self,horizon,length):
        self.horizon=horizon
        self.length=length
        self.window=deque(maxlen=horizon+1)
        self.streak={}
        self.missing=0
        self.incomplete=0
        self.peak=0

    def observe(self,index,marks):
        previous=self.streak
        self.streak={sid:previous.get(sid,0)+1 for sid in marks}
        eligible=({sid for sid,n in self.streak.items() if n>=self.length}
                  if self.length>1 else None)
        self.window.append((marks,eligible))
        self.peak=max(self.peak,len(self.window))
        if index<self.horizon:
            return
        signal,eligible=self.window[0]
        i=index-self.horizon
        for sid,(price,x) in sorted(signal.items()):
            if eligible is not None and sid not in eligible:
                self.incomplete+=1
                continue
            if self.streak.get(sid,0)<self.horizon+1:
                y=None
                self.missing+=1
            else:
                y=finite(marks[sid][0]/price-1,"forward_return")
            yield sid,i,x,y


def build_panel(payload, horizon, sequence_length=1, *, compact=False, feature_column=None, regime_config=None):
    if feature_column is not None and not compact:
        raise ValueError('DISK_FEATURES_REQUIRE_COMPACT_PANEL')
    features=validate_panel_metadata(payload,horizon,sequence_length)
    tracker=MarketRegimes(**regime_config) if regime_config is not None else None
    dates,observations=[],[]
    keep_observations=not compact or sequence_length>1
    labels=WindowLabels(horizon,sequence_length)
    samples,prediction_samples=[],[]
    if compact:
        prediction_samples=CompactSamples(dates,horizon,len(features),feature_column)
        valid_indices=array('Q')
    for k,(day,marks) in enumerate(validated_sessions(payload["sessions"],features)):
        dates.append(day)
        if keep_observations:
            observations.append(marks)
        if tracker is not None:
            tracker.observe(day,marks)
        for sid,i,x,y in labels.observe(k,marks):
            if compact:
                prediction_samples.append(sid,i,x,y)
                if y is not None:
                    valid_indices.append(len(prediction_samples)-1)
                continue
            sequence=[observations[j][sid][1] for j in range(i-sequence_length+1,i+1)]
            sample=dict(security_id=sid,signal_index=i,label_end_index=k,date=dates[i],
                        label_end_date=dates[k],x=x,sequence=sequence,y=y)
            prediction_samples.append(sample)
            if y is not None:
                samples.append(sample)
    if compact:
        if feature_column is not None:
            feature_column.seal()
        samples=IndexedRows(prediction_samples,valid_indices)
    return dict(feature_names=features,dates=dates,samples=samples,horizon=horizon,
                prediction_samples=prediction_samples,sequence_length=sequence_length,
                **({'observations':observations} if compact and sequence_length>1 else {}),
                market_regimes=tracker.states if tracker is not None else None,
                incomplete_sequences=labels.incomplete,
                missing_label_paths=labels.missing,unmatured_final_sessions=min(horizon,len(dates)),
                research_only=True,approved_for_model_training=False,
                historical_security_identity_verified=False,live_execution_enabled=False)


def sequence_for(panel,row):
    """Materialize a trailing window only for the requested sample."""
    if "sequence" in row:
        return row["sequence"]
    length=panel["sequence_length"]
    if length==1:
        return [row["x"]]
    i=row["signal_index"]
    sid=row["security_id"]
    if panel.get("observation_store") is not None:
        return panel["observation_store"].sequence(i,sid,length)
    return [panel["observations"][j][sid][1] for j in range(i-length+1,i+1)]


def iter_purged_folds(panel, *, minimum_train_sessions, test_sessions, embargo_sessions=0):
    for v,lo in ((minimum_train_sessions,1),(test_sessions,1),(embargo_sessions,0)):
        if type(v) is not int or v<lo:
            raise ValueError("INVALID_FOLD_CONFIG")
    horizon=panel["horizon"]
    samples=panel.get("prediction_samples",panel["samples"])
    end=len(panel["dates"])-horizon
    first=minimum_train_sessions+horizon+embargo_sessions+panel.get("sequence_length",1)-1
    # Samples are already sorted by signal session. Reuse one compact index
    # of date boundaries instead of scanning the full panel for each fold.
    offsets=[0]*(len(panel["dates"])+1)
    if isinstance(samples,CompactSamples):
        for i in range(len(offsets)):
            offsets[i]=bisect_left(samples.session,i)
    else:
        j=0
        for i in range(len(offsets)):
            while j<len(samples) and samples[j]["signal_index"]<i:
                j+=1
            offsets[i]=j
    if first>=end:
        raise ValueError("INSUFFICIENT_SESSIONS_FOR_FOLDS")
    for start in range(first,end,test_sessions):
        stop=min(start+test_sessions,end)
        cutoff=offsets[max(0,start-embargo_sessions-horizon)]
        if isinstance(samples,CompactSamples) and isinstance(panel['samples'],IndexedRows):
            valid=panel['samples'].indices
            train=IndexPrefix(valid,bisect_left(valid,cutoff))
        else:
            train=[i for i in range(cutoff) if samples[i]['y'] is not None]
        test=range(offsets[start],offsets[stop])
        if not train or not test:
            raise ValueError("EMPTY_PURGED_FOLD")
        yield dict(train=train,test=test,test_start_index=start,test_end_index=stop-1,
                   latest_training_label_end=samples[train[-1]]["label_end_index"])


def purged_folds(panel, **kwargs):
    """Compatibility API; production loop uses the lazy iterator instead."""
    return [dict(f,train=list(f["train"]),test=list(f["test"])) for f in iter_purged_folds(panel,**kwargs)]
