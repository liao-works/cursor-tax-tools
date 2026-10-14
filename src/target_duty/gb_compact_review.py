"""Produce a bounded GB 103/1011 candidate-review SQLite from a full DBT export.

This file is deliberately NOT desktop tariffs.db: there is no 'rate' or
'north_ireland_rate' column, and every matched measure is labelled a
conditional/unverified CANDIDATE, not an applicable or payable duty.
The country/geography 1080 (Channel Islands) is preserved only as clearly
non-GB context for otherwise unexplained gaps. Never map it to 1011.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import tempfile
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path

from .gb_full_measures import _catalogue
from .region_measures import _db_readonly

SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE provenance (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE commodities (
    code TEXT PRIMARY KEY CHECK(length(code)=10),
    status TEXT NOT NULL,
    source_measure_rows INTEGER NOT NULL CHECK(source_measure_rows>=0),
    active_target_candidates INTEGER NOT NULL CHECK(active_target_candidates>=0),
    other_area_context_rows INTEGER NOT NULL CHECK(other_area_context_rows>=0)
);
CREATE TABLE evidence (
    code TEXT NOT NULL REFERENCES commodities(code),
    source_ordinal INTEGER NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('gb_103_1011_candidate','non_gb_103_1080_context')),
    sid TEXT,
    measure_type TEXT NOT NULL CHECK(measure_type='103'),
    geography TEXT NOT NULL CHECK(geography IN ('1011','1080')),
    effective_status TEXT NOT NULL,
    expression TEXT,
    additional_code TEXT,
    conditions TEXT,
    excluded_areas TEXT,
    effective_start TEXT,
    effective_end TEXT,
    raw_json TEXT NOT NULL,
    PRIMARY KEY(code,source_ordinal)
);
CREATE INDEX idx_evidence_code_role ON evidence(code,role,effective_status);
"""


def file_sha(path: str) -> str:
    digest=hashlib.sha256()
    with Path(path).open("rb") as inp:
        while part := inp.read(1024*1024):
            digest.update(part)
    return digest.hexdigest()


def _input_checks(source: sqlite3.Connection, raw: dict, *,
                  source_file: str, version: str, as_of: date) -> tuple[int,str]:
    snapshot=source.execute(
        "SELECT source_kind,source_version,as_of,scope,production_eligible "
        "FROM snapshots WHERE region='GB'").fetchone()
    if snapshot != (
        "DBT-expanded-measures",version,as_of.isoformat(),
        "all-DBT-expanded-measures-not-all-import-taxes",0
    ):
        raise ValueError("GB full measure source scope/version/day mismatch")
    if source.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0]!=1:
        raise ValueError("GB shadow DB must have exactly one regional snapshot")
    meta=dict(source.execute("SELECT key,value FROM meta"))
    rows=int(meta["imported_measure_rows"])
    if rows<1 or rows!=source.execute(
        "SELECT COUNT(*) FROM measures WHERE region='GB'").fetchone()[0]:
        raise ValueError("GB expanded measure row-count mismatch")
    if source.execute("PRAGMA integrity_check").fetchone()[0]!="ok":
        raise ValueError("GB source SQLite failed integrity check")
    if raw.get("kind")!="gb-full-expanded-measures-shadow":
        raise ValueError("Expected full DBT source manifest")
    if (raw.get("production_eligible") is not False
        or raw.get("all_import_duties_verified") is not False
        or raw.get("ni_other_verified") is not False
        or raw.get("focused_parity") is not True
        or raw.get("complete_within_dbt_export_scope") is not True
        or raw.get("source_rows")!=rows
        or raw.get("version")!=version
        or raw.get("as_of")!=as_of.isoformat()
        or raw.get("source_csv_sha256")!=meta["source_sha256"]):
        raise ValueError("GB source metadata or focused parity mismatches")
    # Authenticate bytes against manifest before trusting the full database.
    verified=file_sha(source_file)
    if verified!=raw.get("database_sha256"):
        raise ValueError("GB full source SQLite SHA-256 mismatch")
    return rows,verified


def build(full_db: str, catalogue_db: str, output: str, *, max_mb: int=256) -> dict:
    """Build a small self-contained REVIEW-only candidate DB atomically."""
    if not 1<=max_mb<=1000:
        raise ValueError("Invalid compact shadow output byte limit")
    source_file=Path(full_db)
    manifest_file=source_file.with_suffix(".json")
    if not source_file.is_file() or not manifest_file.is_file():
        raise ValueError("Missing GB full-source SQLite or original JSON manifest")
    raw=json.loads(manifest_file.read_text(encoding="utf-8"))
    version=raw["version"]
    day=date.fromisoformat(raw["as_of"])
    if Path(output).resolve() in (
        source_file.resolve(), Path(catalogue_db).resolve()):
        raise ValueError("Cannot overwrite evidence inputs")
    active=_catalogue(catalogue_db,version,day)
    if len(active)!=raw.get("official_active_leaves"):
        raise ValueError("GB active-leaf catalogue coverage mismatch")
    with _db_readonly(str(source_file)) as inp:
        source_rows,source_sha=_input_checks(
            inp,raw,source_file=str(source_file),version=version,as_of=day)
        output_file=Path(output)
        output_file.parent.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(dir=output_file.parent) as folder:
            stage=Path(folder)/"gb-target-review.db"
            db=sqlite3.connect(stage)
            summaries=Counter()
            active_target_codes=set()
            candidates=0
            context_rows=0
            try:
                db.executescript(SCHEMA)
                for key,value in (
                    ("region","GB"),("source_kind","pinned-DBT-compact-103-review"),
                    ("source_version",version),("as_of",day.isoformat()),
                    ("full_source_sha256",source_sha),
                    ("source_csv_sha256",raw["source_csv_sha256"]),
                    ("complete_import_source_rows",str(source_rows)),
                    ("official_active_leaves",str(len(active))),
                    ("production_eligible","false"),
                    ("rate_applicability_verified","false"),
                    ("ni_other_verified","false")
                ):
                    db.execute("INSERT INTO provenance VALUES (?,?)",(key,value))
                for code in sorted(active):
                    request=inp.execute(
                        "SELECT status,row_count FROM requests "
                        "WHERE region='GB' AND code=?",(code,)).fetchone()
                    if request is None:
                        raise ValueError(f"Official active leaf absent from source requests: {code}")
                    src_status,total_rows=request
                    if not isinstance(total_rows,int) or total_rows<0:
                        raise ValueError("Malformed full GB request row count")
                    if src_status not in (
                        "has_expanded_measures","no_expanded_measure_rows_unverified"
                    ):
                        raise ValueError("Unexpected GB measure source request status")
                    if (src_status=="no_expanded_measure_rows_unverified")!=(total_rows==0):
                        raise ValueError("GB empty source request status inconsistent")
                    # Materialize parent first: SQLite enforces child FOREIGN KEY.
                    db.execute("INSERT INTO commodities VALUES (?,?,?,?,?)",
                               (code,"being_derived",total_rows,0,0))
                    selected=list(inp.execute(
                        """SELECT ordinal,measure_sid,measure_type,geography,
                           effective_status,expression,additional_code,
                           conditions,excluded_areas,effective_start,effective_end,raw_json
                           FROM measures
                           WHERE region='GB' AND code=? AND measure_type='103'
                              AND geography IN ('1011','1080')
                           ORDER BY ordinal""",(code,)))
                    active_target=0
                    other_area=0
                    for row in selected:
                        (ordinal,sid,kind,geo,effective,expression,additional,
                         conditions,excluded,start,end,raw_json)=row
                        src=json.loads(raw_json)
                        if (src.get("commodity__code")!=code
                            or str(src.get("measure__sid"))!=sid
                            or src.get("measure__type__id")!=kind
                            or src.get("measure__geographical_area__id")!=geo):
                            raise ValueError(f"GB raw and indexed evidence mismatch at {code}")
                        role=("gb_103_1011_candidate" if geo=="1011"
                              else "non_gb_103_1080_context")
                        db.execute(
                            "INSERT INTO evidence VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                            (code,ordinal,role,sid,kind,geo,effective,expression,
                             additional,conditions,excluded,start,end,raw_json))
                        if role=="gb_103_1011_candidate":
                            candidates+=1
                            if effective=="active":
                                active_target+=1
                        else:
                            context_rows+=1
                            other_area+=1
                    if active_target:
                        status="candidate_present_requires_applicability_review"
                        active_target_codes.add(code)
                    elif total_rows==0:
                        status="no_expanded_measure_rows_unverified"
                    elif other_area:
                        status="non_gb_1080_only_or_other_measures_unresolved"
                    else:
                        status="no_active_103_1011_unresolved"
                    db.execute(
                        "UPDATE commodities SET status=?,active_target_candidates=?,"
                        "other_area_context_rows=? WHERE code=?",
                        (status,active_target,other_area,code))
                    summaries[status]+=1
                if len(active_target_codes)!=raw["active_103_1011_codes"]:
                    raise ValueError("Active GB 103/1011 target count differs from pinned full source")
                if (len(active_target_codes)<raw["focused_codes"]
                    or summaries["no_expanded_measure_rows_unverified"]
                       !=raw["active_leaves_without_any_export_rows"]):
                    raise ValueError("GB target parity or seven unresolved leaves failed")
                db.commit()
                if db.execute("PRAGMA integrity_check").fetchone()[0]!="ok":
                    raise ValueError("GB candidate shadow SQLite corrupted")
                if db.execute("SELECT COUNT(*) FROM evidence").fetchone()[0]!=(candidates+context_rows):
                    raise ValueError("GB target evidence rows lost")
            finally:
                db.close()
            if stage.stat().st_size>max_mb*1024*1024:
                raise ValueError("Compact GB review SQLite exceeds configured size limit")
            stage.replace(output_file)
    result={
        "kind":"gb-compact-103-1011-shadow-v1",
        "production_eligible":False,
        "rate_applicability_verified":False,
        "ni_other_verified":False,
        "as_of":day.isoformat(),"version":version,
        "full_source_sha256":source_sha,
        "full_source_rows":source_rows,
        "official_active_leaf_codes":len(active),
        "active_target_codes":len(active_target_codes),
        "candidate_103_1011_rows_all_dates":candidates,
        "non_gb_channel_islands_1080_context_rows":context_rows,
        "statuses":dict(summaries),
        "review_database_sha256":file_sha(str(output_file)),
        "review_database_bytes":output_file.stat().st_size,
        "warning":"GB 103/1011 is raw candidate evidence, not payable GB or XI Other. 1080 is never treated as GB.",
    }
    output_file.with_suffix(".json").write_text(
        json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--full-db",required=True)
    p.add_argument("--catalogue",required=True)
    p.add_argument("--output",default="gb-compact-103-review.db")
    p.add_argument("--max-mb",type=int,default=256)
    a=p.parse_args()
    print(json.dumps(build(a.full_db,a.catalogue,a.output,max_mb=a.max_mb),
                     ensure_ascii=False,indent=2))


if __name__=="__main__":
    main()
