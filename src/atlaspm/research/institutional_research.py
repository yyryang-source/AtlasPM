"""Licensed institutional report intake and local, evidence-bound extraction.

No external tools, OCR, model egress or order calls. Input documents remain
in the existing gitignored local_data tree; only bounded revision metadata
and evidence quotes are indexed in the existing research runs area.
"""
from __future__ import annotations

from datetime import UTC, datetime, date
from decimal import Decimal
from math import exp, log
from statistics import mean, pstdev
from hashlib import sha256
import json
import os
from pathlib import Path
import re
from urllib.parse import urlsplit

from atlaspm.data.market_db import private_path
from atlaspm.data.provider_policy import RESEARCH_FIRMS, research_rights
from atlaspm.data.sec_download import _atomic_write
from atlaspm.research.financial_events import FLAGS, _digest, _time

ARCHIVE=Path("local_data/runs/institutional_reports.jsonl")
MAX_DOC=8*1024*1024
MAX_ARCHIVE=16*1024*1024
MAX_RECORDS=5000
EVIDENCE_TYPES=frozenset({"original_report","news_relay","rating_summary","ai_inference"})
METRICS={"target price":"target_price","eps":"eps","revenue":"revenue","ebitda":"ebitda",
         "index forecast":"index_forecast","rate forecast":"rate_forecast",
         "macro forecast":"macro_forecast"}
NARRATIVES={"investment thesis","risks","bull case","bear case","valuation",
            "industry outlook","asset allocation","macro outlook"}
_NUMERIC=re.compile(r"^(Target Price|EPS|Revenue|EBITDA|Index Forecast|Rate Forecast|Macro Forecast)"
                    r"\s*\(([A-Z]{3}|PCT|INDEX),\s*(\d{4}-\d{2}-\d{2}),\s*([A-Za-z0-9._-]{1,32})\)"
                    r"\s*:\s*(-?[0-9]{1,18}(?:\.[0-9]{1,8})?)$",re.I)


def _token(value):
    if not isinstance(value,str) or not re.fullmatch(r"[A-Za-z0-9._:/+-]{1,128}",value):
        raise ValueError("Invalid research identifier")
    return value


def _subject(value):
    value=_token(value)
    if not re.fullmatch(r"(?:conid:[1-9][0-9]{0,18}|CIK:[0-9]{10}|ISIN:[A-Z]{2}[A-Z0-9]{10}|index:[A-Za-z0-9._-]+|macro:[A-Za-z0-9._-]+)",value):
        raise ValueError("Stable typed subject required; ticker alone is insufficient")
    return value


def _metadata(row, received):
    fields={"provider","firm","analyst_id","report_id","subject","evidence_type",
            "published_at","first_available_at","source_url","document"}
    if not isinstance(row,dict) or set(row)!=fields:
        raise ValueError("Unexpected institutional metadata fields")
    provider=row["provider"]
    research_rights(provider)
    if row["firm"] not in RESEARCH_FIRMS or row["evidence_type"] not in EVIDENCE_TYPES:
        raise ValueError("Unrecognized firm or evidence type")
    pub=_time(row["published_at"])
    avail=_time(row["first_available_at"]) if row["first_available_at"] is not None else None
    if pub>received or (avail and not pub<=avail<=received):
        raise ValueError("Invalid report time chronology")
    url=row["source_url"]
    if url is not None:
        if not isinstance(url,str) or len(url)>2048 or any(ord(ch)<33 for ch in url):
            raise ValueError("Invalid source URL")
        p=urlsplit(url)
        if p.scheme!="https" or not p.hostname or p.username or p.password or p.query or p.fragment or p.port not in (None,443):
            raise ValueError("Use stable HTTPS URL without credentials or query")
    path=private_path(Path(row["document"]),exists=True)
    return {**row,"analyst_id":_token(row["analyst_id"]),"report_id":_token(row["report_id"]),
            "subject":_subject(row["subject"]),"published_at":pub.isoformat(),
            "first_available_at":avail.isoformat() if avail else None,
            "document":path.relative_to(Path.cwd().resolve()).as_posix()}


def _read_document(path):
    with path.open("rb") as f:
        raw=f.read(MAX_DOC+1)
    if len(raw)>MAX_DOC:
        raise ValueError("Document exceeds 8 MiB")
    digest=sha256(raw).hexdigest()
    if path.suffix.lower()==".txt":
        pages=[raw.decode("utf-8-sig")]
    elif path.suffix.lower()==".pdf":
        if not raw.startswith(b"%PDF-"):
            raise ValueError("Invalid PDF signature")
        from io import BytesIO
        try:
            from pypdf import PdfReader
        except ImportError:
            raise ValueError("Install optional institutional-pdf dependency for PDF intake") from None
        reader=PdfReader(BytesIO(raw),strict=True)
        if reader.is_encrypted or len(reader.pages)>100:
            raise ValueError("Encrypted or over-100-page PDF not supported")
        pages=[]
        total=0
        for page in reader.pages:
            # pypdf has no process-level decompression memory guarantee.
            text=page.extract_text() or ""
            total+=len(text)
            if total>500000:
                raise ValueError("Extracted PDF text exceeds limit")
            pages.append(text)
    else:
        raise ValueError("Only authorized UTF-8 text or PDF supported")
    if sum(map(len,pages))>500000 or not any(t.strip() for t in pages):
        raise ValueError("Empty, scanned or oversized document")
    return digest,pages


def extract(pages):
    """Strict tagged-line extraction, not a universal report/table parser.

    Numerical values must be explicit with currency/unit, horizon and basis.
    No inferred estimates from free prose, and no automated instructions.
    """
    claims=[]
    keys=set()
    for page, text in enumerate(pages,1):
        for line in text.splitlines():
            line=line.strip()
            if len(line)>2000:
                continue
            match=_NUMERIC.fullmatch(line)
            claim=None
            if match:
                label,unit,period,basis,value=match.groups()
                metric=METRICS[label.lower()]
                date.fromisoformat(period)
                number=Decimal(value)
                unit=unit.upper()
                if (not number.is_finite() or abs(number)>Decimal("1e18") or
                        (metric=="target_price" and number<=0) or
                        (metric in {"target_price","eps","revenue","ebitda"} and unit in {"PCT","INDEX"}) or
                        (metric=="rate_forecast" and unit!="PCT") or
                        (metric=="index_forecast" and unit!="INDEX")):
                    raise ValueError("Invalid explicit forecast")
                claim={"metric":metric,"unit":unit,"period":period,"basis":basis,"value":str(number)}
            elif re.fullmatch(r"Rating:\s*(Buy|Hold|Sell)",line,re.I):
                claim={"metric":"rating","unit":None,"period":None,"basis":"explicit_buy_hold_sell",
                       "value":line.split(":",1)[1].strip().title()}
            elif re.fullmatch(r"Rating Action:\s*(Upgrade|Downgrade|Initiation|Reiteration|Withdrawal)",line,re.I):
                claim={"metric":"rating_action","unit":None,"period":None,"basis":None,
                       "value":line.split(":",1)[1].strip().lower()}
            elif ":" in line and line.split(":",1)[0].casefold() in NARRATIVES:
                label,value=line.split(":",1)
                if value.strip():
                    claim={"metric":label.lower().replace(" ","_"),"unit":None,"period":None,
                           "basis":None,"value":value.strip()}
            if claim is None:
                continue
            key=(claim["metric"],claim["unit"],claim["period"],claim["basis"])
            if key in keys:
                # Repeated or conflicting tables require operator normalization;
                # never choose a forecast value by page order.
                prior=next(c for c in claims if (c["metric"],c["unit"],c["period"],c["basis"])==key)
                if prior["value"]!=claim["value"]:
                    raise ValueError("Conflicting explicit claim in one report")
                continue
            keys.add(key)
            claims.append({**claim,"page":page,"quote":line,"quote_sha256":sha256(line.encode()).hexdigest()})
            if len(claims)>128:
                raise ValueError("Report claim limit exceeded")
    return claims


def _records(path):
    if not path.exists():
        return []
    if path.stat().st_size>MAX_ARCHIVE:
        raise ValueError("Research archive byte budget exceeded")
    rows=[]
    with path.open(encoding="utf-8") as f:
        for line in f:
            row=json.loads(line)
            payload={k:v for k,v in row.items() if k not in {"revision","received_at","receipt_sha256"}}
            if _digest(payload)!=row.get("revision"):
                raise ValueError("Research revision integrity failure")
            received=_time(row["received_at"])
            if (row.get("receipt_sha256")!=_digest([row["revision"],row["received_at"]]) or
                    received>datetime.now(UTC)):
                raise ValueError("Research receipt integrity failure")
            rows.append(row)
            if len(rows)>MAX_RECORDS:
                raise ValueError("Research record budget exceeded")
    return rows


def ingest(metadata_file, *, archive=ARCHIVE):
    source=private_path(Path(metadata_file),exists=True)
    with source.open("rb") as f:
        raw=f.read(65537)
    if len(raw)>65536:
        raise ValueError("Metadata exceeds 64 KiB")
    received=datetime.now(UTC)
    meta=_metadata(json.loads(raw),received)
    digest,pages=_read_document(private_path(Path(meta["document"]),exists=True))
    claims=extract(pages)
    # Duplicate content at the same report identity is handled below.
    payload={**meta,"document_sha256":digest,"claims":claims}
    revision=_digest(payload)
    path=private_path(Path(archive))
    path.parent.mkdir(parents=True,exist_ok=True)
    lock=path.with_suffix(".lock")
    fd=os.open(lock,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    os.close(fd)
    try:
        rows=_records(path)
        identity=(meta["provider"],meta["firm"],meta["analyst_id"],meta["report_id"],meta["subject"])
        for row in rows:
            if row["revision"]==revision:
                return {**FLAGS,"status":"DUPLICATE","revision":revision,"claim_count":len(claims)}
            other=tuple(row[k] for k in ("provider","firm","analyst_id","report_id","subject"))
            if other==identity and row["document_sha256"]==digest:
                if any(row[k]!=meta[k] for k in ("published_at","first_available_at","evidence_type")):
                    raise ValueError("Same document with conflicting provenance")
                return {**FLAGS,"status":"DUPLICATE_CONTENT","revision":row["revision"],"claim_count":len(row["claims"])}
        rows.append({**payload,"revision":revision,"received_at":received.isoformat(),
                     "receipt_sha256":_digest([revision,received.isoformat()])})
        if len(rows)>MAX_RECORDS:
            raise ValueError("Research record budget exceeded")
        body=("\n".join(json.dumps(row,ensure_ascii=False,sort_keys=True,allow_nan=False) for row in rows)+"\n").encode()
        if len(body)>MAX_ARCHIVE:
            raise ValueError("Research archive byte budget exceeded")
        _atomic_write(path,lambda f:f.write(body))
    finally:
        lock.unlink(missing_ok=True)
    return {**FLAGS,"status":"IMPORTED","revision":revision,"claim_count":len(claims),
            "rights_status":"operator_attested_not_independently_verified",
            "parser":"explicit_tagged_lines_v1","document_sha256":digest}


def visible(*, as_of, subject, archive=ARCHIVE, all_revisions=False):
    """Use actual local receipt and stated availability; do not backdate imports."""
    cutoff=_time(as_of)
    _subject(subject)
    if cutoff>datetime.now(UTC):
        raise ValueError("Future research cutoff")
    selected={}
    for row in _records(private_path(Path(archive))):
        if row["subject"]!=subject or _time(row["received_at"])>cutoff:
            continue
        research_rights(row["provider"])
        key=tuple(row[k] for k in ("provider","firm","analyst_id","report_id","subject"))
        if all_revisions:
            key=(*key,row["revision"])
        prior=selected.get(key)
        if prior and prior["received_at"]==row["received_at"] and prior["revision"]!=row["revision"]:
            raise ValueError("Conflicting report receipt")
        if not prior or prior["received_at"]<row["received_at"]:
            selected[key]=row
    rows=[]
    for row in selected.values():
        if row["first_available_at"] is not None and _time(row["first_available_at"])>cutoff:
            continue
        meta={k:row[k] for k in ("provider","firm","analyst_id","report_id","subject","evidence_type",
                                  "published_at","first_available_at","source_url","document")}
        if _metadata(meta,_time(row["received_at"]))!=meta:
            raise ValueError("Stored research metadata invalid")
        digest,pages=_read_document(private_path(Path(row["document"]),exists=True))
        if digest!=row["document_sha256"] or extract(pages)!=row["claims"]:
            raise ValueError("Source report content or parser evidence changed")
        rows.append(row)
    return sorted(rows,key=lambda r:(r["published_at"],r["received_at"],r["revision"]))


_RATING={"Buy":1,"Hold":0,"Sell":-1}


def _eligible(row, cutoff, age_days):
    return (row["evidence_type"]=="original_report" and row["first_available_at"] is not None
            and max(_time(row["first_available_at"]),_time(row["received_at"]))<=cutoff
            and 0<=(cutoff-_time(row["published_at"])).total_seconds()<=age_days*86400)


def _snapshot(rows, cutoff, age_days, selectors):
    """Latest explicit claim per reviewed firm/analyst identity and exact basis."""
    chosen={}
    for row in rows:
        if not _eligible(row,cutoff,age_days):
            continue
        actor=(row["firm"],row["analyst_id"])
        rank=(_time(row["published_at"]),_time(row["received_at"]))
        for claim in row["claims"]:
            metric=claim["metric"]
            if metric not in selectors:
                continue
            if tuple(claim[k] for k in ("unit","period","basis"))!=selectors[metric]:
                continue
            key=(actor,metric)
            prior=chosen.get(key)
            if prior and prior[0]==rank and prior[1]["value"]!=claim["value"]:
                raise ValueError("Conflicting same-time analyst estimates")
            if not prior or prior[0]<rank:
                chosen[key]=(rank,claim,row["receipt_sha256"])
    return chosen


def _paired(current, previous, metric, *, relative=False, positive=False):
    result=[]
    for key,(_,claim,_) in current.items():
        if key[1]!=metric or key not in previous:
            continue
        old=previous[key][1]["value"]
        if metric=="rating":
            result.append(_RATING[claim["value"]]-_RATING[old])
        else:
            value,base=Decimal(claim["value"]),Decimal(old)
            if relative and (base==0 or (positive and base<=0)):
                continue
            result.append(float((value-base)/abs(base) if relative else value-base))
    return result


def _metric_values(snapshot, metric):
    return [float(c["value"]) for (_,m),(_,c,_) in snapshot.items() if m==metric]


def _avg(values):
    return mean(values) if values else None


def factors(*, subject, as_of, previous_as_of, unit, period, basis,
            max_age_days=180, half_life_days=30, archive=ARCHIVE):
    """Exploratory point-in-time-shaped analyst factors, not certified PIT.

    Identical metric scope/period/basis at both cutoffs. Current/previous
    report versions are independently loaded so later corrections never
    erase the earlier visible snapshot. No pricing or training admission.
    """
    now,prior=_time(as_of),_time(previous_as_of)
    if (not prior<now or type(max_age_days) is not int or type(half_life_days) is not int
            or not 1<=max_age_days<=730 or not 1<=half_life_days<=365):
        raise ValueError("Invalid factor cutoffs/age policy")
    if not isinstance(unit,str) or not re.fullmatch(r"[A-Z]{3}",unit) or unit in {"PCT","INDEX"}:
        raise ValueError("Expected common currency")
    if date.fromisoformat(period)<now.date():
        raise ValueError("Forecast horizon already expired")
    if not isinstance(basis,str) or not re.fullmatch(r"[A-Za-z0-9._-]{1,32}",basis):
        raise ValueError("Expected comparable forecast basis")
    selectors={m:(unit,period,basis) for m in ("target_price","eps","revenue","ebitda")}
    selectors["rating"]=(None,None,"explicit_buy_hold_sell")
    current=_snapshot(visible(as_of=as_of,subject=subject,archive=archive),now,max_age_days,selectors)
    previous=_snapshot(visible(as_of=previous_as_of,subject=subject,archive=archive),prior,max_age_days,selectors)
    ratings=[_RATING[c["value"]] for (_,m),(_,c,_) in current.items() if m=="rating"]
    targets=_metric_values(current,"target_price")
    eps=_metric_values(current,"eps")
    revenue=_metric_values(current,"revenue")
    rating_changes=_paired(current,previous,"rating")
    target_changes=_paired(current,previous,"target_price",relative=True,positive=True)
    eps_changes=_paired(current,previous,"eps",relative=True)
    revenue_changes=_paired(current,previous,"revenue",relative=True)
    actors={a for (a,m) in current if m=="rating"}
    prior_actors={a for (a,m) in previous if m=="rating"}
    # Compare new matched revisions to prior matched-peer consensus; no market
    # surprise claim, since price expectations are not independently observed.
    surprise=[]
    for key,(_,c,_) in current.items():
        if key[1]!="eps" or key not in previous or c["value"]==previous[key][1]["value"]:
            continue
        peers=[Decimal(pc["value"]) for pk,(_,pc,_) in previous.items()
               if pk[1]=="eps" and pk[0]!=key[0]]
        if peers:
            baseline=sum(peers)/len(peers)
            if baseline:
                surprise.append(float((Decimal(c["value"])-baseline)/abs(baseline)))
    weighted=[]
    for (_,m),(rank,c,_) in current.items():
        if m=="rating":
            weight=exp(-log(2)*(now-rank[0]).total_seconds()/86400/half_life_days)
            weighted.append(weight*_RATING[c["value"]])
    values={"analyst_rating_momentum":_avg(rating_changes),
            "target_price_revision":_avg(target_changes),
            "target_price_dispersion":pstdev(targets)/mean(targets) if len(targets)>=2 else None,
            "eps_revision_momentum":_avg(eps_changes),
            "revenue_revision_momentum":_avg(revenue_changes),
            "analyst_consensus":_avg(ratings),
            "analyst_disagreement":pstdev(ratings) if len(ratings)>=2 else None,
            "analyst_coverage_change":len(actors)-len(prior_actors),
            "institutional_sentiment":_avg(ratings),
            "research_event_surprise":_avg(surprise),
            "analyst_historical_accuracy":None,
            "recommendation_decay":_avg(weighted)}
    return {**FLAGS,"subject":subject,"as_of":now.isoformat(),"previous_as_of":prior.isoformat(),
            "model":"institutional_factors_v1","values":values,"unit":unit,"period":period,"basis":basis,
            "sample_counts":{"ratings":len(ratings),"target_prices":len(targets),"eps":len(eps),"revenue":len(revenue),
                             "paired_ratings":len(rating_changes),"paired_targets":len(target_changes),
                             "paired_eps":len(eps_changes),"paired_revenue":len(revenue_changes),
                             "eps_peer_surprise":len(surprise),"previous_ratings":len(prior_actors)},
            "consensus_values":{"target_price":_avg(targets),"eps":_avg(eps),"revenue":_avg(revenue)},
            "evidence_receipts":sorted({v[2] for snapshot in (current,previous) for v in snapshot.values()}),
            "half_life_days":half_life_days,"sentiment_is_rating_proxy":True,
            "surprise_is_prior_peer_eps_proxy":True,"identity_and_basis_independently_verified":False,
            "statistically_validated":False,"directional_signal":None}


def evaluate_forecasts(*, subject, as_of, outcomes_file, archive=ARCHIVE):
    """Descriptive forecast errors only, no return backtest or promotion.

    Outcomes are locally reviewed normalized observations with exact
    metric/currency/period/basis, and are available only after horizon ends.
    Select one first forecast per actor/target/horizon bucket to limit repeated
    revisions. No historical weights until independent sample validation.
    """
    cutoff=_time(as_of)
    rows=visible(as_of=as_of,subject=subject,archive=archive,all_revisions=True)
    with private_path(Path(outcomes_file),exists=True).open("rb") as handle:
        raw=handle.read(1024*1024+1)
    if len(raw)>1024*1024:
        raise ValueError("Outcome file too large")
    outcomes=json.loads(raw)
    if not isinstance(outcomes,list) or len(outcomes)>2000:
        raise ValueError("Invalid outcome list")
    actual={}
    for item in outcomes:
        if not isinstance(item,dict) or set(item)!={"subject","metric","unit","period","basis","value","observed_at","evidence_sha256"}:
            raise ValueError("Invalid outcome schema")
        if item["subject"]!=subject:
            raise ValueError("Outcome subject mismatch")
        period=date.fromisoformat(item["period"])
        observed=_time(item["observed_at"])
        if not re.fullmatch(r"[0-9a-f]{64}",item["evidence_sha256"]):
            raise ValueError("Outcome evidence digest required")
        if observed.date()<=period:
            raise ValueError("Outcome predates completed forecast horizon")
        if observed>cutoff:
            continue
        value=item["value"]
        if not isinstance(value,str) or not re.fullmatch(r"-?[0-9]{1,18}(?:\.[0-9]{1,8})?",value):
            raise ValueError("Invalid outcome value")
        key=tuple(item[k] for k in ("metric","unit","period","basis"))
        if key in actual:
            raise ValueError("Ambiguous outcome revision; provide one reviewed vintage")
        actual[key]=(Decimal(value),item["evidence_sha256"])
    selected={}
    eligible_count=0
    for row in rows:
        if row["evidence_type"]!="original_report" or row["first_available_at"] is None:
            continue
        known=max(_time(row["received_at"]),_time(row["first_available_at"]))
        for claim in row["claims"]:
            if claim["metric"] not in METRICS.values():
                continue
            key=tuple(claim[k] for k in ("metric","unit","period","basis"))
            horizon=(date.fromisoformat(claim["period"])-known.date()).days
            if not 1<=horizon<=730:
                continue
            bucket="1-30d" if horizon<=30 else "31-90d" if horizon<=90 else "91-365d" if horizon<=365 else "366-730d"
            group=(row["firm"],row["analyst_id"],key,bucket)
            if group in selected and known==selected[group][0] and Decimal(claim["value"])!=selected[group][1]:
                raise ValueError("Ambiguous same-receipt forecast")
            if group not in selected or known<selected[group][0]:
                selected[group]=(known,Decimal(claim["value"]),row["receipt_sha256"])
    scores={}
    for (firm,analyst,key,bucket),(_,forecast,receipt) in selected.items():
        eligible_count+=1
        if key not in actual:
            continue
        realized,evidence=actual[key]
        if key[0]=="target_price" and realized<=0:
            raise ValueError("Invalid realized target price")
        group=(firm,analyst,key[0],key[1],key[3],bucket)
        scores.setdefault(group,[]).append((float(abs(forecast-realized)),
            float(abs(forecast-realized)/abs(realized)) if realized else None,receipt,evidence))
    result=[]
    for (firm,analyst,metric,unit,basis,bucket),obs in sorted(scores.items()):
        result.append({"firm":firm,"analyst_id":analyst,"metric":metric,"unit":unit,"basis":basis,
                       "horizon_bucket":bucket,"n":len(obs),"mean_absolute_error":mean(x[0] for x in obs),
                       "mean_absolute_percentage_error":_avg([x[1] for x in obs if x[1] is not None]),
                       "evidence_receipts":sorted({x[2] for x in obs}),
                       "outcome_digests":sorted({x[3] for x in obs}),"research_weight":None})
    return {**FLAGS,"model":"forecast_error_review_v1","subject":subject,"as_of":cutoff.isoformat(),
            "evaluated":sum(x["n"] for x in result),"eligible":eligible_count,"scores":result,
            "outcome_file_sha256":sha256(raw).hexdigest(),"outcomes_independently_verified":False,
            "benchmark_adjusted_returns":None,"historical_accuracy_calibrated":False,
            "weights_enabled":False}


def event_review(*, subject, as_of, context=(), archive=ARCHIVE, holdings=()):
    """Link research with existing normalized news/options/disclosure events.

    Shared subject only; never equate conflicting forecasts with verified
    market repricing. No external calls and no automatic decision authority.
    """
    from atlaspm.research.financial_events import normalize, review
    if not isinstance(context,(list,tuple)) or len(context)>1000:
        raise ValueError("Context exceeds 1000 events")
    events=list(context)
    for row in visible(as_of=as_of,subject=subject,archive=archive):
        if row["evidence_type"]!="original_report":
            continue
        for kind,metrics in (("analyst_rating",{"rating","rating_action"}),
                             ("earnings_forecast",{"eps","revenue","ebitda"})):
            if any(c["metric"] in metrics for c in row["claims"]):
                events.append(normalize({"provider":"investment_banks",
                    "source_id":_digest([row["firm"],row["analyst_id"],row["report_id"],subject,kind]),
                    "kind":kind,"subjects":[subject],"published_at":row["published_at"],
                    "first_available_at":row["first_available_at"],"effective_at":None,
                    "evidence_sha256":row["receipt_sha256"],"delivery":"file","expiry_seconds":86400},
                    received_at=row["received_at"]))
    if len(events)>2000:
        raise ValueError("Institutional event batch exceeds review capacity")
    return review(events,as_of=as_of,holdings=holdings)


def main():
    from argparse import ArgumentParser
    parser=ArgumentParser(description=__doc__)
    parser.add_argument("--import-metadata")
    parser.add_argument("--subject")
    parser.add_argument("--as-of")
    parser.add_argument("--previous-as-of")
    parser.add_argument("--unit",default="USD")
    parser.add_argument("--period")
    parser.add_argument("--basis")
    parser.add_argument("--outcomes")
    args=parser.parse_args()
    if args.import_metadata:
        result=ingest(args.import_metadata)
    elif args.subject and args.as_of and args.outcomes:
        result=evaluate_forecasts(subject=args.subject,as_of=args.as_of,outcomes_file=args.outcomes)
        result.pop("scores")
    elif all((args.subject,args.as_of,args.previous_as_of,args.period,args.basis)):
        result=factors(subject=args.subject,as_of=args.as_of,previous_as_of=args.previous_as_of,
                       unit=args.unit,period=args.period,basis=args.basis)
        # Console summary only, no proprietary values, identifiers or evidence.
        result={k:v for k,v in result.items() if k not in {"values","consensus_values","evidence_receipts","subject"}}
    else:
        parser.error("Provide --import-metadata or explicit factor/evaluation arguments")
    print(json.dumps(result,indent=2))


if __name__=="__main__":
    main()
