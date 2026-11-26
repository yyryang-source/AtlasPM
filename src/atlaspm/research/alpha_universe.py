"""Read-only candidate universe derived from an explicitly reviewed security master."""
import json
import re
from pathlib import Path
from atlaspm.data.market_db import private_path
from atlaspm.research.financial_events import _digest

def load_universe(path):
    p=private_path(Path(path),exists=True)
    with p.open("rb") as f:
        raw=f.read(4*1024*1024+1)
    if len(raw)>4*1024*1024:
        raise ValueError("Universe file exceeds budget")
    rows=json.loads(raw)
    if not isinstance(rows,list) or not 1<=len(rows)<=20000:
        raise ValueError("Invalid reviewed universe")
    result={}
    for row in rows:
        if set(row)!={"symbol","security_id","asset_type","cik","active"}:
            raise ValueError("Invalid universe row")
        s=row["symbol"]
        if not isinstance(s,str) or not re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,15}",s) or s in result:
            raise ValueError("Invalid or duplicate symbol")
        if not isinstance(row["security_id"],str) or not re.fullmatch(r"[A-Za-z0-9:._-]{1,128}",row["security_id"]):
            raise ValueError("Invalid security identity")
        if row["asset_type"] not in {"stock","etf"} or type(row["active"]) is not bool:
            raise ValueError("Unsupported universe asset")
        if row["cik"] is not None and (not isinstance(row["cik"],str) or not re.fullmatch(r"[0-9]{10}",row["cik"])):
            raise ValueError("Invalid CIK")
        result[s]=row
    if len({row["security_id"] for row in result.values()})!=len(result):
        raise ValueError("Duplicate security identity")
    return {"rows":result,"sha256":_digest(rows),"identity_independently_verified":False}

def batches(universe, *, size=50):
    if type(size) is not int or not 1<=size<=100:
        raise ValueError("Invalid batch size")
    symbols=sorted(s for s,r in universe["rows"].items() if r["active"])
    for index in range(0,len(symbols),size):
        yield symbols[index:index+size]
