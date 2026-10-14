"""S3 Object Lock vault for *operator-signed, source-only* XI archives.

No managed archive is durable until the exact stored S3 VERSION has been
downloaded, SHA-256 checked, operator-signature verified by a trusted public
key and all HMRC source rows replayed. The AWS CLI is invoked without shell.
The bucket MUST have versioning+Object Lock enabled; new objects are written
with SSE-KMS, COMPLIANCE retention and If-None-Match=*.

This is an audit vault, NOT NI Other rate validation or production publishing.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
import subprocess
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from .xi_archive import restore
from .xi_frozen_catalogue import digest_file

KIND="xi-s3-compliance-review-v1"
LIMIT=5*1024**3 # S3 PutObject single-request ceiling; multipart is NOT enabled
HEX=re.compile(r"[0-9a-f]{64}\Z")
BUCKET=re.compile(r"[a-z0-9][a-z0-9.-]{2,62}\Z")
OWNER=re.compile(r"[0-9]{12}\Z")
KMS=re.compile(r"arn:aws[a-z-]*:kms:[a-z0-9-]+:[0-9]{12}:key/[a-zA-Z0-9-]+\Z")
PREFIX=re.compile(r"[a-z0-9][a-z0-9/_-]{0,90}\Z")
MIN_DAYS=90


def aws(args: list[str]) -> dict:
    try:
        r=subprocess.run(["aws","s3api",*args,"--no-cli-pager","--output","json"],
                         capture_output=True,text=True,timeout=3600,check=False)
    except (OSError,subprocess.TimeoutExpired) as e:
        raise ValueError("AWS S3 vault operation unavailable or timed out") from e
    # Do NOT include stderr; it may contain account details.
    if r.returncode!=0:
        raise ValueError("AWS S3 vault operation failed: "+args[0])
    try:
        out=json.loads(r.stdout)
    except json.JSONDecodeError as e:
        raise ValueError("AWS S3 vault response invalid JSON: "+args[0]) from e
    if not isinstance(out,dict):
        raise ValueError("AWS S3 vault response must be JSON object")
    return out


def _validate(bucket: str,owner: str,kms_key: str,prefix: str,days: int) -> None:
    if (not BUCKET.fullmatch(bucket) or ".." in bucket or
        not OWNER.fullmatch(owner) or not KMS.fullmatch(kms_key) or
        kms_key.split(":")[4]!=owner or not PREFIX.fullmatch(prefix)
        or ".." in prefix or "//" in prefix or
        not MIN_DAYS<=days<=3650):
        raise ValueError("Unsafe S3 vault bucket, owner, KMS key, prefix or retention")


def preflight(bucket: str,owner: str,*,runner: Callable=aws) -> None:
    base=["--bucket",bucket,"--expected-bucket-owner",owner]
    v=runner(["get-bucket-versioning",*base])
    if v.get("Status")!="Enabled":
        raise ValueError("XI vault bucket versioning must be Enabled")
    lock=runner(["get-object-lock-configuration",*base])
    if lock.get("ObjectLockConfiguration",{}).get("ObjectLockEnabled")!="Enabled":
        raise ValueError("XI vault bucket S3 Object Lock must be Enabled")


def _head(bucket: str,key: str,owner: str,version: str,*,runner: Callable=aws) -> dict:
    head=runner(["head-object","--bucket",bucket,"--key",key,
                 "--version-id",version,"--expected-bucket-owner",owner])
    if head.get("VersionId")!=version or not version or version=="null":
        raise ValueError("XI S3 saved object lacks exact immutable version identity")
    return head


def store(archive: str,*,bucket: str,owner: str,kms_key: str,
          public_key: str,prefix: str="xi-review",days: int=90,
          runner: Callable=aws) -> dict:
    _validate(bucket,owner,kms_key,prefix,days)
    file=Path(archive)
    if (not file.is_file() or file.is_symlink() or file.stat().st_size<=0
            or file.stat().st_size>LIMIT):
        raise ValueError("XI signed archive missing, symlinked, or over single-PUT limit")
    if not Path(public_key).is_file():
        raise ValueError("Trusted operator public key not present")
    sha=digest_file(str(file))
    key=f"{prefix}/{sha[:2]}/{sha}.zip"
    preflight(bucket,owner,runner=runner)
    now=datetime.now(timezone.utc)
    retain=(now+timedelta(days=days)).isoformat(timespec="seconds")
    digest_b64=base64.b64encode(bytes.fromhex(sha)).decode("ascii")
    result=runner([
        "put-object","--bucket",bucket,"--key",key,
        "--expected-bucket-owner",owner,
        "--body",str(file),"--if-none-match","*",
        "--checksum-sha256",digest_b64,
        "--server-side-encryption","aws:kms","--ssekms-key-id",kms_key,
        "--object-lock-mode","COMPLIANCE",
        "--object-lock-retain-until-date",retain,
        "--metadata",f"sha256={sha},source-scope=managed-full",
    ])
    version=result.get("VersionId")
    if not isinstance(version,str) or version in ("","null"):
        raise ValueError("XI S3 upload did not return exact immutable VersionId")
    head=_head(bucket,key,owner,version,runner=runner)
    if (head.get("ContentLength")!=file.stat().st_size
            or head.get("ServerSideEncryption")!="aws:kms"
            or head.get("SSEKMSKeyId")!=kms_key
            or head.get("ObjectLockMode")!="COMPLIANCE"
            or head.get("Metadata",{}).get("sha256")!=sha):
        raise ValueError("XI vault stored-object encryption/size/lock metadata mismatch")
    expiry=head.get("ObjectLockRetainUntilDate")
    if not isinstance(expiry,str):
        raise ValueError("XI vault stored object retention date unavailable")
    try:
        actual=datetime.fromisoformat(expiry.replace("Z","+00:00"))
    except ValueError as e:
        raise ValueError("XI vault object retention timestamp malformed") from e
    if actual.tzinfo is None or actual < now+timedelta(days=days)-timedelta(minutes=10):
        raise ValueError("XI vault stored object retention is shorter than required")
    with tempfile.TemporaryDirectory() as tmp:
        local=Path(tmp)/"round-trip.zip"
        runner(["get-object","--bucket",bucket,"--key",key,
                "--version-id",version,"--expected-bucket-owner",owner,str(local)])
        if not local.is_file() or digest_file(str(local))!=sha:
            raise ValueError("XI S3 downloaded pinned version differs from uploaded archive")
        check=restore(str(local),str(Path(tmp)/"restored"),
                      trusted_public_key=public_key)
        if (not check["operator_signature_verified"]
                or not check["source_replay_verified"]
                or check["production_eligible"]):
            raise ValueError("XI S3 restored archive failed operator/source proof")
    return {
        "kind":KIND,
        "archive_sha256":sha,
        "s3_bucket":bucket,
        "s3_key":key,
        "s3_version_id":version,
        "s3_storage_class":head.get("StorageClass","STANDARD"),
        "sse_kms_verified":True,
        "object_lock_mode":"COMPLIANCE",
        "object_lock_retained_until":actual.isoformat(),
        "downloaded_version_sha256_verified":True,
        "operator_signature_verified":True,
        "raw_source_replay_verified":True,
        "managed_source_scope":"managed_full",
        "offsite_replication_proven":False,
        "production_eligible":False,
        "ni_other_verified":False,
        "atomic_official_snapshot_verified":False,
        "warning":"Protected source archive only: not NI Other or legally applicable duty.",
    }


def main() -> None:
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--archive",required=True)
    p.add_argument("--bucket",required=True)
    p.add_argument("--owner",required=True)
    p.add_argument("--kms-key",required=True)
    p.add_argument("--trusted-public-key",required=True)
    p.add_argument("--prefix",default="xi-review")
    p.add_argument("--retention-days",type=int,default=90)
    p.add_argument("--output",required=True)
    a=p.parse_args()
    result=store(a.archive,bucket=a.bucket,owner=a.owner,
                 kms_key=a.kms_key,public_key=a.trusted_public_key,
                 prefix=a.prefix,days=a.retention_days)
    dest=Path(a.output)
    dest.parent.mkdir(parents=True,exist_ok=True)
    if dest.exists():
        raise SystemExit("Do not overwrite existing independent XI S3 vault receipt")
    dest.write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",
                    encoding="utf-8")
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=="__main__":
    main()
