"""XpressAPI Agency Ratings: official issuer/issue GET, no guessed timestamps.

Local normalized observations preserve original rating scale/type/debt basis.
Comparison requires an explicitly reviewed common basis; no default mapping
from issuer to issue or rating to probability of default.
"""
from __future__ import annotations

from datetime import date
from threading import Lock
import time
from urllib.parse import urlencode
import re
from atlaspm.data.api_gateway import DEFAULT_GATEWAY, GatewayError
from atlaspm.data.provider_policy import local_rights

BASE = "https://xpressapi.marketplace.spglobal.com/agencyratings/api/v1/"
_ID_TYPES = {"issuer":{"CIQCOMPANYID":r"[0-9]{1,20}", "LEI":r"[A-Z0-9]{20}",
                       "CUSIP6":r"[A-Z0-9]{6}", "GVKEY":r"[0-9]{6}"},
             "issue":{"CUSIP9":r"[A-Z0-9]{9}", "ISIN":r"[A-Z]{2}[A-Z0-9]{10}"}}
_AGENCIES = ("S&P + MOODY'S", "S&P + FITCH", "MOODY'S + FITCH", "S&P + MOODY'S + FITCH")
_MAP = (
    ("sp_ratings","s&pRatingsData","s&pData","ratingType","ratingDate","ratingActionWord",
     "creditWatchOutlookValue","orgDebtType","debtType"),
    ("moodys","moodysRatingsData","moodysData","ratingTypeText","ratingDate","ratingDirectionText",
     "watchlistDirectionText","ratingClassText","currencyCapdText"),
    ("fitch","fitchRatingsData","fitchData","ratingTypeName","ratingEffectiveDate","ratingAction",
     "ratingAlertDescription","issueDebtLevelDescription","ratingSolicitationDescription"),
)
_FLAGS = {"research_only":True,"point_in_time_verified":False,"external_ai_allowed":False,
          "approved_for_model_training":False,"approved_for_backtest":False,
          "broker_order_submission_enabled":False,"live_execution_enabled":False}


def _text(v):
    if v is None:
        return None
    if not isinstance(v,str) or len(v)>512:
        raise GatewayError("Invalid rating text")
    return v


def fetch_ratings(scope, identifier_type, identifiers, *, as_of, start_date=None, agencies=_AGENCIES[-1], gateway=DEFAULT_GATEWAY, session=None):
    local_rights("xpressapi")
    pattern = _ID_TYPES.get(scope,{}).get(identifier_type)
    if (pattern is None or not isinstance(identifiers,(tuple,list)) or not 1<=len(identifiers)<=50
            or any(not isinstance(x,str) or not re.fullmatch(pattern,x) for x in identifiers)
            or len(set(identifiers))!=len(identifiers) or agencies not in _AGENCIES):
        raise ValueError("Invalid Agency Ratings request")
    day=date.fromisoformat(as_of)
    if day>date.today():
        raise ValueError("Future rating query")
    params={"identifierType":identifier_type,"identifierValues":",".join(identifiers),
            "creditRatingAgency":agencies}
    if start_date is None:
        params["asOfDate"]=day.isoformat()
    else:
        first=date.fromisoformat(start_date)
        if not first<=day or (day-first).days>366:
            raise ValueError("Historical rating request capped at 367 calendar days")
        params.update(startDate=first.isoformat(),endDate=day.isoformat())
    query=urlencode(params)
    if session is not None and session.gateway is not gateway:
        raise ValueError("Token session must share the same gateway and budget")
    token=session.access() if session is not None else None
    response=gateway.fetch("xpressapi",BASE+scope+"?"+query,key=token)
    body=response.data()
    if any(body.get(k) for k in ("error","spRatingsCapabilityError","moodyRatingsCapabilityError","fitchRatingsCapabilityError")):
        raise GatewayError("Agency Ratings provider/capability error")
    groups=body.get("results")
    if not isinstance(groups,list) or len(groups)>50:
        raise GatewayError("Invalid Agency Ratings results")
    rows=[]
    seen=set()
    for group in groups:
        if not isinstance(group,dict) or group.get("errorMessage"):
            raise GatewayError("Agency Ratings identifier error")
        ident=group.get("identifierValue")
        if group.get("identifierType")!=identifier_type or ident not in identifiers or ident in seen:
            raise GatewayError("Agency Ratings identifier mismatch")
        seen.add(ident)
        for agency,outer,inner,typ,when,action,watch,basis1,basis2 in _MAP:
            entries=group.get(outer) or []
            if not isinstance(entries,list) or len(entries)>1000:
                raise GatewayError("Invalid agency group")
            for entry in entries:
                if not isinstance(entry,dict) or entry.get("errorMessage"):
                    raise GatewayError("Agency record error")
                ratings=entry.get(inner) or []
                if not isinstance(ratings,list) or len(ratings)>1000:
                    raise GatewayError("Invalid agency ratings")
                for row in ratings:
                    if not isinstance(row,dict) or len(rows)>=10000:
                        raise GatewayError("Rating row budget exceeded")
                    value, kind, effective=(_text(row.get(k)) for k in ("ratingValue",typ,when))
                    if not value or not kind or not effective:
                        raise GatewayError("Missing rating type/value/date")
                    # Date-only validation; timezone/publication semantics differ.
                    if date.fromisoformat(effective[:10])>day:
                        raise GatewayError("Rating effective date exceeds query")
                    rows.append({"agency":agency,"identifier_type":identifier_type,"identifier":ident,
                                 "scope":scope,"rating":value,"rating_type":kind,"effective_raw":effective,
                                 "action_raw":_text(row.get(action)),"watch_outlook_raw":_text(row.get(watch)),
                                 "basis_raw":[_text(row.get(basis1)),_text(row.get(basis2))],
                                 "agency_entity_id":entry.get("rxEntityId",entry.get("moodysOrgId",entry.get("fitchIssuerId"))),
                                 "agency_issue_id":entry.get("rxMaturityId",entry.get("issueId")),
                                 "published_at":None,"first_available_at":None})
    return {**_FLAGS,"provider":"xpressapi","model":"agency_ratings_schema_v1",
            "as_of_date":day.isoformat(),"history_start_date":start_date,"transport":response.evidence(),"ratings":rows,
            "missing_identifiers":sorted(set(identifiers)-seen),
            "coverage_complete":False,"total_pages_reported":body.get("totalPages"),
            "cross_agency_basis_verified":False}


# Only use these comparable global long-term scales after explicit basis review.
_SP="AAA AA+ AA AA- A+ A A- BBB+ BBB BBB- BB+ BB BB- B+ B B- CCC+ CCC CCC- CC C".split()
_MOODY="Aaa Aa1 Aa2 Aa3 A1 A2 A3 Baa1 Baa2 Baa3 Ba1 Ba2 Ba3 B1 B2 B3 Caa1 Caa2 Caa3 Ca C".split()


def compare_reviewed(rows, *, common_basis_reviewed=False):
    if common_basis_reviewed is not True or not isinstance(rows,list) or not 1<=len(rows)<=5:
        raise ValueError("Explicit comparable global long-term rating basis review required")
    keys={(r["scope"],r["identifier_type"],r["identifier"]) for r in rows}
    if len(keys)!=1:
        raise ValueError("Cannot compare different obligations")
    grades={}
    for row in rows:
        agency=row["agency"]
        if agency not in {"sp_ratings","moodys","fitch"} or agency in grades:
            raise ValueError("One comparable observation per supported agency required")
        scale=_MOODY if agency=="moodys" else _SP
        if row["rating"] not in scale:
            raise ValueError("Unmapped/withdrawn/default rating requires separate review")
        grades[agency]=scale.index(row["rating"])
    return {**_FLAGS,"notch_indices":grades,"notch_range":max(grades.values())-min(grades.values()),
            "investment_grade_split":min(grades.values())<=9<max(grades.values()),
            "default_probability":None,"basis_operator_reviewed":True}


class XpressSession:
    """Opt-in in-memory login/refresh, shared transport budget; no disk or repr secrets."""
    def __init__(self, *, gateway=DEFAULT_GATEWAY, clock=time.monotonic):
        self.gateway=gateway
        self._clock=clock
        self._lock=Lock()
        self._access=None
        self._refresh=None
        self._expires=0.0

    def access(self):
        with self._lock:
            if self._access is not None and self._clock()<self._expires:
                return self._access
            access,refresh,ttl=self.gateway.xpress_token(refresh_token=self._refresh)
            self._access,self._refresh=access,refresh
            self._expires=self._clock()+max(0,ttl-min(30,ttl/2))
            return access

    def clear(self):
        with self._lock:
            self._access=self._refresh=None
            self._expires=0.0


def review_actions(report):
    """Classify explicit vendor action text; never infer default from withdrawal."""
    results=[]
    mapping={"upgrade":"upgrade","downgrade":"downgrade","withdrawal":"withdrawal",
             "withdrawn":"withdrawal","affirmed":"affirmation","affirmation":"affirmation",
             "new rating":"new_rating","new":"new_rating","default":"default"}
    for row in report["ratings"]:
        action=mapping.get((row.get("action_raw") or "").strip().casefold(),"unclassified")
        results.append({"agency":row["agency"],"identifier":row["identifier"],"scope":row["scope"],
                        "rating":row["rating"],"rating_type":row["rating_type"],
                        "effective_raw":row["effective_raw"],"action":action,
                        "watch_outlook_raw":row.get("watch_outlook_raw"),
                        "credit_review_requested":action in {"downgrade","withdrawal","default"},
                        "default_probability":None,"cross_asset_price_impact":None})
    return {**_FLAGS,"model":"credit_action_rules_v1","transport":report["transport"],"actions":results}


def archive_observations(report, *, path=None):
    """Retain bounded authorized normalized rating revisions, not raw wire data.

    Existing local research-state writer is reused. Receipt is the local
    archival time, not the effective date or independently proven publication.
    No automatic retention and no formal dataset admission.
    """
    from datetime import UTC,datetime
    from pathlib import Path
    from atlaspm.research.event_queue import _transaction
    from atlaspm.research.financial_events import _time
    from atlaspm.research.training_registry import digest
    local_rights("xpressapi",retention=True)
    if (not isinstance(report,dict) or report.get("provider")!="xpressapi" or
        any(report.get(k) is not v for k,v in _FLAGS.items())):
        raise ValueError("INVALID_RATING_ARCHIVE_REPORT")
    transport=report.get("transport",{})
    source_hash=transport.get("sha256")
    received=datetime.now(UTC)
    fetched=_time(transport["retrieved_at_utc"])
    if (transport.get("provider")!="xpressapi" or not isinstance(source_hash,str) or
        not re.fullmatch(r"[0-9a-f]{64}",source_hash) or fetched>received):
        raise ValueError("INVALID_RATING_ARCHIVE_TRANSPORT")
    rows=report.get("ratings")
    if not isinstance(rows,list) or len(rows)>1000:
        raise ValueError("RATING_ARCHIVE_BATCH_LIMIT")
    required={"agency","identifier_type","identifier","scope","rating","rating_type","effective_raw",
              "action_raw","watch_outlook_raw","basis_raw","agency_entity_id","agency_issue_id",
              "published_at","first_available_at"}
    for row in rows:
        if (not isinstance(row,dict) or set(row)!=required or row["agency"] not in {"sp_ratings","moodys","fitch"} or
            row["published_at"] is not None or row["first_available_at"] is not None):
            raise ValueError("INVALID_RATING_ARCHIVE_ROW")
        pattern=_ID_TYPES.get(row["scope"],{}).get(row["identifier_type"])
        if pattern is None or not isinstance(row["identifier"],str) or not re.fullmatch(pattern,row["identifier"]):
            raise ValueError("INVALID_RATING_ARCHIVE_IDENTITY")
        if not _text(row["rating"]) or not _text(row["rating_type"]) or not _text(row["effective_raw"]):
            raise ValueError("MISSING_RATING_FIELDS")
        if date.fromisoformat(row["effective_raw"][:10])>fetched.date():
            raise ValueError("FUTURE_RATING_EFFECTIVE_DATE")
    def write(items):
        added=0
        for row in rows:
            key=digest(row)
            if key not in items:
                items[key]={"rating":row,"source_sha256":source_hash,
                            "provider_retrieved_at":fetched.isoformat(),"archived_at":received.isoformat()}
                added+=1
        return {**_FLAGS,"added":added,"retained":len(items),"coverage_complete":False}
    return _transaction(path or Path("local_data/runs/credit_rating_observations.json"),write)


def rating_history(*, identifier_type, identifier, as_of, path=None):
    from datetime import UTC,datetime
    from pathlib import Path
    from atlaspm.research.event_queue import _read_state
    from atlaspm.research.financial_events import _time
    from atlaspm.research.training_registry import digest
    local_rights("xpressapi",retention=True)
    cutoff=_time(as_of)
    if cutoff>datetime.now(UTC):
        raise ValueError("FUTURE_RATING_HISTORY_CUTOFF")
    rows=[]
    for key,item in _read_state(path or Path("local_data/runs/credit_rating_observations.json")).items():
        row=item["rating"]
        if digest(row)!=key or _time(item["provider_retrieved_at"])>_time(item["archived_at"]):
            raise ValueError("RATING_ARCHIVE_INTEGRITY_FAILURE")
        if row["identifier_type"]==identifier_type and row["identifier"]==identifier and _time(item["archived_at"])<=cutoff:
            rows.append({**item,"revision_sha256":key})
    return {**_FLAGS,"ratings":sorted(rows,key=lambda x:(x["archived_at"],x["revision_sha256"])),
            "as_of":cutoff.isoformat(),"coverage_complete":False,"publication_time_verified":False}


def main():
    from argparse import ArgumentParser
    import json
    parser=ArgumentParser(description=__doc__)
    parser.add_argument("--scope",choices=("issuer","issue"),required=True)
    parser.add_argument("--identifier-type",required=True)
    parser.add_argument("--identifier",action="append",required=True)
    parser.add_argument("--as-of",required=True)
    parser.add_argument("--start-date")
    parser.add_argument("--login",action="store_true",help="Explicit in-memory login; otherwise use XPRESSAPI_TOKEN")
    parser.add_argument("--retain",action="store_true",help="Explicitly authorized local rating history")
    args=parser.parse_args()
    session=XpressSession() if args.login else None
    try:
        report=fetch_ratings(args.scope,args.identifier_type,args.identifier,as_of=args.as_of,
                             start_date=args.start_date,session=session)
        if args.retain:
            report["retention"]=archive_observations(report)
        # No proprietary rating values or private identifiers on stdout.
        summary={k:v for k,v in report.items() if k not in {"ratings","missing_identifiers"}}
        summary.update(rating_count=len(report["ratings"]),missing_identifier_count=len(report["missing_identifiers"]))
        print(json.dumps(summary,indent=2))
    finally:
        if session:
            session.clear()


if __name__=="__main__":
    main()
