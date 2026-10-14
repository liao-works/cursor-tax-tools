"""Tamper-evident *review-only* continuity receipt for XI merged checkpoints.

This is a hash-linked operational audit, NOT a digital signature or
authorization to publish NI duty. An earlier trusted root must be pinned
out of band: hashes alone cannot authenticate malicious source artifacts.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import tempfile
from pathlib import Path

from .xi_acceptance import evaluate
from .xi_frozen_catalogue import digest_file, verify as verify_frozen

KIND="xi-raw-source-checkpoint-continuity-v1"
HASH=re.compile(r"[0-9a-f]{64}\Z")


def _document(path: str) -> dict:
    obj=json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(obj,dict):
        raise ValueError("XI checkpoint proof is not a JSON object")
    return obj


def _source(catalogue: str, database: str, merged_report: str,
            *, manifest: str | None, canary: bool) -> dict:
    if canary:
        if manifest:
            raise ValueError("Public XI canary cannot impersonate managed manifest")
        with sqlite3.connect(Path(catalogue).resolve().as_uri()+"?mode=ro",
                             uri=True) as db:
            meta=dict(db.execute("SELECT key,value FROM metadata"))
            chapters=db.execute("SELECT COUNT(*) FROM chapters").fetchone()[0]
        if (meta.get("listing_scope")!="partial_or_fixture"
                or meta.get("production_eligible")!="false" or chapters>5):
            raise ValueError("XI continuity canary requires partial fixture source")
        source_scope="partial_or_fixture"
        catalog_hash=digest_file(catalogue)
    else:
        if not manifest:
            raise ValueError("XI managed checkpoint requires frozen source manifest")
        frozen=verify_frozen(catalogue,manifest)
        source_scope="managed_full"
        catalog_hash=frozen["catalogue_sha256"]

    # All source measure rows are independently replayed from original
    # compressed official responses before continuity can be trusted.
    audit=evaluate(catalogue,database,min_chapters=1 if canary else 90)
    if audit["listing_scope"]!=source_scope:
        raise ValueError("XI merged checkpoint source scope mismatch")
    report=_document(merged_report)
    db_hash=digest_file(database)
    if (report.get("kind")!="xi-merged-source-checkpoint-v1"
            or report.get("production_eligible") is not False
            or report.get("other_semantics_verified") is not False
            or report.get("atomic_official_snapshot_verified") is not False
            or report.get("sqlite_sha256")!=db_hash
            or report.get("catalogue_sha256")!=catalog_hash
            or report.get("as_of")!=audit["date"]):
        raise ValueError("XI merge report source hash or safety flags mismatch")
    n=audit["requests_recorded"]
    success=n-audit["request_failures"]
    failed=audit["request_failures"]
    if (report.get("merged_requests")!=n
            or report.get("successful_codes")!=success
            or report.get("failed_codes")!=failed
            or report.get("remaining_codes")!=audit["active_declarable_codes"]-success
            or report.get("catalogue_candidate_codes")!=audit["active_declarable_codes"]
            or report.get("source_complete") is not (
                n==audit["active_declarable_codes"] and failed==0)):
        raise ValueError("XI merged checkpoint numeric counters disagree with replay")
    if not isinstance(report.get("source_shards"),list) or not report["source_shards"]:
        raise ValueError("XI merged checkpoint lacks source shard provenance")
    for source in report["source_shards"]:
        if (not isinstance(source,dict)
                or not isinstance(source.get("sha256"),str)
                or not HASH.fullmatch(source["sha256"])):
            raise ValueError("XI original shard hash malformed")
    with sqlite3.connect(Path(database).resolve().as_uri()+"?mode=ro",
                         uri=True) as db:
        if db.execute("PRAGMA integrity_check").fetchone()[0]!="ok":
            raise ValueError("XI merged SQLite integrity failed")
        rows=db.execute(
            "SELECT code,status,response_sha256,bytes,checked_at,error "
            "FROM requests ORDER BY code").fetchall()
    return {
        "catalogue_sha256":catalog_hash,
        "checkpoint_sha256":db_hash,
        "merge_report_sha256":digest_file(merged_report),
        "as_of":audit["date"],
        "source_scope":source_scope,
        "total":audit["active_declarable_codes"],
        "success":success,
        "failed":failed,
        "missing":audit["missing_codes"],
        "rows":{code:(status,sha,size,checked,err)
                for code,status,sha,size,checked,err in rows},
    }


def _identity(source: dict) -> dict:
    return {
        "source_scope":source["source_scope"],
        "as_of":source["as_of"],
        "catalogue_sha256":source["catalogue_sha256"],
        "checkpoint_sha256":source["checkpoint_sha256"],
        "merge_report_sha256":source["merge_report_sha256"],
        "total_official_codes":source["total"],
        "successful_codes":source["success"],
        "failed_codes":source["failed"],
        "missing_codes":source["missing"],
    }


def _parent(catalogue: str, source: dict, *,
            previous_db: str | None, previous_report: str | None,
            previous_chain: str | None, manifest: str | None,
            canary: bool) -> tuple[dict|None,str|None,dict]:
    given=[previous_db,previous_report,previous_chain]
    if any(given) and not all(given):
        raise ValueError("XI previous checkpoint, merged report AND continuity receipt required")
    if not all(given):
        return None,None,{"old_requests_retained":0,"old_successes_immutable":0,
                          "old_failures_recovered":0,"new_requests":len(source["rows"])}
    prior=_source(catalogue,previous_db,previous_report,manifest=manifest,
                  canary=canary)
    chain=_document(previous_chain)
    for key,value in {
        "kind":KIND,"production_eligible":False,
        "ni_other_verified":False,"atomic_official_snapshot_verified":False,
        "chain_authenticated_or_signed":False,
        **_identity(prior)
    }.items():
        if chain.get(key)!=value:
            raise ValueError("XI parent continuity receipt/source provenance mismatch: "+key)
    # A prior receipt without its own ancestor remains a locally
    # verifiable genesis; later links are only hash-linked, not signed.
    preceding=chain.get("parent_chain_sha256")
    preceding_db=chain.get("parent_checkpoint_sha256")
    if preceding is None:
        if (preceding_db is not None
                or chain.get("parent_successful_codes") is not None
                or chain.get("continuity")!={
                    "old_requests_retained":0,
                    "old_successes_immutable":0,
                    "old_failures_recovered":0,
                    "new_requests":len(prior["rows"])}):
            raise ValueError("XI original genesis checkpoint continuity metadata invalid")
    elif (not isinstance(preceding,str) or not HASH.fullmatch(preceding)
          or not isinstance(preceding_db,str) or not HASH.fullmatch(preceding_db)):
        raise ValueError("XI previous checkpoint lineage SHA-256 malformed")
    prior_hash=digest_file(previous_chain)
    old=prior["rows"]
    latest=source["rows"]
    if old.keys()-latest.keys():
        raise ValueError("XI checkpoint continuity lost previously captured requests")
    recovered=0
    retained_success=0
    for code, before in old.items():
        after=latest[code]
        if before[0]=="ok":
            if after!=before:
                raise ValueError(f"XI checkpoint continuity modified successful evidence: {code}")
            retained_success+=1
        elif before[0]=="source_error":
            if after[0]=="ok":
                recovered+=1
            elif after!=before:
                raise ValueError(f"XI checkpoint continuity modified original source error: {code}")
        else:
            raise ValueError("XI parent checkpoint contains illegal request state")
    if latest.keys()-old.keys():
        added=len(latest)-len(old)
    else:
        added=0
    if recovered+added and source["success"] < prior["success"]:
        raise ValueError("XI continuity success counters went backwards")
    return prior,prior_hash,{
        "old_requests_retained":len(old),
        "old_successes_immutable":retained_success,
        "old_failures_recovered":recovered,
        "new_requests":added,
    }


def seal(catalogue: str, checkpoint: str, merged_report: str, *,
         output: str, manifest: str | None=None, public_canary: bool=False,
         previous_db: str | None=None, previous_report: str | None=None,
         previous_chain: str | None=None) -> dict:
    inputs=[catalogue,checkpoint,merged_report,manifest,previous_db,
            previous_report,previous_chain]
    dest=Path(output).resolve()
    if dest in {Path(x).resolve() for x in inputs if x}:
        raise ValueError("XI continuity receipt cannot overwrite evidence inputs")
    current=_source(catalogue,checkpoint,merged_report,
                    manifest=manifest,canary=public_canary)
    prior,previous_hash,counts=_parent(
        catalogue,current,previous_db=previous_db,
        previous_report=previous_report,previous_chain=previous_chain,
        manifest=manifest,canary=public_canary)
    proof={
        "kind":KIND,
        **_identity(current),
        "parent_chain_sha256":previous_hash,
        "parent_checkpoint_sha256":prior["checkpoint_sha256"] if prior else None,
        "parent_successful_codes":prior["success"] if prior else None,
        "continuity":counts,
        "production_eligible":False,
        "ni_other_verified":False,
        "atomic_official_snapshot_verified":False,
        "chain_authenticated_or_signed":False,
        "warning":"Hash-linked operational review, NOT signed/authenticated HMRC evidence or NI Other duty.",
    }
    if dest.exists():
        if _document(str(dest))!=proof:
            raise ValueError("XI immutable checkpoint continuity receipt cannot be overwritten")
        return proof
    dest.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(dir=dest.parent) as folder:
        stage=Path(folder)/"xi-continuity.json"
        stage.write_text(json.dumps(proof,ensure_ascii=False,indent=2)+"\n",
                         encoding="utf-8")
        os.replace(stage,dest)
    return proof


def verify(catalogue: str,checkpoint: str,merged_report: str,chain_path: str,
           *,manifest: str|None=None,public_canary: bool=False,
           previous_db: str|None=None,previous_report: str|None=None,
           previous_chain: str|None=None) -> dict:
    path=Path(chain_path)
    recorded=_document(str(path))
    with tempfile.TemporaryDirectory() as folder:
        fresh=seal(catalogue,checkpoint,merged_report,output=str(Path(folder)/"replay.json"),
                   manifest=manifest,public_canary=public_canary,
                   previous_db=previous_db,previous_report=previous_report,
                   previous_chain=previous_chain)
    if recorded!=fresh:
        raise ValueError("XI checkpoint continuity receipt does not match replayed sources")
    return fresh


def main() -> None:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command",choices=("seal","verify"))
    parser.add_argument("--catalogue",required=True)
    parser.add_argument("--manifest")
    parser.add_argument("--checkpoint",required=True)
    parser.add_argument("--merge-report",required=True)
    parser.add_argument("--previous-checkpoint")
    parser.add_argument("--previous-report")
    parser.add_argument("--previous-chain")
    parser.add_argument("--public-canary",action="store_true")
    parser.add_argument("--output",required=True,
                        help="seal: destination receipt; verify: original receipt")
    args=parser.parse_args()
    common={
        "manifest":args.manifest,"public_canary":args.public_canary,
        "previous_db":args.previous_checkpoint,
        "previous_report":args.previous_report,
        "previous_chain":args.previous_chain,
    }
    if args.command=="seal":
        result=seal(args.catalogue,args.checkpoint,args.merge_report,
                    output=args.output,**common)
    else:
        result=verify(args.catalogue,args.checkpoint,args.merge_report,
                      args.output,**common)
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=="__main__":
    main()
