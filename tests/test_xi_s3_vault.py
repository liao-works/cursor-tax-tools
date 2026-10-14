"""Offline disaster drill for XI S3 vault: Object Lock, KMS, version pin and replay.

The test transport simulates AWS S3 API responses; it makes NO real AWS
calls or claims that an offsite bucket is configured.
"""
from __future__ import annotations

import base64
import json
import shutil
import tempfile
import unittest
from datetime import datetime,timezone
from pathlib import Path

import test_xi_archive as fixtures

from src.target_duty.xi_s3_vault import store,recover
from src.target_duty.xi_frozen_catalogue import digest_file

BUCKET="xi-tariff-review-archive"
OWNER="123456789012"
KMS="arn:aws:kms:ap-southeast-1:123456789012:key/abcd-1234"


class FakeS3:
    def __init__(self,root):
        self.root=Path(root)
        self.versioning="Enabled"
        self.lock_enabled="Enabled"
        self.changed_head={}
        self.damage_download=False
        self.calls=[]
        self.writes={}
        self.version="review-version-123"
        self.immutable=True

    @staticmethod
    def opt(args,key):
        return args[args.index(key)+1]

    def __call__(self,args):
        self.calls.append(args)
        op=args[0]
        if op=="get-bucket-versioning":
            return {"Status":self.versioning}
        if op=="get-object-lock-configuration":
            return {"ObjectLockConfiguration":{"ObjectLockEnabled":self.lock_enabled}}
        if op=="put-object":
            assert self.opt(args,"--if-none-match")=="*"
            assert self.opt(args,"--server-side-encryption")=="aws:kms"
            assert self.opt(args,"--object-lock-mode")=="COMPLIANCE"
            key=self.opt(args,"--key")
            if key in self.writes:
                raise ValueError("S3 412 Precondition Failed: protected immutable key")
            original=Path(self.opt(args,"--body"))
            sha=digest_file(str(original))
            assert self.opt(args,"--checksum-sha256")==base64.b64encode(bytes.fromhex(sha)).decode()
            file=self.root/"simulated-cloud-archive.zip"
            shutil.copy2(original,file)
            self.writes[key]={
                "file":file,"sha":sha,
                "head":{
                    "VersionId":self.version,
                    "ContentLength":original.stat().st_size,
                    "ServerSideEncryption":"aws:kms",
                    "SSEKMSKeyId":self.opt(args,"--ssekms-key-id"),
                    "ObjectLockMode":"COMPLIANCE",
                    "ObjectLockRetainUntilDate":self.opt(args,"--object-lock-retain-until-date"),
                    "Metadata":{"sha256":sha,"source-scope":"managed-full"},
                }
            }
            return {"VersionId":self.version}
        if op=="head-object":
            key=self.opt(args,"--key")
            if self.opt(args,"--version-id")!=self.version:
                raise ValueError("Pinned version mismatch")
            return {**self.writes[key]["head"],**self.changed_head}
        if op=="get-object":
            key=self.opt(args,"--key")
            assert self.opt(args,"--version-id")==self.version
            destination=Path(args[-1])
            assert not destination.exists()
            b=self.writes[key]["file"].read_bytes()
            destination.write_bytes(b[:-1]+b"x" if self.damage_download else b)
            return {"ContentLength":len(b)}
        raise AssertionError("Unknown S3 operation "+op)


@unittest.skipUnless(shutil.which("openssl"),"OpenSSL Ed25519 required")
class SourceS3VaultTests(unittest.TestCase):
    def setUp(self):
        # Re-use the independently tested real signed managed 97-chapter
        # fixture; never bypass signed ZIP or original-source replay.
        instance=fixtures.SignedXIArchiveTests(
            methodName="test_managed_signed_root_restores_full_verified_source_but_not_duty")
        instance.setUp()
        self.addCleanup(instance.doCleanups)
        self.fixture=instance
        self.fixture._pack()
        self.cloud=FakeS3(self.fixture.root)
        self.kw={
            "bucket":BUCKET,"owner":OWNER,"kms_key":KMS,
            "public_key":str(self.fixture.pub),"prefix":"xi-checkpoints",
            "runner":self.cloud
        }

    def test_versioned_kms_compliance_put_exact_download_and_offline_recovery(self):
        upload=store(str(self.fixture.archive),**self.kw)
        self.assertTrue(upload["sse_kms_verified"])
        self.assertTrue(upload["operator_signature_verified"])
        self.assertTrue(upload["raw_source_replay_verified"])
        self.assertEqual(upload["object_lock_mode"],"COMPLIANCE")
        self.assertEqual(upload["s3_version_id"],self.cloud.version)
        self.assertFalse(upload["ni_other_verified"])
        self.assertFalse(upload["offsite_replication_proven"])
        receipt=self.fixture.root/"trusted-vault-pointer.json"
        receipt.write_text(json.dumps(upload))
        restored=self.fixture.root/"manual-disaster-recovery"
        result=recover(str(receipt),str(restored),
                       trusted_public_key=str(self.fixture.pub),runner=self.cloud)
        self.assertTrue(result["raw_source_replay_verified"])
        self.assertTrue(result["operator_signature_verified"])
        self.assertTrue((restored/"checkpoint.db").exists())
        self.assertFalse(result["production_eligible"])
        with self.assertRaisesRegex(ValueError,"replace existing"):
            recover(str(receipt),str(restored),
                    trusted_public_key=str(self.fixture.pub),runner=self.cloud)
        with self.assertRaisesRegex(ValueError,"Precondition Failed"):
            store(str(self.fixture.archive),**self.kw)

    def test_wrong_operator_key_rejected_before_any_immutable_s3_call(self):
        with self.assertRaisesRegex(ValueError,"signature operation failed"):
            store(str(self.fixture.archive),
                  **{**self.kw,"public_key":str(self.fixture.wrong_pub)})
        self.assertEqual(self.cloud.calls,[])
        self.assertFalse(self.cloud.writes)

    def test_modified_signed_archive_cannot_create_locked_undeletable_object(self):
        import zipfile
        edited=self.fixture.root/"forged-source.zip"
        with zipfile.ZipFile(self.fixture.archive) as old,zipfile.ZipFile(edited,"w") as out:
            for name in old.namelist():
                payload=old.read(name)
                if name=="checkpoint.db":
                    payload+=b"F"
                out.writestr(name,payload)
        with self.assertRaises(ValueError):
            store(str(edited),**self.kw)
        self.assertEqual(self.cloud.calls,[])

    def test_bucket_versioning_or_object_lock_disabled_prevents_any_put(self):
        self.cloud.versioning="Suspended"
        with self.assertRaisesRegex(ValueError,"versioning"):
            store(str(self.fixture.archive),**self.kw)
        self.assertFalse(self.cloud.writes)
        self.cloud.versioning="Enabled"
        self.cloud.lock_enabled="Disabled"
        with self.assertRaisesRegex(ValueError,"Object Lock"):
            store(str(self.fixture.archive),**self.kw)
        self.assertFalse(self.cloud.writes)

    def test_wrong_kms_or_short_object_retention_never_claims_uploaded_usable(self):
        self.cloud.changed_head["ServerSideEncryption"]="AES256"
        with self.assertRaisesRegex(ValueError,"metadata mismatch"):
            store(str(self.fixture.archive),**self.kw)
        self.cloud=FakeS3(self.fixture.root)
        self.cloud.changed_head["ObjectLockRetainUntilDate"]="2026-01-01T00:00:00Z"
        with self.assertRaisesRegex(ValueError,"shorter than required"):
            store(str(self.fixture.archive),**{**self.kw,"runner":self.cloud})

    def test_downloaded_object_corruption_fails_own_sha_before_archive_restore(self):
        self.cloud.damage_download=True
        with self.assertRaisesRegex(ValueError,"downloaded pinned version differs"):
            store(str(self.fixture.archive),**self.kw)

    def test_bad_owner_kms_or_untrusted_receipt_cannot_select_arbitrary_object(self):
        with self.assertRaisesRegex(ValueError,"Unsafe S3"):
            store(str(self.fixture.archive),**{**self.kw,"owner":"999"})
        with self.assertRaisesRegex(ValueError,"Unsafe S3"):
            store(str(self.fixture.archive),**{**self.kw,"kms_key":"alias/maybe"})
        self.assertFalse(self.cloud.writes)
        upload=store(str(self.fixture.archive),**self.kw)
        receipt=self.fixture.root/"pointer.json"
        upload["s3_key"]="../attacker/archive.zip"
        receipt.write_text(json.dumps(upload))
        with self.assertRaisesRegex(ValueError,"content-addressed"):
            recover(str(receipt),str(self.fixture.root/"unsafe"),
                    trusted_public_key=str(self.fixture.pub),runner=self.cloud)
        self.assertFalse((self.fixture.root/"unsafe").exists())

    def test_existing_pinned_version_missing_or_changed_blocks_recovery(self):
        upload=store(str(self.fixture.archive),**self.kw)
        receipt=self.fixture.root/"pointer.json"
        receipt.write_text(json.dumps(upload))
        self.cloud.changed_head["SSEKMSKeyId"]="wrong"
        with self.assertRaisesRegex(ValueError,"no longer meets"):
            recover(str(receipt),str(self.fixture.root/"not-restored"),
                    trusted_public_key=str(self.fixture.pub),runner=self.cloud)
        self.assertFalse((self.fixture.root/"not-restored").exists())


if __name__=="__main__":
    unittest.main()
