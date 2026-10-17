"""Daily read-only production tariff health watch. NEVER downloads to install.

Verify paired latest-data release assets AND versioned immutable database;
compare actual SHA-256/size/SQLite schema/count against metadata and age.
No successful result certifies origin, conditions, UKIMS or NI Other rates.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import re
import sqlite3
import tempfile
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

API="https://api.github.com/repos/liao-works/cursor-tax-tools/releases/tags/"
REQUIRED={"tariffs.db","metadata.json"}
VERSION=re.compile(r"data-[A-Za-z0-9.-]{1,100}\Z")
# Full GB/XI source snapshots are ~500MB and grow slowly; the audit downloads
# latest-data AND the pinned immutable copy into memory for byte comparison.
MAX_BYTES=1024*1024*1024


def when(value: str) -> datetime:
    if not isinstance(value,str):
        raise ValueError("Missing release asset update timestamp")
    parsed=datetime.fromisoformat(value.replace("Z","+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Unzoned update timestamp")
    return parsed.astimezone(timezone.utc)


def select(release: dict) -> dict:
    if not isinstance(release,dict):
        raise ValueError("Invalid release response")
    named={}
    for asset in release.get("assets",[]):
        name=asset.get("name")
        if name in REQUIRED:
            if name in named:
                raise ValueError("Duplicate release asset: "+name)
            if not isinstance(asset.get("size"),int) or asset["size"]<=0:
                raise ValueError("Invalid release asset size")
            when(asset.get("updated_at"))
            named[name]=asset
    if set(named)!=REQUIRED:
        raise ValueError("Missing database or metadata release asset")
    return named


def validate(latest: dict, metadata_bytes: bytes, db_bytes: bytes,
             immutable: dict, immutable_db: bytes, *, now: datetime,
             max_age_hours: int=48) -> dict:
    if not isinstance(max_age_hours,int) or not 1<=max_age_hours<=168:
        raise ValueError("Invalid allowed maximum age")
    if now.tzinfo is None:
        raise ValueError("Audit time must have a timezone")
    if latest.get("tag_name")!="latest-data":
        raise ValueError("Expected latest-data release tag")
    assets=select(latest)
    m=json.loads(metadata_bytes)
    if not isinstance(m,dict):
        raise ValueError("Invalid release metadata")
    version=m.get("version")
    if not isinstance(version,str) or not VERSION.fullmatch(version):
        raise ValueError("Unsafe or non-immutable metadata version")
    if immutable.get("tag_name")!=version:
        raise ValueError("Immutable release tag does not match metadata")
    pinned=select(immutable)
    observed_size=len(db_bytes)
    expected_size=m.get("file_size")
    if (not isinstance(expected_size,int) or isinstance(expected_size,bool) or
            expected_size<=0 or observed_size!=expected_size or
            assets["tariffs.db"]["size"]!=expected_size or
            pinned["tariffs.db"]["size"]!=expected_size or
            assets["metadata.json"]["size"]!=len(metadata_bytes)):
        raise ValueError("Release database/metadata reported size mismatch")
    reported=m.get("file_hash")
    if not isinstance(reported,str) or not re.fullmatch(r"sha256:[a-fA-F0-9]{64}",reported):
        raise ValueError("Missing valid database SHA-256")
    digest=hashlib.sha256(db_bytes).hexdigest()
    if (digest.lower()!=reported.split(":",1)[1].lower() or
            hashlib.sha256(immutable_db).hexdigest()!=digest):
        raise ValueError("Latest and immutable database SHA-256/metadata mismatch")
    with tempfile.TemporaryDirectory() as temp:
        path=Path(temp)/"snapshot.db"
        path.write_bytes(db_bytes)
        with sqlite3.connect(f"file:{path}?mode=ro",uri=True) as db:
            if db.execute("PRAGMA integrity_check").fetchone()[0]!="ok":
                raise ValueError("Released SQLite failed integrity_check")
            cols={r[1] for r in db.execute("PRAGMA table_info(tariffs)")}
            if not {"code","rate","north_ireland_rate","description","url"}.issubset(cols):
                raise ValueError("Production SQLite schema mismatch")
            count=db.execute("SELECT COUNT(*) FROM tariffs").fetchone()[0]
            if not isinstance(m.get("record_count"),int) or m["record_count"]!=count or count<=0:
                raise ValueError("Production release has bad record_count")
    now=now.astimezone(timezone.utc)
    db_day=when(assets["tariffs.db"]["updated_at"])
    meta_day=when(assets["metadata.json"]["updated_at"])
    # A torn pair is a fresh database published with stale metadata. The
    # publisher's unchanged-bytes path legitimately re-uploads metadata (and
    # the gap audit) alone while pinning the version to the existing release,
    # so a newer metadata asset with the older database is not a tear.
    if (db_day-meta_day).total_seconds()>900:
        raise ValueError("Database and metadata asset timestamps too far apart")
    age_hours=(now-min(db_day,meta_day)).total_seconds()/3600
    return {
        "kind":"gb-ni-latest-data-live-freshness-watch-v1",
        "checked_at":now.isoformat(),
        "release_tag":"latest-data",
        "immutable_data_tag":version,
        "database_sha256":digest,
        "db_and_immutable_match":True,
        "sqlite_integrity":"ok",
        "record_count":count,
        "max_age_hours":max_age_hours,
        "actual_age_hours":round(age_hours,2),
        "fresh":0<=age_hours<=max_age_hours,
        "source_rate_legality_verified":False,
        "ni_other_payable_verified":False,
        "production_eligible_from_this_check":False,
        "no_release_writes":True,
    }


def request_json(url: str) -> dict:
    req=urllib.request.Request(url,headers={"Accept":"application/vnd.github+json",
                             "User-Agent":"liao-tariff-freshness-audit/1.0"})
    with urllib.request.urlopen(req,timeout=30) as resp:
        data=resp.read(2*1024*1024+1)
    if len(data)>2*1024*1024:
        raise ValueError("Release JSON unusually large")
    return json.loads(data)


def request_asset(asset: dict) -> bytes:
    url=asset.get("browser_download_url")
    if not isinstance(url,str) or not url.startswith(
            "https://github.com/liao-works/cursor-tax-tools/releases/download/"):
        raise ValueError("Unexpected GitHub Release asset URL")
    size=asset.get("size")
    if not isinstance(size,int) or not 0<size<=MAX_BYTES:
        raise ValueError("Release artifact too large")
    req=urllib.request.Request(url,headers={"User-Agent":"liao-tariff-freshness-audit/1.0"})
    with urllib.request.urlopen(req,timeout=90) as resp:
        data=resp.read(size+1)
    if len(data)!=size:
        raise ValueError("Artifact bytes differ from GitHub release size")
    return data


def run(max_age_hours: int=48) -> dict:
    latest=request_json(API+"latest-data")
    a=select(latest)
    metadata=request_asset(a["metadata.json"])
    m=json.loads(metadata)
    version=m.get("version")
    if not isinstance(version,str) or not VERSION.fullmatch(version):
        raise ValueError("No trusted pinned production data release version")
    pinned=request_json(API+version)
    pinned_db=select(pinned)["tariffs.db"]
    return validate(latest,metadata,request_asset(a["tariffs.db"]),
                    pinned,request_asset(pinned_db),
                    now=datetime.now(timezone.utc),max_age_hours=max_age_hours)


def main() -> None:
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--max-age-hours",type=int,default=48)
    p.add_argument("--output",default="production-freshness-watch.json")
    args=p.parse_args()
    output=Path(args.output)
    try:
        report=run(args.max_age_hours)
    except Exception as e:
        report={"kind":"gb-ni-latest-data-live-freshness-watch-v1",
                "checked_at":datetime.now(timezone.utc).isoformat(),
                "fresh":False,"no_release_writes":True,
                "source_rate_legality_verified":False,
                "ni_other_payable_verified":False,
                "error":f"{type(e).__name__}: {str(e)[:280]}"}
    output.write_text(json.dumps(report,indent=2,ensure_ascii=False)+"\n",encoding="utf-8")
    print(json.dumps(report,indent=2,ensure_ascii=False))
    if not report["fresh"]:
        raise SystemExit("Production tariff release failed read-only freshness/integrity watch")


if __name__=="__main__":
    main()
