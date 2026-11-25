"""Fetch SEC companyfacts into the existing gzip importer format.

No database or duplicate financial parser. Explicit fetch, single-writer local
workspace. The retrieval timestamp is not historical publication time.
"""
from __future__ import annotations

from argparse import ArgumentParser
from gzip import GzipFile, open as gzopen
from hashlib import sha256
import json
import os
from pathlib import Path
import re
from tempfile import NamedTemporaryFile

from atlaspm.data.api_gateway import DEFAULT_GATEWAY, GatewayError
from atlaspm.data.market_db import private_path


def _atomic_write(path, write):
    temp = None
    try:
        with NamedTemporaryFile(dir=path.parent, prefix=".atlaspm-", delete=False) as handle:
            temp = Path(handle.name)
            write(handle)
        os.replace(temp, path)
    finally:
        if temp is not None:
            temp.unlink(missing_ok=True)


def _raw_digest(path):
    digest = sha256()
    total = 0
    with gzopen(path, "rb") as handle:
        while chunk := handle.read(1024 * 1024):
            total += len(chunk)
            if total > 32 * 1024 * 1024:
                raise GatewayError("Cached SEC response exceeds size limit")
            digest.update(chunk)
    return digest.hexdigest(), total


def download_companyfacts(cik, *, directory=Path("local_data/sec/companyfacts"),
                          refresh=False, gateway=DEFAULT_GATEWAY):
    if not isinstance(cik, str) or not re.fullmatch(r"[0-9]{1,10}", cik) or int(cik) == 0:
        raise ValueError("Expected nonzero SEC CIK")
    if type(refresh) is not bool:
        raise ValueError("refresh must be boolean")
    cik = cik.zfill(10)
    folder = private_path(directory)
    target = private_path(folder / f"CIK{cik}.json.gz")
    receipt_path = private_path(folder / f"CIK{cik}.receipt.json")
    if not refresh and (target.exists() or receipt_path.exists()):
        if not target.is_file() or not receipt_path.is_file() or receipt_path.stat().st_size > 16384:
            raise GatewayError("Unmanaged or incomplete SEC cache; review and explicitly refresh")
        with receipt_path.open(encoding="utf-8") as handle:
            receipt = json.load(handle)
        digest, size = _raw_digest(target)
        if (receipt.get("cik") != cik or receipt.get("provider") != "sec"
                or receipt.get("sha256") != digest or receipt.get("bytes") != size):
            raise GatewayError("SEC cache content mismatch; explicit refresh required")
        # Reusing local data never grants admission even if metadata was edited.
        return {"cik": cik, "path": str(target), "sha256": digest, "bytes": size,
                "status": "LOCAL_CACHE_REUSED", "provider": "sec",
                "retrieved_at_utc": receipt.get("retrieved_at_utc"),
                "point_in_time_verified": False, "approved_for_model_training": False,
                "approved_for_backtest": False, "live_execution_enabled": False}
    response = gateway.fetch("sec", f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json")
    data = response.data()
    if str(data.get("cik", "")).zfill(10) != cik or not isinstance(data.get("facts"), dict):
        raise GatewayError("SEC companyfacts identity/schema mismatch")
    receipt = {**response.evidence(), "cik": cik, "status": "RETRIEVED_UNCERTIFIED"}
    folder.mkdir(parents=True, exist_ok=True)

    def compress(handle):
        with GzipFile(fileobj=handle, mode="wb", filename="", mtime=0) as packed:
            packed.write(response.body)

    _atomic_write(target, compress)
    _atomic_write(receipt_path, lambda handle: handle.write(
        json.dumps(receipt, sort_keys=True, indent=2).encode("utf-8")))
    return {**receipt, "path": str(target)}


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("--cik", required=True)
    parser.add_argument("--directory", type=Path, default=Path("local_data/sec/companyfacts"))
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()
    print(json.dumps(download_companyfacts(args.cik, directory=args.directory,
                                           refresh=args.refresh), indent=2))


if __name__ == "__main__":
    main()
