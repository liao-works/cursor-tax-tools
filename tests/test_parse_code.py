#!/usr/bin/env python3
"""测试 parse_commodity_page code 提取（Phase 4 Step 1 RED）

修复根因 #2：parse 对重定向页面用 re.search(soup) 提取 code，
会抓到页面里的 commodity 链接 code（错位）。
修复：优先用 url 的 code。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from src.core.scraper import TariffScraper
from src.db.database import TariffDB


def _scraper(tmp_path):
    s = TariffScraper.__new__(TariffScraper)
    s.db = TariffDB(str(tmp_path / "t.db"))
    s.existing_codes = set()
    return s


def test_parse_uses_url_code_not_soup_link(tmp_path):
    """parse 应优先用 url 的 code，而非 soup 里第一个 /commodities/ 链接 code

    场景：9403208000 重定向到 subheading 页面，页面里有 /commodities/9403208020 链接。
    旧逻辑 re.search(soup) 会错位提取 9403208020；应优先用 url 的 9403208000。
    """
    s = _scraper(tmp_path)
    # soup 里有 9403208020 链接（重定向 subheading 页面的子链接）
    html = '<html><body><a href="/commodities/9403208020">9403208020</a></body></html>'
    url = 'https://www.trade-tariff.service.gov.uk/commodities/9403208000'

    tariff = s.parse_commodity_page(html, url=url)

    assert tariff.get('code') == '9403208000', \
        f"应用 url code(9403208000)，实际错位为: {tariff.get('code')}"


def test_parse_url_code_when_no_soup_code(tmp_path):
    """soup 无 code 时仍能从 url 提取"""
    s = _scraper(tmp_path)
    html = '<html><body>no commodity links here</body></html>'
    url = 'https://www.trade-tariff.service.gov.uk/commodities/0101210000'

    tariff = s.parse_commodity_page(html, url=url)

    assert tariff.get('code') == '0101210000'


def test_parse_url_generates_correct_urls(tmp_path):
    """用 url code 后，north_ireland_url 等也基于正确 code"""
    s = _scraper(tmp_path)
    html = '<html><body><a href="/commodities/9999999999">wrong</a></body></html>'
    url = 'https://www.trade-tariff.service.gov.uk/commodities/9403208000'

    tariff = s.parse_commodity_page(html, url=url)

    assert tariff.get('code') == '9403208000'
    assert '9403208000' in tariff.get('north_ireland_url', '')
