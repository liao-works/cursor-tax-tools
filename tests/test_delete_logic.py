#!/usr/bin/env python3
"""测试商品删除判定逻辑（should_delete_code）—— Phase 4 Step 1 RED 阶段

规约来源（Phase 1-3 实证）：
- 废弃 code（如 9403208000）被 trade-tariff 重定向到 /subheadings/，
  parse_commodity_page 解析后得到 rate=''（无 duty rate 结构）。
- DB 中 rate 为空 ≠ 废弃：Phase 3 抽样 6/7 是有效商品的历史抓取失败残留。
  故判据必须基于【重新抓取的解析结果 data.get('rate')】，绝不能用 DB 旧值。

删除规则：UK 与 NI 都"无效"才删（任一地有效则保留）。
无效 = 干净 HTTP 404，或 200 但 parse 后无有效 duty rate。
保守保留：抓取错误（防误删）、空 dict（状态不确定）。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from src.core.scraper import should_delete_code

_NI_NOT_PROVIDED = "未提供北爱尔兰URL"


def test_both_clean_404_should_delete():
    """UK 和 NI 都干净 404（data None, error None）→ 删"""
    assert should_delete_code(None, None, None, None) is True


def test_both_no_duty_rate_should_delete():
    """废弃 code（如 9403208000）：两地 200 但 parse 后 rate 空 → 删"""
    uk = {'code': '9403208000', 'rate': '', 'description': ''}
    ni = {'code': '9403208000', 'rate': '', 'description': ''}
    assert should_delete_code(uk, None, ni, None) is True


def test_has_duty_rate_should_not_delete():
    """有有效 duty rate → 保留"""
    uk = {'code': '0101210000', 'rate': '5%'}
    ni = {'code': '0101210000', 'rate': '5%'}
    assert should_delete_code(uk, None, ni, None) is False


def test_rate_zero_percent_is_valid():
    """rate='0%' 是有效免税税率，不删"""
    uk = {'code': 'X', 'rate': '0%'}
    ni = {'code': 'X', 'rate': '0%'}
    assert should_delete_code(uk, None, ni, None) is False


def test_fetch_error_should_not_delete():
    """抓取错误（data None + error 非 None）→ 保守保留，避免误删有效商品。

    同时作为"scrape_with_retry 不伪装404"的回归保护：
    网络超时/异常不应被判为废弃而误删。
    """
    assert should_delete_code(None, "超时", None, "超时") is False


def test_uk_valid_ni_404_should_not_delete():
    """UK 有效但 NI 404 → 保留（任一地有效即保留）"""
    uk = {'code': 'X', 'rate': '5%'}
    assert should_delete_code(uk, None, None, None) is False


def test_ni_not_provided_uk_no_rate_should_delete():
    """NI 未提供 + UK 无 duty rate → 删"""
    uk = {'code': 'X', 'rate': ''}
    assert should_delete_code(uk, None, None, _NI_NOT_PROVIDED) is True


def test_ni_not_provided_uk_valid_should_not_delete():
    """NI 未提供 + UK 有效 → 保留"""
    uk = {'code': 'X', 'rate': '5%'}
    assert should_delete_code(uk, None, None, _NI_NOT_PROVIDED) is False


def test_empty_dict_should_not_delete():
    """parse 异常返回空 dict {} → 状态不确定，保守保留"""
    assert should_delete_code({}, None, {}, None) is False


def test_uk_404_ni_no_rate_should_delete():
    """UK 干净 404 + NI 200 无 rate → 两地都无效 → 删"""
    ni = {'code': 'X', 'rate': ''}
    assert should_delete_code(None, None, ni, None) is True
