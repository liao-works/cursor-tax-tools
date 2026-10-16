"""Independent fail-closed release-readiness decision, NEVER publish anything.

Source evidence completeness is a separate dimension from payable duty,
freshness, repeatable source acquisition, and atomic update recovery.
The decision is intentionally pessimistic for unknown/unproven attributes.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path

REQUIRED = {
    "data_source_completeness": "PASS_SOURCE_ONLY_NOT_PAYABLE",
    "collection_timeliness": "PASS_CURRENT_FULL_SNAPSHOT",
    "data_acquisition_stability": "PASS_REPEATED_RECOVERABLE",
    "update_installation_stability": "PASS_ATOMIC_ROLLBACK_AND_RELEASE",
}


def decide(report: dict) -> dict:
    if not isinstance(report,dict) or report.get("kind") != (
            "gb-ni-2026-10-15-final-operational-readiness-audit-v1"):
        raise ValueError("Invalid final tariff operational audit")
    dimensions = report.get("dimensions")
    if not isinstance(dimensions,dict):
        raise ValueError("Missing review dimensions")
    warnings=[]
    for name,target in REQUIRED.items():
        current=dimensions.get(name)
        if not isinstance(current,dict):
            warnings.append(name + ": missing evidence")
        elif current.get("status") != target:
            warnings.append(name + ": " + str(current.get("status","unknown")))
    complete=dimensions.get("data_source_completeness",{})
    if not isinstance(complete,dict):
        complete={}
    if complete.get("certified_payable_NI_other_rates",0) == 0:
        warnings.append("NI Other payable rates not independently certified")
    if complete.get("gb_without_direct_candidates",0) != 0:
        warnings.append("GB commodity source candidate gaps remain")
    if complete.get("ni_codes_without_candidates",0) != 0:
        warnings.append("NI commodity source candidate gaps remain")
    if complete.get("ni_unknown_additional_code_relations",0) != 0:
        warnings.append("NI additional code conditions unknown")
    if report.get("release_decision") != "PASS_AFTER_EXPLICIT_OPERATOR_APPROVAL":
        warnings.append("No approved production release receipt")
    if report.get("draft_pr_must_remain_draft") is not False:
        warnings.append("Source/NI applicability draft not reviewed or merged")
    return {
        "kind":"gb-ni-operational-release-decision-v1",
        "checks_executed":len(REQUIRED),
        "fail_closed":True,
        "release_permitted":False,
        "blockers":warnings,
        "readiness":"BLOCKED" if warnings else "STILL_REQUIRES_SEPARATE_SIGNED_OPERATOR_APPROVAL",
        "warning":"This checker cannot publish or attest any payable NI rate."
    }


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--input",required=True)
    p.add_argument("--output",required=True)
    a=p.parse_args()
    if Path(a.input).resolve()==Path(a.output).resolve():
        raise ValueError("Decision output may not overwrite evidence")
    receipt=decide(json.loads(Path(a.input).read_text(encoding="utf-8")))
    Path(a.output).write_text(json.dumps(receipt,indent=2,ensure_ascii=False)+"\n",encoding="utf-8")
    print(json.dumps(receipt,indent=2,ensure_ascii=False))
    # Deliberately exits success for analysis; explicit release flag is ALWAYS false.


if __name__=="__main__":
    main()
