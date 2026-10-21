#!/usr/bin/env python3
"""测试僵尸 code 清理（Phase 4 Step 1 RED）

方案 B（差集 last_updated）：找出 last_updated 早于本次抓取窗口的 code
（未抓到 = 潜在僵尸，如 9403208000 从 heading 链接消失），调 auto_update_single
验证（PR#2 should_delete_code 双地），废弃则删除。
"""
import asyncio
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from src.db.database import TariffDB
from src.actions.cleanup_deprecated import DeprecatedCleaner


def _db(tmp_path):
    return TariffDB(str(tmp_path / "t.db"))


def _now_str():
    return datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')


def test_get_stale_codes_filters_by_last_updated(tmp_path):
    """差集筛选：last_updated 旧的 code 被选出，新 code 不选"""
    db = _db(tmp_path)
    db.conn.execute("INSERT INTO tariffs (code, rate, last_updated) VALUES ('OLD1', '5%', '2020-01-01 00:00:00')")
    db.conn.execute("INSERT INTO tariffs (code, rate, last_updated) VALUES ('OLD2', '5%', '2020-06-01 00:00:00')")
    db.conn.execute("INSERT INTO tariffs (code, rate, last_updated) VALUES ('NEW1', '5%', ?)", (_now_str(),))
    db.conn.commit()

    cleaner = DeprecatedCleaner(db, scraper=None)
    stale = cleaner.get_stale_codes(hours=6)

    assert 'OLD1' in stale and 'OLD2' in stale, f"旧 code 应在差集: {stale}"
    assert 'NEW1' not in stale, "新 code（本次抓取）不应在差集"


def test_cleanup_deletes_stale_deprecated(monkeypatch, tmp_path):
    """cleanup 对差集 code 调 auto_update_single，废弃则删除（9403208000 场景）"""
    db = _db(tmp_path)
    db.conn.execute("INSERT INTO tariffs (code, rate, last_updated) VALUES ('9403208000', '0.00%', '2020-01-01 00:00:00')")
    db.conn.commit()

    cleaner = DeprecatedCleaner(db, scraper=type('S', (), {})())  # 占位 scraper

    async def fake_auto_update(code, uk_url, ni_url=None):
        # 模拟 auto_update_single 的废弃删除
        db.delete_tariff(code)
        return {'overall_success': True, 'message': f'商品编码 {code} 已不存在，已删除记录'}

    monkeypatch.setattr(cleaner.scraper, 'auto_update_single', fake_auto_update, raising=False)

    result = asyncio.run(cleaner.cleanup(hours=6))

    assert result['deleted'] >= 1, f"应删除废弃 code: {result}"
    assert db.get_tariff('9403208000') is None, "9403208000 应被删除"


def test_cleanup_skips_recent_codes(monkeypatch, tmp_path):
    """新 code（本次抓取，last_updated 新）不在差集，不被验证"""
    db = _db(tmp_path)
    db.conn.execute("INSERT INTO tariffs (code, rate, last_updated) VALUES ('NEW1', '5%', ?)", (_now_str(),))
    db.conn.commit()

    cleaner = DeprecatedCleaner(db, scraper=type('S', (), {})())
    called = []

    async def fake_auto_update(code, uk_url, ni_url=None):
        called.append(code)
        return {'message': 'ok'}

    monkeypatch.setattr(cleaner.scraper, 'auto_update_single', fake_auto_update, raising=False)

    asyncio.run(cleaner.cleanup(hours=6))

    assert called == [], "新 code 不应被验证"
