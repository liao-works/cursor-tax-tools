import hashlib
import json
import sqlite3
import tempfile
import unittest
import zipfile
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from src.target_duty.signed_review import sign_review, verify_review


class SignedReviewTests(unittest.TestCase):
    def create_preview(self, target):
        db_file = target.with_suffix(".db")
        with sqlite3.connect(db_file) as connection:
            connection.execute("CREATE TABLE tariffs (code TEXT PRIMARY KEY,rate TEXT,"
                               "uk_rate_status TEXT,ni_rate_status TEXT)")
            connection.executemany(
                "INSERT INTO tariffs VALUES (?,?,'verified','pending')",
                [(f"{i:010}", "0%") for i in range(120)])
        body = db_file.read_bytes()
        unsigned = {
            "schema_version": 2, "channel": "shadow-preview",
            "release_id": "target-duty-test-001",
            "signed": False, "production_eligible": False,
            "source": {"measure_type": "103", "geographical_area": "1011"},
            "as_of": "2026-10-13",
            "quality": {"record_count": 120, "gb_verified": 120,
                        "gb_conditional": 0, "ni_unverified": 120},
            "artifact": {"name": "target-duty.db",
                         "size": len(body),
                         "sha256": hashlib.sha256(body).hexdigest()}
        }
        with zipfile.ZipFile(target, "w") as z:
            z.writestr("manifest.json", json.dumps(unsigned))
            z.writestr("target-duty.db", body)

    def test_signature_is_over_exact_manifest_and_rejects_tampering(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            preview, signed = base/"preview.zip", base/"signed.zip"
            self.create_preview(preview)
            seed = bytes(range(32))
            public = Ed25519PrivateKey.from_private_bytes(seed).public_key().public_bytes(
                encoding=serialization.Encoding.Raw,
                format=serialization.PublicFormat.Raw).hex()
            report = sign_review(str(preview), str(signed), "test-fixture",
                                 seed.hex())
            self.assertFalse(report["production_eligible"])
            verified = verify_review(str(signed), public)
            self.assertTrue(verified["trusted_signature"])
            with zipfile.ZipFile(signed) as z:
                signed_manifest = z.read("manifest.json")
                original_sig = z.read("manifest.sig")
                sqlite = z.read("target-duty.db")
            tampered = base/"tampered.zip"
            with zipfile.ZipFile(tampered, "w") as z:
                z.writestr("manifest.json", signed_manifest.replace(
                    b"signed-shadow-review", b"signed-shadow-reviex"))
                z.writestr("manifest.sig", original_sig)
                z.writestr("target-duty.db", sqlite)
            with self.assertRaises(Exception):
                verify_review(str(tampered), public)
            with self.assertRaises(ValueError):
                sign_review(str(preview), str(base/"bad.zip"), "../x", seed.hex())

    def test_refuses_misleading_or_absent_private_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            preview = Path(tmp)/"preview.zip"
            self.create_preview(preview)
            with self.assertRaises(ValueError):
                sign_review(str(preview), str(Path(tmp)/"signed.zip"),
                            "my-key", "")
            with self.assertRaises(ValueError):
                sign_review(str(preview), str(preview),
                            "my-key", bytes(range(32)).hex())

if __name__ == "__main__":
    unittest.main()
