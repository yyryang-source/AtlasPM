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
                        parquet_sha256=first[0],manifest_sha256=first[1],qc=qc)
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
