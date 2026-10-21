#!/usr/bin/env python3
"""测试 auto_update_single 集成 should_delete_code（Phase 4 Step 3）

验证路径2/3（单条+批量更新）能识别"200 无税率"的废弃 code 并删除，
而非仅识别 HTTP 404。核心判定逻辑由 test_delete_logic.py 覆盖，
此处验证【接线】正确：双地无税率 → delete_tariff 被调用。

外部依赖（网络/解析/持久化）用 fakes 替换，测试真实删除行为。
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from src.core.scraper import TariffScraper


class FakeDB:
    """记录 delete_tariff 调用的内存 DB"""
    def __init__(self, old_rate='5%'):
        self.deleted = []
        self.old_rate = old_rate

    def get_tariff(self, code):
        return {'code': code, 'rate': self.old_rate, 'description': 'old'}

    def clear_scrape_error(self, code):
        pass

    def delete_tariff(self, code):
        self.deleted.append(code)

    def add_scrape_error(self, code, msg):
        pass

    def update_tariff(self, **kwargs):
        pass


class NoRateParser:
    """模拟废弃页（如 9403208000 重定向后）：parse 得 rate=''"""
    def parse_commodity_page(self, content, url=''):
        return {'code': '9403208000', 'rate': '', 'description': ''}


class ValidParser:
    """模拟有效页：parse 得正常税率"""
    def parse_commodity_page(self, content, url=''):
        return {'code': '0101210000', 'rate': '5%', 'description': 'horses'}


def _make_scraper(old_rate='5%'):
    s = TariffScraper.__new__(TariffScraper)
    s.db = FakeDB(old_rate)
    s.headers = {}
    s.max_retries = 1
    return s


def test_auto_update_deletes_when_both_no_rate(monkeypatch):
    """UK+NI 都 200 但无 duty rate → 应删除（废弃 code 如 9403208000）"""
    s = _make_scraper(old_rate='5%')  # DB 旧数据有税率，证明是"变废弃"

    async def fake_scrape(urls):
        return [(200, '<html>')]

    monkeypatch.setattr(s, 'scrape_with_retry', fake_scrape)
    monkeypatch.setattr(s, '_create_temp_parser', lambda: NoRateParser())

    asyncio.run(s.auto_update_single('9403208000', 'http://uk', 'http://ni'))

    assert '9403208000' in s.db.deleted, \
        f"双地无税率应删除，实际删除: {s.db.deleted}"


def test_auto_update_keeps_when_has_rate(monkeypatch):
    """UK+NI 有有效税率 → 不删除（回归保护，防误删）"""
    s = _make_scraper(old_rate='5%')

    async def fake_scrape(urls):
        return [(200, '<html>')]

    monkeypatch.setattr(s, 'scrape_with_retry', fake_scrape)
    monkeypatch.setattr(s, '_create_temp_parser', lambda: ValidParser())

    asyncio.run(s.auto_update_single('0101210000', 'http://uk', 'http://ni'))

    assert s.db.deleted == [], f"有税率不应删除，实际删除: {s.db.deleted}"
