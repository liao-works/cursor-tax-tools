#!/usr/bin/env python3
"""测试末尾重试 transient 失败（Phase 4 Step 3 RED）

验证 retry_transient_failures：
- 成功重试 → 清理失败记录
- 失败重试 → retry_count 递增
- permanent 失败不重试
- 达到 max_retries 不再重试
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from src.core.scraper import TariffScraper


async def _noop_sleep(*args, **kwargs):
    return None


class FakeDB:
    """内存错误记录 DB"""
    def __init__(self, errors):
        self.errors = {e['code']: dict(e) for e in errors}
        self.cleared = []
        self.added = []

    def add_tariff(self, **kwargs):
        self.added.append(kwargs.get('code'))

    def get_scrape_errors(self, code=None):
        if code:
            return [self.errors[code]] if code in self.errors else []
        return list(self.errors.values())

    def clear_scrape_error(self, code):
        self.cleared.append(code)
        self.errors.pop(code, None)

    def increment_retry_count(self, code):
        if code in self.errors:
            self.errors[code]['retry_count'] = self.errors[code].get('retry_count', 0) + 1


def _scraper():
    s = TariffScraper.__new__(TariffScraper)
    s.headers = {}
    s.max_retries = 3
    s.existing_codes = set()
    s.base_url = "https://www.trade-tariff.service.gov.uk"
    return s


def test_retry_transient_success_clears(monkeypatch):
    """transient 失败重试成功 → 清理失败记录"""
    db = FakeDB([{'code': 'X', 'failure_type': 'transient', 'retry_count': 0, 'error_message': 'timeout'}])
    s = _scraper()
    s.db = db

    async def fake_scrape(urls):
        return [(200, '<html>')]

    monkeypatch.setattr(s, 'scrape_with_retry', fake_scrape)
    monkeypatch.setattr(s, 'parse_commodity_page', lambda c, url='': {'code': 'X', 'rate': '5%', 'description': 'd'})
    monkeypatch.setattr(asyncio, 'sleep', _noop_sleep)

    asyncio.run(s.retry_transient_failures())

    assert 'X' in db.cleared, "成功应清理失败记录"
    assert 'X' in db.added, "成功应通过 add_tariff 更新（而非 save_to_db 跳过已存在）"
    assert db.errors.get('X') is None


def test_retry_transient_failure_increments_count(monkeypatch):
    """transient 失败重试仍失败 → retry_count 递增"""
    db = FakeDB([{'code': 'X', 'failure_type': 'transient', 'retry_count': 0, 'error_message': 'timeout'}])
    s = _scraper()
    s.db = db

    async def fake_scrape(urls):
        return [(0, None)]  # 异常

    monkeypatch.setattr(s, 'scrape_with_retry', fake_scrape)
    monkeypatch.setattr(asyncio, 'sleep', _noop_sleep)

    asyncio.run(s.retry_transient_failures())

    assert db.errors['X']['retry_count'] == 1, "失败应递增 retry_count"


def test_permanent_not_retried(monkeypatch):
    """permanent 失败不应被重试"""
    db = FakeDB([{'code': 'P', 'failure_type': 'permanent', 'retry_count': 0, 'error_message': '未找到'}])
    s = _scraper()
    s.db = db

    called = []

    async def fake_scrape(urls):
        called.append(urls)
        return [(200, 'x')]

    monkeypatch.setattr(s, 'scrape_with_retry', fake_scrape)
    monkeypatch.setattr(asyncio, 'sleep', _noop_sleep)

    asyncio.run(s.retry_transient_failures())

    assert called == [], "permanent 不应被重试"
    assert db.errors['P']['retry_count'] == 0


def test_max_retries_not_exceeded(monkeypatch):
    """达到 max_retries 的 transient 失败不再重试"""
    db = FakeDB([{'code': 'X', 'failure_type': 'transient', 'retry_count': 3, 'error_message': 'timeout'}])
    s = _scraper()
    s.db = db

    called = []

    async def fake_scrape(urls):
        called.append(urls)
        return [(200, 'x')]

    monkeypatch.setattr(s, 'scrape_with_retry', fake_scrape)
    monkeypatch.setattr(asyncio, 'sleep', _noop_sleep)

    asyncio.run(s.retry_transient_failures(max_retries=3))

    assert called == [], "达到 max_retries 不应再重试"
