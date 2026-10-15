"""Prevent regression of legacy concurrent or partial tariff publishing.

A deliberately narrow static contract over the three legacy GitHub Actions
files. It MUST fail CI if one regains a cron trigger, write permissions,
partial merge, timeout success-mask, or a GitHub Release publisher.
Actual end-user tariff promotion needs a separately approved workflow.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

WORKFLOWS = (
    "scrape-tariff.yml",
    "scrape-tariff-parallel.yml",
    "scrape-tariff-parallel-improved.yml",
)

PUBLISH_COMMAND = re.compile(
    r"(softprops/action-gh-release|gh\s+release\s+(?:create|edit|upload|delete)|"
    r"releases/(?:latest|update|create)|create-release|update-release|"
    r"upload-release-asset|git\s+push\s+\S*latest-data)",
    re.IGNORECASE,
)

def verify(name: str, source: str) -> dict:
    if name not in WORKFLOWS or not source.strip():
        raise ValueError("Unknown or empty legacy workflow")
    lines = [re.sub(r"\s+#.*$", "", line) for line in source.splitlines()
             if line.strip() and not line.lstrip().startswith("#")]
    code = "\n".join(lines)
    issues = []
    if re.search(r"(?m)^\s+schedule\s*:", code) or re.search(r"(?m)^\s+-\s*cron\s*:", code):
        issues.append("Legacy workflow must have no scheduled trigger")
    if not re.search(r"(?m)^\s*permissions:\s*$", code):
        issues.append("Legacy workflow lacks explicit read-only permissions")
    if re.search(r"(?m)^\s+contents:\s*write\b", code):
        issues.append("Legacy workflow can write repository or releases")
    if not re.search(r"(?m)^\s+contents:\s*read\b", code):
        issues.append("Legacy workflow lacks contents: read")
    if PUBLISH_COMMAND.search(code) or re.search(r"(?m)^\s+tag_name:\s*latest-data", code):
        issues.append("Legacy GitHub Release publishing command found")
    if "--allow-partial" in code:
        issues.append("Incomplete shard merging reenabled")
    failure_block = code.split("health_check_passed=false",1)
    if len(failure_block)!=2 or "exit 1" not in failure_block[1][:240]:
        issues.append("Failed SQLite health check may be masked as success")
    if name != "scrape-tariff.yml":
        if ("SCRAPE_RESULT" not in code or "MERGE_RESULT" not in code
                or "exit 1" not in code.split("SCRAPE_RESULT",1)[1][:750]):
            issues.append("Incomplete scrape/merge result may be masked")
    else:
        failure_branch = code.split('if [ "$FAILED" -ne 0 ]; then',1)
        if len(failure_branch)!=2 or "exit 1" not in failure_branch[1][:180]:
            issues.append("Individual source failures may be masked")
    if name == "scrape-tariff-parallel-improved.yml":
        # Inspect the actual retry-exhausted timeout block, not comments.
        timeout = re.search(
            r"if \[ \$EXIT_CODE -eq 124 \]; then([\s\S]{0,800}?)\n\s*fi\n\s*else",
            code)
        if timeout is None:
            issues.append("No explicit timeout retry-exhaustion branch found")
        else:
            block = timeout.group(1)
            if re.search(r"\bexit\s+0\b", block):
                issues.append("Timed-out scraper exits success")
            if not re.search(r"\bexit\s+1\b", block):
                issues.append("No hard failure on exhausted timeout retry")
    if "workflow_dispatch:" not in code:
        issues.append("Manual source-only diagnostic entrypoint missing")
    return {
        "workflow":name,
        "safe_review_only":not issues,
        "violations":issues,
        "may_publish_latest_data":False if not issues else None,
    }


def inspect(directory: str) -> dict:
    folder=Path(directory)
    rows=[]
    for name in WORKFLOWS:
        path=folder/name
        if not path.is_file():
            rows.append({"workflow":name,"safe_review_only":False,
                         "violations":["Legacy workflow missing"]})
            continue
        rows.append(verify(name,path.read_text(encoding="utf-8")))
    return {"kind":"legacy-latest-data-publisher-quarantine-v1",
            "workflows_checked":len(rows),
            "safe_review_only":all(x["safe_review_only"] for x in rows),
            "results":rows,
            "production_changed":False,
            "note":"Checks the checkout only. Default branch is not protected until merged."}


def main() -> None:
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--directory",default=".github/workflows")
    p.add_argument("--output")
    args=p.parse_args()
    receipt=inspect(args.directory)
    if args.output:
        Path(args.output).write_text(json.dumps(receipt,indent=2,ensure_ascii=False)+"\n",
                                     encoding="utf-8")
    print(json.dumps(receipt,indent=2,ensure_ascii=False))
    if not receipt["safe_review_only"]:
        raise SystemExit("Legacy latest-data publishing quarantine violated")


if __name__=="__main__":
    main()
