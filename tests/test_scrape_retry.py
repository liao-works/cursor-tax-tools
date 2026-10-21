#!/usr/bin/env python3
"""测试 scrape_with_retry 不再把抓取失败伪装成 404（Phase 4 Step 2）

背景（Phase 1 根因）：原实现重试耗尽时统一返回 [(404, "")]，
导致"服务器错误/网络超时"被误判为"商品不存在"，
进而在全量抓取路径(scrape_tariffs)触发 delete_tariff 误删有效商品。

修复规约：重试耗尽时返回【最后一次真实结果】；
若从未拿到结果（全异常）返回 (0, None)。
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import src.core.scraper as scraper_mod
from src.core.scraper import TariffScraper


async def _noop_sleep(*args, **kwargs):
    """替换 asyncio.sleep，测试不实际等待"""
    return None


def _make_scraper(retries=2):
    """用 __new__ 构造轻量实例，避免触发 __init__ 的 DB 连接"""
    s = TariffScraper.__new__(TariffScraper)
    s.headers = {}
    s.max_retries = retries
    return s


def test_no_200_keeps_last_real_status(monkeypatch):
    """服务器错误(500)等非200状态应原样返回，不被伪装成404"""
    async def fake(urls, headers=None):
        return [(500, None)]
    monkeypatch.setattr(scraper_mod, 'scrape_urls', fake)
    s = _make_scraper(retries=2)
    results = asyncio.run(s.scrape_with_retry(['http://x']))
    assert results == [(500, None)], f"应保留真实500，实际: {results}"


def test_real_404_status_preserved(monkeypatch):
    """真实404应原样保留（content 为 None，而非空串）"""
    async def fake(urls, headers=None):
        return [(404, None)]
    monkeypatch.setattr(scraper_mod, 'scrape_urls', fake)
    s = _make_scraper(retries=2)
    results = asyncio.run(s.scrape_with_retry(['http://x']))
    assert results == [(404, None)], f"真404应原样保留，实际: {results}"


def test_all_exceptions_return_zero_status(monkeypatch):
    """全部重试都抛异常时应返回 (0, None)，绝不可伪装成 (404, '')"""
    async def fake(urls, headers=None):
        raise Exception("network timeout")
    monkeypatch.setattr(scraper_mod, 'scrape_urls', fake)
    monkeypatch.setattr(asyncio, 'sleep', _noop_sleep)
    s = _make_scraper(retries=2)
    results = asyncio.run(s.scrape_with_retry(['http://x']))
    assert results == [(0, None)], f"全异常应返回(0,None)，实际: {results}"


def test_returns_immediately_on_200(monkeypatch):
    """任一200应立即返回，不重试（现有正确行为的回归保护）"""
    calls = {'n': 0}

    async def fake(urls, headers=None):
        calls['n'] += 1
        return [(200, 'html')]
    monkeypatch.setattr(scraper_mod, 'scrape_urls', fake)
    s = _make_scraper(retries=3)
    results = asyncio.run(s.scrape_with_retry(['http://x']))
    assert results == [(200, 'html')]
    assert calls['n'] == 1, f"有200应只调用1次，实际: {calls['n']}"
