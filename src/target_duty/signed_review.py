#!/usr/bin/env python3
"""Ed25519-authenticated *review* bundle. Signing never permits production use.

Sign the exact UTF-8 bytes of manifest.json (not a reserialized object). The
artifact SHA-256 is covered by the signed manifest. Private seed is supplied
ONLY via environment; fail closed without it. Trust anchor belongs in desktop
binary, never the manifest.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import tempfile
import zipfile
from pathlib import Path

MAX_PREVIEW_BYTES = 40 * 1024 * 1024
MAX_ARCHIVE_BYTES = 40 * 1024 * 1024
MAX_MANIFEST_BYTES = 64 * 1024


def _canonical_bytes(payload: dict) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


def _parse_seed(value: str) -> bytes:
    if not re.fullmatch(r"[a-fA-F0-9]{64}", value):
        raise ValueError("Expected exactly 32 private seed bytes encoded as hex")
    return bytes.fromhex(value)


def sign_review(preview_zip: str, output: str, key_id: str, private_seed_hex: str) -> dict:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,60}", key_id):
        raise ValueError("Invalid signing key ID")
    src = Path(preview_zip)
    if src.stat().st_size > MAX_ARCHIVE_BYTES:
        raise ValueError("Preview archive is too large")
    with zipfile.ZipFile(src) as z:
        if sorted(z.namelist()) != ["manifest.json", "target-duty.db"]:
            raise ValueError("Expected an untrusted shadow preview containing exactly two files")
        manifest_bytes = z.read("manifest.json")
        if len(manifest_bytes) > MAX_MANIFEST_BYTES:
            raise ValueError("Preview manifest too large")
        unsigned = json.loads(manifest_bytes)
        if (unsigned.get("schema_version") != 2 or
            unsigned.get("channel") != "shadow-preview" or
            unsigned.get("signed") is not False or
            unsigned.get("production_eligible") is not False):
            raise ValueError("Unexpected unsigned source channel")
        db_bytes = z.read("target-duty.db")
    if len(db_bytes) > MAX_PREVIEW_BYTES:
        raise ValueError("DB is too large")
    artifact = unsigned.get("artifact") or {}
    if (artifact.get("name") != "target-duty.db" or
        artifact.get("size") != len(db_bytes) or
        artifact.get("sha256") != hashlib.sha256(db_bytes).hexdigest()):
        raise ValueError("Preview manifest does not match DB file")

    # Signing is a trust boundary. Independently recheck the source DB,
    # including that nobody has silently marked legacy XI Other as verified.
    source = unsigned.get("source") or {}
    quality = unsigned.get("quality") or {}
    if (source.get("measure_type") != "103" or
        source.get("geographical_area") != "1011" or
        not isinstance(quality.get("record_count"), int)):
        raise ValueError("Source cannot be certified as focused GB 103/1011")
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "candidate.db"
        path.write_bytes(db_bytes)
        with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as conn:
            if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("SQLite integrity check failed before signing")
            counts = conn.execute(
                """SELECT COUNT(*),
                    SUM(CASE WHEN uk_rate_status='verified' AND TRIM(rate)!=''
                         THEN 1 ELSE 0 END),
                    SUM(CASE WHEN uk_rate_status='conditional' AND TRIM(rate)=''
                         THEN 1 ELSE 0 END),
                    SUM(CASE WHEN ni_rate_status IN ('stale','pending')
                         THEN 1 ELSE 0 END)
                    FROM tariffs""").fetchone()
    total, verified, conditional, untrusted_ni = counts
    if (total < 100 or verified + conditional != total or untrusted_ni != total or
        quality.get("record_count") != total or
        quality.get("gb_verified") != verified or
        quality.get("gb_conditional") != conditional or
        quality.get("ni_unverified") != untrusted_ni):
        raise ValueError("Quality gate failed before signing (NI must remain unverified)")
    signed = {**unsigned, "channel": "signed-shadow-review",
              "signed": True, "production_eligible": False,
              "signature_alg": "ed25519", "key_id": key_id}
    signed_bytes = _canonical_bytes(signed)
    key = Ed25519PrivateKey.from_private_bytes(_parse_seed(private_seed_hex))
    signature = key.sign(signed_bytes)
    output_path = Path(output)
    if output_path.resolve() == src.resolve():
        raise ValueError("Cannot overwrite source shadow archive")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED,
                         compresslevel=6) as z:
        z.writestr("manifest.json", signed_bytes)
        z.writestr("manifest.sig", signature)
        z.writestr("target-duty.db", db_bytes)
    return {"release_id": signed["release_id"], "key_id": key_id,
            "signature_alg": "ed25519", "signed": True,
            "production_eligible": False, "artifact_sha256": artifact["sha256"]}


def verify_review(bundle: str, public_key_hex: str) -> dict:
    """Independent source-side smoke test. Desktop must still verify on its own."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    if not re.fullmatch(r"[a-fA-F0-9]{64}", public_key_hex):
        raise ValueError("Invalid trusted public key")
    with zipfile.ZipFile(bundle) as z:
        if sorted(z.namelist()) != ["manifest.json", "manifest.sig", "target-duty.db"]:
            raise ValueError("Unexpected signed ZIP structure")
        manifest = z.read("manifest.json")
        signature = z.read("manifest.sig")
        db = z.read("target-duty.db")
    if len(manifest) > MAX_MANIFEST_BYTES or len(signature) != 64 or len(db) > MAX_PREVIEW_BYTES:
        raise ValueError("Invalid signed ZIP sizes")
    Ed25519PublicKey.from_public_bytes(bytes.fromhex(public_key_hex)).verify(signature, manifest)
    meta = json.loads(manifest)
    if (meta.get("schema_version") != 2 or meta.get("channel") != "signed-shadow-review" or
        meta.get("signed") is not True or meta.get("production_eligible") is not False or
        meta.get("signature_alg") != "ed25519"):
        raise ValueError("Signed contract cannot be promoted to production")
    artifact = meta["artifact"]
    if artifact["size"] != len(db) or artifact["sha256"] != hashlib.sha256(db).hexdigest():
        raise ValueError("Signed artifact checksum mismatch")
    return {"release_id": meta["release_id"], "trusted_signature": True,
            "production_eligible": False}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--preview", default="target-duty-preview.zip")
    p.add_argument("--output", default="target-duty-signed-review.zip")
    p.add_argument("--key-id", required=True)
    args = p.parse_args()
    seed = os.environ.get("TAX_V2_ED25519_PRIVATE_SEED_HEX", "")
    if not seed:
        raise SystemExit("TAX_V2_ED25519_PRIVATE_SEED_HEX is not configured")
    print(json.dumps(sign_review(args.preview, args.output, args.key_id, seed),
                     indent=2))


if __name__ == "__main__":
    main()
