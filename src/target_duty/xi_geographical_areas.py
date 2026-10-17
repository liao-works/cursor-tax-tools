"""Pinned-as-of official XI geographical-area tree for China applicability."""
from __future__ import annotations

import argparse
import hashlib
import json
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path

from .xi_probe import ACCEPT, PUBLIC_API_URL

MAX_BYTES = 20_000_000


def source_url(as_of: date) -> str:
    query = urllib.parse.urlencode({
        "as_of": as_of.isoformat(),
        "filter[exclude_none]": "true",
    })
    return PUBLIC_API_URL.rsplit("/commodities/", 1)[0] + "/geographical_areas?" + query


def inspect_payload(payload: bytes, as_of: date, *, min_areas: int = 50) -> dict:
    if not payload or len(payload) > MAX_BYTES:
        raise ValueError("Empty or oversized official XI geographical-area response")
    try:
        doc = json.loads(payload)
    except (ValueError, UnicodeError) as error:
        raise ValueError("Official XI geographical-area response is not valid JSON") from error
    if not isinstance(doc, dict) or not isinstance(doc.get("data"), list):
        raise ValueError("XI geographical-area response lacks the complete data list")
    links = doc.get("links") or {}
    if isinstance(links, dict) and links.get("next"):
        raise ValueError("XI geographical-area inventory is paginated and incomplete")
    nodes = {}
    for item in (doc.get("data") or []) + (doc.get("included") or []):
        if not isinstance(item, dict) or item.get("type") != "geographical_area":
            continue
        area_id = str(item.get("id") or "")
        if not area_id:
            raise ValueError("XI geographical-area entry lacks an ID")
        previous = nodes.get(area_id)
        if previous is not None and previous != item:
            # JSON:API may repeat the same resource in data and included with
            # a different field order; compare the normalized representation.
            if json.dumps(previous, sort_keys=True) != json.dumps(item, sort_keys=True):
                raise ValueError("Conflicting XI geographical-area resource IDs")
        nodes[area_id] = item
    if len(nodes) < min_areas or "CN" not in nodes or "1011" not in nodes:
        raise ValueError("XI geographical-area inventory is incomplete")

    memo = {}
    def contains_china(area_id: str, stack: tuple[str, ...] = ()) -> bool:
        if area_id == "CN":
            return True
        if area_id in memo:
            return memo[area_id]
        if area_id in stack:
            raise ValueError("Cycle in official XI geographical-area membership tree")
        node = nodes.get(area_id)
        if node is None:
            raise ValueError("Unresolved XI geographical-area membership reference: " + area_id)
        relationships = node.get("relationships") or {}
        relation = relationships.get("contained_geographical_areas") or {}
        children = relation.get("data") or []
        if not isinstance(children, list):
            raise ValueError("Malformed XI contained_geographical_areas relationship")
        result = False
        for child in children:
            if not isinstance(child, dict) or not child.get("id"):
                raise ValueError("Malformed XI geographical-area child reference")
            if contains_china(str(child["id"]), stack + (area_id,)):
                result = True
        memo[area_id] = result
        return result

    china_areas = sorted(area_id for area_id in nodes if contains_china(area_id))
    if "1011" not in china_areas:
        raise ValueError("XI Erga Omnes area does not resolve to China in official membership tree")
    groups = [area_id for area_id in china_areas if area_id != "CN"]
    return {
        "kind": "hmrc-official-xi-geographical-area-tree-v1",
        "as_of": as_of.isoformat(),
        "source": "official-HMRC-XI-Trade-Tariff-API",
        "area_resource_count": len(nodes),
        "area_ids_containing_china": china_areas,
        "china_applicable_group_ids": groups,
        "china_applicable_group_count": len(groups),
        "source_sha256": hashlib.sha256(payload).hexdigest(),
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "complete": True,
    }


def download(as_of: date, output: str, report_path: str) -> dict:
    url = source_url(as_of)
    request = urllib.request.Request(url, headers={
        "Accept": ACCEPT,
        "User-Agent": "liao-xi-geographical-area-audit/1.0",
    })
    target = Path(output)
    target.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(request, timeout=60) as response:
        if response.status != 200:
            raise ValueError("Official XI geographical-area endpoint returned non-200")
        resolved = response.geturl()
        if "/geographical_areas" not in resolved or as_of.isoformat() not in resolved:
            raise ValueError("XI geographical-area response does not match requested as_of")
        payload = response.read(MAX_BYTES + 1)
    if len(payload) > MAX_BYTES:
        raise ValueError("Official XI geographical-area response exceeds byte limit")
    result = inspect_payload(payload, as_of)
    target.write_bytes(payload)
    result["source_url"] = url
    result["resolved_url"] = resolved
    result["bytes"] = len(payload)
    Path(report_path).write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n",
                                 encoding="utf-8")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--as-of", required=True)
    parser.add_argument("--output", default="xi-geographical-areas.json")
    parser.add_argument("--report", default="xi-geographical-areas-report.json")
    args = parser.parse_args()
    result = download(date.fromisoformat(args.as_of), args.output, args.report)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
