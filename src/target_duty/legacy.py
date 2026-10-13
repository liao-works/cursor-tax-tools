#!/usr/bin/env python3
"""Fetch the last production snapshot for shadow-only NI carry-over and comparison.

Every binary is pinned to metadata.version, size/sha256 checked before use.
This module MUST NOT publish, mutate existing releases, or trust a code's rate.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import tempfile
import urllib.request
from pathlib import Path

ROOT = "https://github.com/liao-works/cursor-tax-tools/releases/download"


def validate_metadata(meta: dict) -> tuple[str, int, str]:
    version = str(meta.get("version", ""))
    if not re.fullmatch(r"data-[A-Za-z0-9._-]{1,95}", version):
        raise ValueError("Legacy metadata has unsafe version")
    size = meta.get("file_size")
    if not isinstance(size, int) or not 0 < size < 100_000_000:
        raise ValueError("Legacy DB size not plausible")
    digest = str(meta.get("file_hash", ""))
    if not re.fullmatch(r"sha256:[0-9a-fA-F]{64}", digest):
        raise ValueError("Legacy metadata missing valid SHA-256")
    return version, size, digest[7:].lower()


def fetch(output_path: str) -> dict:
    req = urllib.request.Request(ROOT + "/latest-data/metadata.json",
        headers={"Accept": "application/json", "User-Agent": "target-duty-shadow/1.0"})
    with urllib.request.urlopen(req, timeout=45) as response:
        metadata = json.load(response)
    version, size, checksum = validate_metadata(metadata)
    dest = Path(output_path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=dest.parent) as folder:
        tmp = Path(folder) / "legacy.db"
        url = ROOT + "/" + version + "/tariffs.db"
        request = urllib.request.Request(url, headers={"User-Agent": "target-duty-shadow/1.0"})
        digest = hashlib.sha256()
        got = 0
        with urllib.request.urlopen(request, timeout=120) as response, open(tmp, "wb") as writer:
            while piece := response.read(1024 * 1024):
                got += len(piece)
                if got > size:
                    raise ValueError("Downloaded legacy DB exceeds metadata size")
                writer.write(piece)
                digest.update(piece)
        if got != size or digest.hexdigest() != checksum:
            raise ValueError("Legacy DB size or SHA-256 mismatch")
        tmp.replace(dest)
    return {"version": version, "bytes": got, "sha256": checksum}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="legacy-tariffs.db")
    args = parser.parse_args()
    print(json.dumps(fetch(args.output), ensure_ascii=False))


if __name__ == "__main__":
    main()
