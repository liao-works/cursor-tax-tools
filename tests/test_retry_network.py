#!/usr/bin/env python3
"""测试网络错误重试（Phase 4 Step 1 RED）

CI 中 400/500 的 code：末尾重试 3 次（长间隔 30/60/120s），
成功则补全，仍失败返回 failed_codes（写入 metadata 供客户端补全）。
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from src.core.scraper import TariffScraper


async def _noop_sleep(*args, **kwargs):
    return None


class FakeDB:
    def __init__(self):
        self.added = []

    def add_tariff(self, **kwargs):
        self.added.append(kwargs.get('code'))


def _scraper():
    s = TariffScraper.__new__(TariffScraper)
    s.headers = {}
    s.max_retries = 1
    s.existing_codes = set()
    s.base_url = "https://www.trade-tariff.service.gov.uk"
    s.db = FakeDB()
    return s


def test_retry_succeeds_on_later_attempt(monkeypatch):
    """重试后成功 → 补全(add_tariff)，不进 failed_codes"""
    s = _scraper()
    attempts = {'n': 0}

    async def fake_scrape(urls):
        attempts['n'] += 1
        # 第1次 500，第2次（重试1）成功
        return [(200, '<html>') if attempts['n'] >= 2 else (500, None)]

    monkeypatch.setattr(s, 'scrape_with_retry', fake_scrape)
    monkeypatch.setattr(s, 'parse_commodity_page', lambda c, url='': {'code': 'X', 'rate': '5%', 'description': 'd'})
    monkeypatch.setattr(asyncio, 'sleep', _noop_sleep)

    failed = asyncio.run(s.retry_network_errors([('X', 500)]))

    assert failed == [], f"重试成功不应有 failed: {failed}"
    assert 'X' in s.db.added, "成功应补全(add_tariff)"


def test_retry_all_fail_returns_failed_codes(monkeypatch):
    """3 次重试都失败 → 返回 failed_codes（含完整字段）"""
    s = _scraper()

    async def fake_scrape(urls):
        return [(500, None)]  # 持续失败

    monkeypatch.setattr(s, 'scrape_with_retry', fake_scrape)
    monkeypatch.setattr(asyncio, 'sleep', _noop_sleep)

    failed = asyncio.run(s.retry_network_errors([('X', 500), ('Y', 400)]))

    assert len(failed) == 2
    codes = {f['code'] for f in failed}
    assert codes == {'X', 'Y'}
    # 验证字段契约（供客户端补全）
    for f in failed:
        assert f['retries'] == 3, f"应重试 3 次: {f}"
        assert 'status' in f and 'error_type' in f and 'last_attempt' in f
    # error_type 分类
    type_map = {f['code']: f['error_type'] for f in failed}
    assert type_map['X'] == 'server_error'  # 500
    assert type_map['Y'] == 'client_error'  # 400


def test_retry_400_client_error_not_discriminated(monkeypatch):
    """400 也是网络错误，同样重试（client_error）"""
    s = _scraper()
    async def fake_scrape(urls):
        return [(400, None)]
    monkeypatch.setattr(s, 'scrape_with_retry', fake_scrape)
    monkeypatch.setattr(asyncio, 'sleep', _noop_sleep)
    failed = asyncio.run(s.retry_network_errors([('Z', 400)]))
    assert failed[0]['error_type'] == 'client_error'
