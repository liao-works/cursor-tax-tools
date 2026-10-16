"""Lightweight, offline-testable desktop data-release notification policy.

This compares the PUBLISHED latest-data SQLite asset hash, not raw GB/XI
collection artifacts and not the desktop application's executable version.
No network requests or database mutations take place in this module.
"""
from __future__ import annotations

import re
from typing import Mapping

DEFAULT_METADATA_URL = (
    "https://github.com/liao-works/cursor-tax-tools/"
    "releases/download/latest-data/metadata.json"
)
OLD_METADATA_URL = (
    "https://github.com/CavinHuang/cursor-tax-tools/"
    "releases/download/latest-data/metadata.json"
)

CHECK_INTERVAL_MS = 24 * 60 * 60 * 1000
RETRY_INTERVAL_MS = 60 * 60 * 1000
_SHA256 = re.compile(r"sha256:[a-fA-F0-9]{64}\Z")


def configured_metadata_url(url: str | None) -> str:
    """Migrate only the known retired default; retain user custom endpoints."""
    value = (url or "").strip()
    return DEFAULT_METADATA_URL if not value or value == OLD_METADATA_URL else value


def assess_published_update(remote: Mapping, local: Mapping, local_metadata=None):
    """Return (needed, reason, detail); unknown/invalid release is an error.

    A change-count threshold, timestamps and branch/Actions snapshots never
    suppress an actual published SQLite hash change.
    """
    if not isinstance(remote, Mapping):
        raise ValueError("远程数据版本信息无效")
    digest = remote.get("file_hash")
    version = remote.get("version")
    if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
        raise ValueError("远程数据缺少有效的 SHA-256 校验值")
    if not isinstance(version, str) or not version.strip():
        raise ValueError("远程数据缺少版本号")
    if not isinstance(local, Mapping):
        raise ValueError("本地数据库状态无效")
    if not local.get("exists"):
        return True, "本地数据尚未安装", {"priority": "high"}
    current = local.get("file_hash")
    if not isinstance(current, str) or not _SHA256.fullmatch(current):
        raise ValueError("无法确认本地数据库的 SHA-256 校验值")
    if digest.lower() == current.lower():
        return False, "本地数据与已发布版本一致", {"priority": "low"}
    prior = (local_metadata or {}).get("version") or "未知版本"
    return True, f"已发布新数据：{prior} → {version}", {"priority": "high"}


def release_notification_key(remote: Mapping) -> str:
    """A release's content hash, independent of its mutable display version."""
    digest = remote.get("file_hash") if isinstance(remote, Mapping) else None
    if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
        raise ValueError("无法识别可信的数据更新")
    return digest.lower()


def check_delay_ms(*, succeeded: bool) -> int:
    """Retry network/metadata failures soon; avoid daily polling storm."""
    return CHECK_INTERVAL_MS if succeeded else RETRY_INTERVAL_MS
