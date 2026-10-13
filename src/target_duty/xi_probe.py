#!/usr/bin/env python3
"""Review-only XI 'Other' candidate probe using official HMRC V2 JSON:API.

Important: do NOT treat the UK 103/1011 selection rule as a confirmed XI
'Other' mapping. This module produces an auditable report; it never changes
`north_ireland_rate` or creates a production release.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path

TOKEN_URL = "https://auth.id.trade-tariff.service.gov.uk/oauth2/token"
API_URL = "https://api.trade-tariff.service.gov.uk/xi/api/commodities/"
ACCEPT = "application/vnd.hmrc.2.0+json"



PUBLIC_API_URL = "https://www.trade-tariff.service.gov.uk/xi/api/commodities/"

def fetch_public_commodity(code: str, as_of: date) -> dict:
    """One-off public API audit; official managed OAuth required for production."""
    if not re.fullmatch(r"\d{10}", code):
        raise ValueError("XI probe requires exactly a 10-digit commodity code")
    url = PUBLIC_API_URL + code + "?" + urllib.parse.urlencode({"as_of": as_of.isoformat()})
    request = urllib.request.Request(url, headers={
        "Accept": ACCEPT, "User-Agent": "liao-target-duty-xi-audit/1.0"})
    with urllib.request.urlopen(request, timeout=40) as response:
        payload = json.load(response)
    if not isinstance(payload, dict) or "data" not in payload:
        raise ValueError("Malformed public XI response")
    return payload


def get_token(client_id: str, client_secret: str) -> str:
    if not client_id or not client_secret:
        raise ValueError("HMRC_CLIENT_ID and HMRC_CLIENT_SECRET are required")
    post = urllib.parse.urlencode({
        "grant_type": "client_credentials",
        "client_id": client_id,
        "client_secret": client_secret
    }).encode()
    request = urllib.request.Request(
        TOKEN_URL, data=post, headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=30) as response:
        body = json.load(response)
    token = body.get("access_token")
    if not isinstance(token, str) or not token:
        raise ValueError("HMRC token response has no access_token")
    return token


def fetch_commodity(code: str, as_of: date, client_id: str, client_secret: str) -> dict:
    if not re.fullmatch(r"\d{10}", code):
        raise ValueError("XI probe requires exactly a 10-digit commodity code")
    url = API_URL + code + "?" + urllib.parse.urlencode({"as_of": as_of.isoformat()})
    token = get_token(client_id, client_secret)
    for trial in range(2):
        req = urllib.request.Request(url, headers={
            "Authorization": f"Bearer {token}", "Accept": ACCEPT,
            "User-Agent": "liao-target-duty-xi-audit/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=40) as response:
                result = json.load(response)
            if not isinstance(result, dict) or "data" not in result:
                raise ValueError("Malformed HMRC XI commodity response")
            return result
        except urllib.error.HTTPError as exc:
            if exc.code == 401 and trial == 0:
                token = get_token(client_id, client_secret)
                continue
            raise RuntimeError(f"XI API failed with HTTP {exc.code}") from exc
    raise RuntimeError("XI API authentication failed")


def refs(node: dict, relation: str) -> list[dict]:
    value = (node.get("relationships") or {}).get(relation, {}).get("data")
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        return [value]
    return []


def candidate_rate(candidates: list[dict]) -> str | None:
    """Unambiguous XI type 103 / ERGA OMNES *candidate*, not an Other ruling."""
    matching = [c for c in candidates if c["measure_type"] == "103"
                and c["geographical_area_id"] == "1011"]
    if len(matching) != 1:
        return None
    item = matching[0]
    if (item["has_conditions"] or item["additional_code_present"]
            or item.get("excluded_countries_present")):
        return None
    expressions = item.get("duty_expression_details") or []
    if len(expressions) != 1:
        return None
    base = expressions[0].get("base", "")
    if not isinstance(base, str) or not base.strip():
        return None
    return base.strip()


def summarize_response(payload: dict, code: str) -> dict:
    data = payload.get("data") or {}
    included = {
        (obj.get("type"), str(obj.get("id"))): obj
        for obj in payload.get("included", [])
        if isinstance(obj, dict)
    }
    candidates = []
    counts: Counter = Counter()
    relations = refs(data, "import_measures")
    all_type_counts: Counter = Counter()
    all_geo_counts: Counter = Counter()
    for measure_ref in relations:
        measure = included.get(("measure", str(measure_ref.get("id"))))
        if not measure:
            counts["unresolved_import_measure"] += 1
            continue
        types = refs(measure, "measure_type")
        geos = refs(measure, "geographical_area")
        measure_type = str(types[0].get("id")) if types else None
        geo = str(geos[0].get("id")) if geos else None
        all_type_counts[str(measure_type)] += 1
        all_geo_counts[str(geo)] += 1
        if measure_type != "103":
            counts["non_target_measure"] += 1
            continue
        conditions = refs(measure, "measure_conditions")
        attrs = measure.get("attributes") or {}
        duty = attrs.get("resolved_duty_expression")
        def detail(ref_name):
            output = []
            for reference in refs(measure, ref_name)[:15]:
                entity = included.get((reference.get("type"), str(reference.get("id"))))
                if entity:
                    output.append(entity.get("attributes") or {})
            return output
        candidates.append({
            "measure_id": str(measure.get("id")),
            "measure_type": measure_type,
            "geographical_area_id": geo,
            "duty_expression": duty,
            "duty_expression_details": detail("duty_expression"),
            "measure_components": detail("measure_components"),
            "resolved_measure_components": detail("resolved_measure_components"),
            "has_conditions": bool(conditions),
            "additional_code_present": bool(refs(measure, "additional_code")),
            "excluded_countries_present": bool(refs(measure, "excluded_countries")),
            "effective_start_date": attrs.get("effective_start_date"),
            "effective_end_date": attrs.get("effective_end_date")
        })
        counts["third_country_candidate"] += 1

    return {
        "commodity_code": code, "region": "XI",
        "status": "review_required",  # NEVER 'verified' without a proven Other mapping
        "rate": None,
        "candidate_rate": candidate_rate(candidates),
        "candidate_rate_status": "review_only_not_verified",
        "import_measures_listed": len(relations),
        "counts": dict(counts),
        "measure_types_observed": dict(all_type_counts),
        "geographical_areas_observed": dict(all_geo_counts),
        "candidates": candidates,
        "observed_at": datetime.now(timezone.utc).isoformat(),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--code", required=True)
    parser.add_argument("--as-of", default=date.today().isoformat())
    parser.add_argument("--output", default="xi-candidate-report.json")
    parser.add_argument("--public", action="store_true",
                        help="One-off public API probe only; never use as production source")
    args = parser.parse_args()
    when = date.fromisoformat(args.as_of)
    if args.public:
        payload = fetch_public_commodity(args.code, when)
    else:
        payload = fetch_commodity(
            args.code, when,
            os.getenv("HMRC_CLIENT_ID", ""), os.getenv("HMRC_CLIENT_SECRET", ""))
    report = summarize_response(payload, args.code)
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    print(f"XI candidate audit saved: {out} ({len(report['candidates'])} candidates)")
    # No raw token/secret output, never writes a trusted NI rate.


if __name__ == "__main__":
    main()
