"""Synthetic point-in-time panel contracts; real data remains blocked."""
from __future__ import annotations
from datetime import date
from atlaspm.engine.contracts import finite, timestamp

HORIZONS = (1, 5, 10, 21, 63)


def build_panel(payload, horizon, sequence_length=1, *, compact=False):
    if type(sequence_length) is not int or not 1 <= sequence_length <= 252:
        raise ValueError('INVALID_SEQUENCE_LENGTH')
    if payload.get("mode") != "synthetic" or payload.get("synthetic_fixture") is not True:
        raise ValueError("REAL_TRAINING_DATA_NOT_APPROVED")
    if type(horizon) is not int or horizon not in HORIZONS:
        raise ValueError("INVALID_LABEL_HORIZON")
    features = payload["feature_names"]
    if not isinstance(features,list) or not features or len(set(features)) != len(features):
        raise ValueError("INVALID_FEATURE_SCHEMA")
    if any(not isinstance(f,str) or not f or f.lower().startswith(("label","future","target")) for f in features):
        raise ValueError("FORBIDDEN_FEATURE_NAME")
    sessions = payload["sessions"]
    dates, observations = [], []
    prior = None
    for row in sessions:
        day = date.fromisoformat(row["date"])
        close = timestamp(row["close_at"],"close_at")
        if close.date()!=day or (prior is not None and close<=prior) or (dates and str(day)<=dates[-1]):
            raise ValueError("SESSION_ORDER")
        prior = close
        dates.append(str(day))
        marks = {}
        for item in row["assets"]:
            sid = item["security_id"]
            if not isinstance(sid,str) or not sid.startswith("SYN:") or sid in marks:
                raise ValueError("INVALID_OR_DUPLICATE_SYNTHETIC_ID")
            if item.get("source")!="synthetic":
                raise ValueError("UNVERIFIED_SOURCE")
            available = timestamp(item["available_at"],"available_at")
            observed = timestamp(item["observed_at"],"observed_at")
            if not observed<=available<=close:
                raise ValueError("FEATURE_LOOKAHEAD")
            if set(item["features"])!=set(features):
                raise ValueError("FEATURE_SCHEMA_MISMATCH")
            marks[sid] = (finite(item["close"],"close",minimum=.000001),
                          [finite(item["features"][f],f) for f in features])
        if not marks:
            raise ValueError("EMPTY_SESSION")
        observations.append(marks)
    samples, prediction_samples, missing, incomplete_sequences = [], [], 0, 0
    for i in range(max(0,len(observations)-horizon)):
        marks=observations[i]
        for sid,(price,x) in sorted(marks.items()):
            # Eligibility for prediction depends only on the trailing feature window.
            if i+1 < sequence_length or any(sid not in observations[j]
                    for j in range(i-sequence_length+1,i+1)):
                incomplete_sequences += 1
                continue
            sequence = None if compact else [observations[j][sid][1] for j in range(i-sequence_length+1,i+1)]
            missing_path = any(sid not in observations[j] for j in range(i+1,i+horizon+1))
            y = None if missing_path else observations[i+horizon][sid][0]/price-1
            sample = dict(security_id=sid,signal_index=i,label_end_index=i+horizon,
                          date=dates[i],label_end_date=dates[i+horizon],x=x,sequence=sequence,y=y)
            if compact:
                del sample['sequence']
            prediction_samples.append(sample)
            if missing_path:
                missing += 1
            else:
                samples.append(sample)
    return dict(feature_names=features,dates=dates,samples=samples,horizon=horizon,
                prediction_samples=prediction_samples,sequence_length=sequence_length,
                **({'observations':observations} if compact and sequence_length>1 else {}),
                incomplete_sequences=incomplete_sequences,
                missing_label_paths=missing,unmatured_final_sessions=min(horizon,len(dates)),
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
        train=[i for i in range(cutoff) if samples[i]["y"] is not None]
        test=range(offsets[start],offsets[stop])
        if not train or not test:
            raise ValueError("EMPTY_PURGED_FOLD")
        yield dict(train=train,test=test,test_start_index=start,test_end_index=stop-1,
                   latest_training_label_end=samples[train[-1]]["label_end_index"])


def purged_folds(panel, **kwargs):
    """Compatibility API; production loop uses the lazy iterator instead."""
    return [dict(f,test=list(f["test"])) for f in iter_purged_folds(panel,**kwargs)]
