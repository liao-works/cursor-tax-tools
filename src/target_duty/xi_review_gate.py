#!/usr/bin/env python3
"""Review-only XI evidence quality gate. NEVER marks rates verified or publishes.

Checks that a public/API audit actually sampled distinct chapters and fetched
traceable, date-specific candidate evidence. Even passing evidence screening
remains *insufficient* to certify the web UI 'Other' meaning.
"""
from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path


def evaluate(audit: dict, min_chapters: int = 12) -> dict:
    if not 1 <= min_chapters <= 96:
        raise ValueError("Invalid chapter threshold")
    if audit.get("kind") != "xi-third-country-duty-review-only":
        raise ValueError("Wrong XI evidence kind")
    if audit.get("production_eligible") is not False:
        raise ValueError("NI audit must never claim production eligibility")
    expected_date = date.fromisoformat(audit["as_of"]).isoformat()
    entries = audit.get("entries")
    if not isinstance(entries, list) or audit.get("coverage") != len(entries):
        raise ValueError("Missing or inconsistent XI sample list")
    seen = set()
    chapters = set()
    stats = Counter()
    high_priority = []
    for entry in entries:
        code = entry.get("code", "")
        if not isinstance(code, str) or not re.fullmatch(r"\d{10}", code) or code in seen:
            raise ValueError("Duplicate or malformed commodity code in XI evidence")
        seen.add(code)
        chapters.add(code[:2])
        verdict = entry.get("verdict")
        candidate = entry.get("xi_candidate")
        comparison = entry.get("comparison")
        stats[str(verdict)] += 1
        stats[str(comparison)] += 1
        if candidate is not None:
            if (verdict != "candidate_review"
                    or entry.get("effective_date_checked") != expected_date
                    or entry.get("source") != "HMRC-XI-v2"
                    or not re.fullmatch(r"[a-fA-F0-9]{64}", str(entry.get("response_sha256", "")))):
                raise ValueError("XI candidate lacks traceable source and as-of evidence")
            matches = [m for m in entry.get("candidate_measures", [])
                       if m.get("measure_type") == "103"
                       and m.get("geographical_area_id") == "1011"]
            if not matches:
                raise ValueError("XI candidate lacks supporting target measure")
        if verdict != "candidate_review" or comparison not in ("ni_rate_same", "ni_format_equivalent"):
            high_priority.append({
                "code": code, "comparison": comparison, "verdict": verdict,
                "legacy": entry.get("legacy_ni_rate"), "candidate": candidate,
                "source_url": entry.get("source_url")
            })
    complete = len(chapters) >= min_chapters and len(entries) >= min_chapters
    return {
        "kind": "xi-other-evidence-gate-v1",
        "as_of": expected_date,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "samples": len(entries), "chapters": len(chapters),
        "required_chapters": min_chapters,
        "evidence_complete": complete,
        "status": "review_required" if complete else "insufficient_evidence",
        "manual_review_items": high_priority,
        "manual_review_count": len(high_priority),
        "counts": dict(stats),
        "production_eligible": False,
        "other_semantics_verified": False,
        "notes": (
            "Evidence screen only. Type 103/1011 is a candidate, NOT proven "
            "identical to the XI UI Other row. Manual interpretation required."
        )
    }


def render_markdown(report: dict) -> str:
    text = [
        "# XI Other candidate evidence review",
        "",
        f"Reference date: {report['as_of']}",
        f"Sampled commodities: {report['samples']}; distinct HS chapters: "
        f"{report['chapters']}/{report['required_chapters']} minimum",
        f"Evidence screening: **{report['status']}**",
        "**Production eligible: NO** — signed or matched values do not prove "
        "the meaning of the XI Other UI bucket.",
        "",
        "## Sample comparison counts",
        "",
    ]
    text.extend(f"- {key}: {value}" for key, value in sorted(report["counts"].items()))
    text.extend(["", "## Manual review priorities", ""])
    if report["manual_review_items"]:
        text.append("| Code | Legacy NI | XI 103 candidate | Difference |")
        text.append("|---|---|---|---|")
        for row in report["manual_review_items"]:
            values = (row["code"], row["legacy"], row["candidate"], row["comparison"])
            escaped = [str(v if v is not None else "—").replace("|", "\|") for v in values]
            text.append("| " + " | ".join(escaped) + " |")
    else:
        text.append("None detected in this small sample; UI semantics still need review.")
    return "\n".join(text) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit", required=True)
    parser.add_argument("--min-chapters", type=int, default=12)
    parser.add_argument("--output", default="xi-review-gate.json")
    args = parser.parse_args()
    payload = json.loads(Path(args.audit).read_text(encoding="utf-8"))
    result = evaluate(payload, min_chapters=args.min_chapters)
    dest = Path(args.output)
    dest.write_text(json.dumps(result, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    dest.with_suffix(".md").write_text(render_markdown(result), encoding="utf-8")
    print(json.dumps({k: result[k] for k in ("samples", "chapters", "status",
                                            "manual_review_count", "production_eligible")},
                     indent=2))


if __name__ == "__main__":
    main()
