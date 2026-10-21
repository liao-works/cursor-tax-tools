#!/usr/bin/env python3
"""测试路径1（全量抓取）单条 commodity 分类逻辑（Phase 4 Step 4）

路径1 只抓 UK commodity 页面，无 NI 数据。废弃识别用 UK-only 判定
（NI 视为未提供）。

局限说明：路径1 只能清理"仍在导航链接内的废弃 code"；
已从链接消失的僵尸 code（范围盲区）需独立的快照差集机制，本 Step 不涉及。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from src.core.scraper import classify_commodity


def test_404_classified_as_delete():
    """真 404 → 删"""
    assert classify_commodity(404, None) == 'delete'


def test_200_with_rate_classified_as_save():
    """200 且有税率 → 保存"""
    tariff = {'code': '0101210000', 'rate': '5%'}
    assert classify_commodity(200, tariff) == 'save'


def test_200_no_rate_classified_as_delete():
    """200 但无税率（废弃如 9403208000 重定向后）→ 删"""
    tariff = {'code': '9403208000', 'rate': '', 'description': ''}
    assert classify_commodity(200, tariff) == 'delete'


def test_zero_percent_is_save():
    """0% 免税是有效税率 → 保存（回归保护）"""
    tariff = {'code': 'X', 'rate': '0%'}
    assert classify_commodity(200, tariff) == 'save'


def test_exception_status_classified_as_skip():
    """status=0（异常，Step 2 修复后 scrape_with_retry 返回）→ 保守跳过，不删不存"""
    assert classify_commodity(0, None) == 'skip'


def test_200_empty_tariff_classified_as_skip():
    """200 但 parse 返回空 dict（parse 异常）→ 保守跳过，状态不确定"""
    assert classify_commodity(200, {}) == 'skip'
