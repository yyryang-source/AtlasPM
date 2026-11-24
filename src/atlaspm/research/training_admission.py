"""Read-only factor-lake admission preflight; never grants training permission.

Current V1 mart cannot establish vendor availability, corporate-action
adjustment, stable security identities or data rights. A structural pass
is deliberately not a point-in-time approval.
"""
from __future__ import annotations

import argparse
import json
from atlaspm.data import factor_db
from atlaspm.research.training_parquet import _hash
from atlaspm.research.training_registry import FLAGS


def _coverage(path,month,*,memory,threads):
    """NYSE month-level diagnostic; not per-listing survivorship evidence."""
    from atlaspm.data.hf_stock5m_audit import _calendar
    expected={d.isoformat() for d,_,_ in _calendar(month)}
    con=factor_db._db(memory,threads)
    try:
        relation=factor_db._files_sql([path])
        rows=con.execute(f"""SELECT trade_date,count(*),
            count(*) FILTER (WHERE volume_shares IS NULL),
            count(*) FILTER (WHERE volume_shares<0 OR NOT isfinite(volume_shares)),
            count(*) FILTER (WHERE abs(ret_1d)>=.5)
            FROM {relation} GROUP BY trade_date ORDER BY trade_date""").fetchall()
        observed={d.isoformat() for d,*_ in rows if d is not None}
        return dict(expected_nyse_sessions=len(expected),observed_sessions=len(observed),
                    missing_sessions=sorted(expected-observed),unexpected_sessions=sorted(observed-expected),
                    null_volume_rows=sum(r[2] for r in rows),invalid_volume_rows=sum(r[3] for r in rows),
                    extreme_return_rows=sum(r[4] for r in rows),
                    security_level_coverage_verified=False)
    finally:
        con.close()


def audit_factor_months(months, *, source="hf", memory="512MB", threads=1):
    if not isinstance(months,list) or not months or len(months)>360 or len(set(months))!=len(months):
        raise ValueError("INVALID_ADMISSION_MONTHS")
    reports=[]
    blockers={"VENDOR_AVAILABILITY_UNVERIFIED","CORPORATE_ACTIONS_UNVERIFIED",
              "HISTORICAL_IDENTITIES_UNVERIFIED","DELISTING_RETURNS_UNVERIFIED",
              "EXCHANGE_CALENDAR_COVERAGE_UNVERIFIED","DATA_RIGHTS_UNVERIFIED"}
    for month in sorted(months):
        path=factor_db._path(month,source,"rth","factors")
        manifest=path.with_suffix(".json")
        item={"month":month}
        if not path.is_file() or not manifest.is_file():
            blockers.add("MISSING_COMPLETED_PARTITION_OR_MANIFEST")
            item["status"]="MISSING"
        else:
            first=(_hash(path),_hash(manifest))
            meta=factor_db._read_meta(manifest)
            qc=factor_db.audit(month,source=source,memory=memory,threads=threads)
            try:
                coverage=_coverage(path,month,memory=memory,threads=threads)
            except ValueError as exc:
                coverage={"status":"CALENDAR_OR_COVERAGE_UNAVAILABLE","reason":str(exc)}
            if coverage.get("missing_sessions") or coverage.get("unexpected_sessions"):
                blockers.add("NYSE_SESSION_COVERAGE_MISMATCH")
            if coverage.get("invalid_volume_rows"):
                blockers.add("INVALID_VOLUME_ROWS")
            if coverage.get("extreme_return_rows"):
                blockers.add("EXTREME_RETURNS_REQUIRE_CORPORATE_ACTION_REVIEW")
            second=(_hash(path),_hash(manifest))
            stable=first==second
            matching=bool(meta and meta.get("version")==factor_db.VERSION and
                          meta.get("month")==month and meta.get("source")==source and
                          meta.get("session")=="rth" and meta.get("rows")==qc["rows"])
            if not stable:
                blockers.add("PARTITION_CHANGED_DURING_AUDIT")
            if not matching or not qc["valid"]:
                blockers.add("STRUCTURAL_OR_MANIFEST_FAILURE")
            item.update(status="STRUCTURAL_PASS" if stable and matching and qc["valid"] else "BLOCK",
                        parquet_sha256=first[0],manifest_sha256=first[1],qc=qc,calendar_diagnostics=coverage)
        reports.append(item)
    return dict(status="BLOCK_REAL_TRAINING",source=source,months=reports,
                blockers=sorted(blockers),point_in_time_validated=False,**FLAGS)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--months",nargs="+",required=True)
    p.add_argument("--source",choices=("hf","sqlite"),default="hf")
    p.add_argument("--memory",default="512MB")
    p.add_argument("--threads",type=int,default=1)
    a=p.parse_args()
    print(json.dumps(audit_factor_months(a.months,source=a.source,memory=a.memory,threads=a.threads),
                     indent=2,allow_nan=False))


if __name__=="__main__":
    main()
