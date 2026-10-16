"""Signed, portable XI *raw-source checkpoint* archives (NOT duty publications).

A managed-full archive requires an operator Ed25519 signature verified using
a separately trusted public key. No private key is embedded in archive.
Public/fixture canaries are explicitly UNSIGNED and never qualify as managed.
A signature authenticates this operator's bytes, NOT HMRC source atomicity or
NI 'Other' applicability.

Archive members are a fixed allowlist: no extractall, zip-slip, symlinks,
extra entries, unbounded extraction, fallback to an unsigned archive, or
replacement of an existing restored destination.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import stat
import subprocess
import tempfile
import zipfile
from pathlib import Path

from .xi_checkpoint_chain import verify as verify_chain
from .xi_frozen_catalogue import digest_file

FORMAT = "xi-signed-raw-checkpoint-archive-v1"
CORE = {
    "catalogue.db": "catalogue",
    "frozen.json": "frozen_manifest",
    "checkpoint.db": "checkpoint",
    "checkpoint.json": "merged_report",
    "chain.json": "chain",
}
PARENT = {
    "parent-checkpoint.db": "previous_db",
    "parent-checkpoint.json": "previous_report",
    "parent-chain.json": "previous_chain",
}
MAX_TOTAL = 6_000_000_000
MAX_MEMBERS = 10
MANIFEST = "receipt.json"
SIGNATURE = "receipt.ed25519"


def _canonical(data: dict) -> bytes:
    return (json.dumps(data,ensure_ascii=False,sort_keys=True,
                       separators=(",",":"),allow_nan=False)+"\n").encode()


def _openssl(args: list[str]) -> None:
    try:
        r=subprocess.run(["openssl",*args],stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE,timeout=60,check=False)
    except (OSError,subprocess.TimeoutExpired) as exc:
        raise ValueError("OpenSSL Ed25519 unavailable") from exc
    if r.returncode:
        raise ValueError("Ed25519 signature operation failed")


def _signature(payload: bytes, *, private_key: str | None=None,
               public_key: str | None=None, signature: bytes | None=None) -> bytes | None:
    with tempfile.TemporaryDirectory() as td:
        root=Path(td)
        msg=root/"receipt.json"
        msg.write_bytes(payload)
        sig=root/"receipt.sig"
        if private_key:
            _openssl(["pkeyutl","-sign","-rawin","-inkey",private_key,
                      "-in",str(msg),"-out",str(sig)])
            raw=sig.read_bytes()
            if len(raw)!=64:
                raise ValueError("Invalid Ed25519 signature length")
            return raw
        if not public_key or signature is None or len(signature)!=64:
            raise ValueError("Managed archive requires a separate trusted Ed25519 public key")
        sig.write_bytes(signature)
        _openssl(["pkeyutl","-verify","-pubin","-rawin",
                  "-inkey",public_key,"-in",str(msg),"-sigfile",str(sig)])
        return None


def _sources(catalogue: str, checkpoint: str, merged_report: str,
             chain: str, *, frozen_manifest: str | None,
             previous_db: str | None, previous_report: str | None,
             previous_chain: str | None, public_canary: bool) -> tuple[dict,dict]:
    trio=(previous_db,previous_report,previous_chain)
    if any(trio) and not all(trio):
        raise ValueError("XI archive requires all three previous-checkpoint files")
    if public_canary and frozen_manifest:
        raise ValueError("Public XI canary cannot carry a managed frozen manifest")
    if not public_canary and not frozen_manifest:
        raise ValueError("Managed XI archive requires original frozen manifest")
    proof=verify_chain(
        catalogue,checkpoint,merged_report,chain,
        manifest=frozen_manifest,public_canary=public_canary,
        previous_db=previous_db,previous_report=previous_report,
        previous_chain=previous_chain)
    if proof["parent_chain_sha256"] is None and any(trio):
        raise ValueError("XI archive root cannot include spurious parent files")
    if proof["parent_chain_sha256"] is not None and not all(trio):
        raise ValueError("XI archive child requires its parent checkpoint and chain")
    inputs={
        "catalogue.db":catalogue,
        "checkpoint.db":checkpoint,
        "checkpoint.json":merged_report,
        "chain.json":chain,
    }
    if frozen_manifest:
        inputs["frozen.json"]=frozen_manifest
    for member,key in PARENT.items():
        value={"previous_db":previous_db,"previous_report":previous_report,
               "previous_chain":previous_chain}[key]
        if value:
            inputs[member]=value
    paths=[Path(x).resolve() for x in inputs.values()]
    if len(paths)!=len(set(paths)):
        raise ValueError("XI archive file inputs must be distinct")
    for file in paths:
        if not file.is_file() or file.is_symlink():
            raise ValueError("XI archive input is missing or symlinked")
    return proof,inputs


def pack(output: str, *, catalogue: str,checkpoint: str,
         merged_report: str,chain: str,frozen_manifest: str | None=None,
         previous_db: str | None=None,previous_report: str | None=None,
         previous_chain: str | None=None,public_canary: bool=False,
         signing_private_key: str | None=None) -> dict:
    if public_canary and signing_private_key:
        raise ValueError("XI public canary archive must remain unsigned")
    if not public_canary and not signing_private_key:
        raise ValueError("Managed XI archive requires an operator Ed25519 private key")
    proof,files=_sources(
        catalogue,checkpoint,merged_report,chain,
        frozen_manifest=frozen_manifest,previous_db=previous_db,
        previous_report=previous_report,previous_chain=previous_chain,
        public_canary=public_canary)
    target=Path(output).resolve()
    if target in {Path(s).resolve() for s in files.values()}:
        raise ValueError("Archive output must not overwrite original source")
    if target.exists():
        raise ValueError("Refusing to replace existing XI source archive")
    total=sum(Path(f).stat().st_size for f in files.values())
    if total>MAX_TOTAL:
        raise ValueError("XI source archive exceeds safe uncompressed size limit")
    entries={member:{"sha256":digest_file(file),"bytes":Path(file).stat().st_size}
             for member,file in sorted(files.items())}
    receipt={
        "kind":FORMAT,
        "source_scope":proof["source_scope"],
        "as_of":proof["as_of"],
        "catalogue_sha256":proof["catalogue_sha256"],
        "checkpoint_sha256":proof["checkpoint_sha256"],
        "chain_sha256":digest_file(chain),
        "parent_chain_sha256":proof["parent_chain_sha256"],
        "files":entries,
        "signature_algorithm":"Ed25519" if not public_canary else "none",
        "operator_signature_verified":False,
        "production_eligible":False,
        "ni_other_verified":False,
        "atomic_official_snapshot_verified":False,
        "warning":"Signed operator review of source bytes; NOT HMRC atomic snapshot or payable NI Other duty.",
    }
    raw=_canonical(receipt)
    signature=_signature(raw,private_key=signing_private_key) if signing_private_key else None
    target.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(dir=target.parent) as td:
        archive=Path(td)/"source.zip"
        with zipfile.ZipFile(archive,"w",compression=zipfile.ZIP_DEFLATED,
                             compresslevel=6,allowZip64=True) as z:
            z.writestr(MANIFEST,raw)
            if signature is not None:
                z.writestr(SIGNATURE,signature)
            for member,file in sorted(files.items()):
                z.write(file,arcname=member)
        if not zipfile.is_zipfile(archive):
            raise ValueError("XI review archive damaged during creation")
        os.replace(archive,target)
    return {
        "kind":FORMAT, "archive_sha256":digest_file(str(target)),
        "archive_bytes":target.stat().st_size,
        "members":len(entries),
        "source_scope":receipt["source_scope"],
        "operator_signature_present":signature is not None,
        "production_eligible":False,
        "ni_other_verified":False,
    }


def restore(archive: str,destination: str,*,trusted_public_key: str | None=None,
            public_canary: bool=False) -> dict:
    source=Path(archive).resolve()
    dest=Path(destination).resolve()
    if not source.is_file() or source.is_symlink():
        raise ValueError("XI source archive file missing or symlinked")
    if dest.exists():
        raise ValueError("Refuse to overwrite existing XI restored destination")
    if dest == source or source in dest.parents:
        raise ValueError("Unsafe XI restore destination")
    dest.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(dir=dest.parent) as td:
        stage=Path(td)/"restored"
        stage.mkdir()
        with zipfile.ZipFile(source,"r",allowZip64=True) as z:
            contents=z.infolist()
            members=[x.filename for x in contents]
            if len(members)!=len(set(members)) or len(members)>MAX_MEMBERS:
                raise ValueError("XI archive has duplicate or excessive entries")
            if MANIFEST not in members:
                raise ValueError("XI archive receipt missing")
            if any(x.is_dir() or stat.S_ISLNK(x.external_attr>>16)
                   or "/" in x.filename or "\\" in x.filename or
                   x.flag_bits & 1 for x in contents):
                raise ValueError("XI archive contains unsafe path, symlink or encrypted entry")
            if sum(x.file_size for x in contents)>MAX_TOTAL:
                raise ValueError("XI archive uncompressed content too large")
            if z.getinfo(MANIFEST).file_size>65536:
                raise ValueError("XI receipt metadata too large")
            manifest_bytes=z.read(MANIFEST)
            receipt=json.loads(manifest_bytes)
            if _canonical(receipt)!=manifest_bytes:
                raise ValueError("Noncanonical XI source receipt")
            if (receipt.get("kind")!=FORMAT
                or receipt.get("production_eligible") is not False
                or receipt.get("ni_other_verified") is not False
                or receipt.get("atomic_official_snapshot_verified") is not False):
                raise ValueError("XI source archive illegally claims production duty")
            signed=not public_canary
            if signed:
                if (receipt.get("source_scope")!="managed_full"
                    or receipt.get("signature_algorithm")!="Ed25519"
                    or SIGNATURE not in members):
                    raise ValueError("Managed XI archive must be signed")
                _signature(manifest_bytes,public_key=trusted_public_key,
                           signature=z.read(SIGNATURE))
            else:
                if (receipt.get("source_scope")!="partial_or_fixture"
                    or receipt.get("signature_algorithm")!="none"
                    or SIGNATURE in members):
                    raise ValueError("Public XI canary archive cannot impersonate managed signature")
            files=receipt.get("files")
            expected=set(CORE if signed else {k:v for k,v in CORE.items() if k!="frozen.json"})
            extras=set(PARENT) if receipt.get("parent_chain_sha256") else set()
            if not isinstance(files,dict) or set(files)!=expected|extras:
                raise ValueError("XI source archive missing or unexpected evidence members")
            if set(members)!=set(files)|{MANIFEST}|({SIGNATURE} if signed else set()):
                raise ValueError("Unexpected member of XI signed source archive")
            total=0
            for name,claim in sorted(files.items()):
                if (not isinstance(claim,dict) or set(claim)!={"sha256","bytes"}
                    or not isinstance(claim["bytes"],int)
                    or isinstance(claim["bytes"],bool)
                    or claim["bytes"]<0 or claim["bytes"]>MAX_TOTAL
                    or not isinstance(claim["sha256"],str)
                    or len(claim["sha256"])!=64):
                    raise ValueError("XI source archive entry claim malformed")
                total+=claim["bytes"]
                if total>MAX_TOTAL or claim["bytes"]!=z.getinfo(name).file_size:
                    raise ValueError("XI source archive entry size mismatch")
                h=hashlib.sha256()
                count=0
                with z.open(name) as inp,(stage/name).open("xb") as out:
                    while part:=inp.read(1024*1024):
                        count+=len(part)
                        if count>claim["bytes"]:
                            raise ValueError("XI source archive member decompression exceeded declared size")
                        out.write(part)
                        h.update(part)
                if count!=claim["bytes"] or h.hexdigest()!=claim["sha256"]:
                    raise ValueError("XI source archive member SHA-256 mismatch: "+name)
        # Deep source replay BEFORE promoting any extracted checkpoint.
        proof,_=_sources(
            str(stage/"catalogue.db"),str(stage/"checkpoint.db"),
            str(stage/"checkpoint.json"),str(stage/"chain.json"),
            frozen_manifest=str(stage/"frozen.json") if signed else None,
            previous_db=str(stage/"parent-checkpoint.db") if extras else None,
            previous_report=str(stage/"parent-checkpoint.json") if extras else None,
            previous_chain=str(stage/"parent-chain.json") if extras else None,
            public_canary=public_canary)
        if (proof["source_scope"]!=receipt["source_scope"]
            or proof["as_of"]!=receipt["as_of"]
            or proof["catalogue_sha256"]!=receipt["catalogue_sha256"]
            or proof["checkpoint_sha256"]!=receipt["checkpoint_sha256"]
            or digest_file(str(stage/"chain.json"))!=receipt["chain_sha256"]
            or proof["parent_chain_sha256"]!=receipt["parent_chain_sha256"]):
            raise ValueError("XI source archive semantic replay differs from signed receipt")
        os.replace(stage,dest)
    return {
        "kind":FORMAT,
        "archive_sha256":digest_file(str(source)),
        "source_scope":receipt["source_scope"],
        "restored_to":str(dest),
        "operator_signature_verified":signed,
        "source_replay_verified":True,
        "production_eligible":False,
        "ni_other_verified":False,
        "atomic_official_snapshot_verified":False,
    }


def main() -> None:
    p=argparse.ArgumentParser(description=__doc__)
    sub=p.add_subparsers(dest="command",required=True)
    create=sub.add_parser("pack")
    create.add_argument("--output",required=True)
    create.add_argument("--catalogue",required=True)
    create.add_argument("--frozen-manifest")
    create.add_argument("--checkpoint",required=True)
    create.add_argument("--merge-report",required=True)
    create.add_argument("--chain",required=True)
    create.add_argument("--previous-checkpoint")
    create.add_argument("--previous-report")
    create.add_argument("--previous-chain")
    create.add_argument("--public-canary",action="store_true")
    create.add_argument("--signing-private-key")
    recover=sub.add_parser("restore")
    recover.add_argument("--archive",required=True)
    recover.add_argument("--destination",required=True)
    recover.add_argument("--trusted-public-key")
    recover.add_argument("--public-canary",action="store_true")
    a=p.parse_args()
    if a.command=="pack":
        result=pack(a.output,catalogue=a.catalogue,checkpoint=a.checkpoint,
                    merged_report=a.merge_report,chain=a.chain,
                    frozen_manifest=a.frozen_manifest,
                    previous_db=a.previous_checkpoint,
                    previous_report=a.previous_report,
                    previous_chain=a.previous_chain,
                    public_canary=a.public_canary,
                    signing_private_key=a.signing_private_key)
    else:
        result=restore(a.archive,a.destination,
                       trusted_public_key=a.trusted_public_key,
                       public_canary=a.public_canary)
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=="__main__":
    main()
