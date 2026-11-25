"""One research contract; descriptive evidence never becomes numeric alpha by coercion."""
from __future__ import annotations
from datetime import UTC
import re
from atlaspm.engine.contracts import timestamp, finite
from atlaspm.research.financial_events import FLAGS
from atlaspm.research.training_registry import digest

CATEGORIES=frozenset({"technical","fundamental","financial_statement","sec","news","sentiment",
                      "options","macro","industry","factor","regime","risk","analyst","credit","bond"})
KINDS=frozenset({"objective","quantitative_prediction","institutional_opinion","llm_inference"})
ASSETS=frozenset({"stock","etf","option","bond"})
FIELDS=frozenset({"security_id","asset_type","category","kind","source","model_version",
                  "evidence","generated_at","expires_at","horizon_days","expected_return",
                  "uncertainty_radius","risk_factors","invalidation","synthetic_fixture"})
EVIDENCE_FIELDS=frozenset({"sha256","available_at","received_at","source","rights_status"})


def _name(value, limit=160):
    if not isinstance(value,str) or not 1<=len(value)<=limit or not value.strip() or any(ord(c)<32 for c in value):
        raise ValueError("INVALID_RESEARCH_TEXT")
    return value


def _utc(value):
    return timestamp(value,"research_time").astimezone(UTC)


def validate_identity(sid,synthetic):
    sid=_name(sid)
    if type(synthetic) is not bool or (synthetic and not re.fullmatch(r"SYN:[A-Z0-9._-]{1,32}",sid)):
        raise ValueError("INVALID_SYNTHETIC_ID")
    if not synthetic and not re.fullmatch(r"(?:conid:[1-9][0-9]{0,18}|CIK:[0-9]{10}|ISIN:[A-Z]{2}[A-Z0-9]{10}|index:[A-Za-z0-9._-]+|macro:[A-Za-z0-9._-]+)",sid):
        raise ValueError("TYPED_SECURITY_ID_REQUIRED")
    return sid


def contract(raw, *, as_of, max_age_seconds=86400):
    if not isinstance(raw,dict) or set(raw)!=FIELDS:
        raise ValueError("INVALID_RESEARCH_CONTRACT_FIELDS")
    cutoff=_utc(as_of)
    age=finite(max_age_seconds,"max_age_seconds",minimum=0)
    synthetic=raw["synthetic_fixture"]
    validate_identity(raw["security_id"],synthetic)
    if raw["asset_type"] not in ASSETS or raw["category"] not in CATEGORIES or raw["kind"] not in KINDS:
        raise ValueError("INVALID_RESEARCH_CLASS")
    source,version=_name(raw["source"]),_name(raw["model_version"])
    gen,expires=_utc(raw["generated_at"]),_utc(raw["expires_at"])
    if not gen<=cutoff<=expires:
        raise ValueError("RESEARCH_NOT_VISIBLE_OR_EXPIRED")
    horizon=raw["horizon_days"]
    if type(horizon) is not int or not 1<=horizon<=730:
        raise ValueError("INVALID_RESEARCH_HORIZON")
    rows=raw["evidence"]
    if not isinstance(rows,list) or not 1<=len(rows)<=32:
        raise ValueError("BOUNDED_EVIDENCE_REQUIRED")
    evidence=[]
    known=[]
    for row in rows:
        if not isinstance(row,dict) or set(row)!=EVIDENCE_FIELDS or not isinstance(row["sha256"],str) or not re.fullmatch("[0-9a-f]{64}",row["sha256"]):
            raise ValueError("INVALID_EVIDENCE_CONTRACT")
        _name(row["source"])
        if row["rights_status"] not in {"synthetic_test_only","operator_attested_not_independently_verified","public_source_review_required"}:
            raise ValueError("UNSUPPORTED_RIGHTS_STATUS")
        if synthetic!=(row["rights_status"]=="synthetic_test_only") or (synthetic and row["source"]!="synthetic"):
            raise ValueError("EVIDENCE_FIXTURE_SCOPE_MISMATCH")
        receipt=_utc(row["received_at"])
        available=_utc(row["available_at"]) if row["available_at"] is not None else None
        if receipt>gen or (available is not None and available>receipt):
            raise ValueError("EVIDENCE_LOOKAHEAD")
        if available is not None:
            known.append(receipt)
        evidence.append({**row,"available_at":available.isoformat() if available else None,"received_at":receipt.isoformat()})
    if len({e["sha256"] for e in evidence})!=len(evidence):
        raise ValueError("DUPLICATE_RESEARCH_EVIDENCE")
    quality=len(known)==len(evidence)
    fresh=quality and all((cutoff-t).total_seconds()<=age for t in known) and all(
        (cutoff-_utc(e["available_at"])).total_seconds()<=age for e in evidence)
    ret,radius=raw["expected_return"],raw["uncertainty_radius"]
    if raw["kind"]!="quantitative_prediction":
        if ret is not None or radius is not None:
            raise ValueError("QUALITATIVE_EVIDENCE_NOT_NUMERIC_ALPHA")
    else:
        if ret is not None and abs(finite(ret,"return"))>1:
            raise ValueError("RETURN_OUTSIDE_RESEARCH_RANGE")
        if radius is not None and finite(radius,"radius",minimum=0)>1:
            raise ValueError("UNCERTAINTY_OUTSIDE_RANGE")
    for field in ("risk_factors","invalidation"):
        if not isinstance(raw[field],list) or not 1<=len(raw[field])<=16:
            raise ValueError("RESEARCH_RISK_AND_INVALIDATION_REQUIRED")
        for item in raw[field]:
            _name(item,512)
    # Nonfixture data never enter the synthetic decision path, even if caller
    # supplies a numerical forecast and apparent rights/availability.
    body={**raw,"source":source,"model_version":version,"evidence":evidence,
          "generated_at":gen.isoformat(),"expires_at":expires.isoformat()}
    return {**FLAGS,"contract":body,"research_id":digest(body),"data_complete":quality,
            "fresh":fresh,"data_available_at":max(known).isoformat() if quality else None,
            "direction":None if ret is None else "positive" if ret>0 else "negative" if ret<0 else "neutral",
            "signal_strength":abs(ret) if ret is not None else None,
            "numeric_eligible":synthetic and source=="synthetic" and quality and fresh and
                               raw["kind"]=="quantitative_prediction" and ret is not None and radius is not None,
            "calibrated_probability":None,"identity_verified":False}


def descriptive_report(report, *, security_id, asset_type, category, kind, as_of, evidence, horizon_days=1):
    """Adapt existing specialist output without converting sentiment into alpha."""
    if not isinstance(report,dict) or report.get("research_only") is not True or kind=="quantitative_prediction":
        raise ValueError("EXPECTED_DESCRIPTIVE_RESEARCH_REPORT")
    from datetime import timedelta
    stamp=_utc(as_of)
    return contract({"security_id":security_id,"asset_type":asset_type,"category":category,
        "kind":kind,"source":report.get("provider","local_research"),"model_version":report.get("model","unknown"),
        "evidence":evidence,"generated_at":stamp.isoformat(),"expires_at":(stamp+timedelta(days=1)).isoformat(),
        "horizon_days":horizon_days,"expected_return":None,"uncertainty_radius":None,
        "risk_factors":["source and interpretation not independently verified"],
        "invalidation":["new evidence or expired source"],"synthetic_fixture":False},as_of=as_of)
