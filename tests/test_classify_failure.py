#!/usr/bin/env python3
"""测试失败类型分类（Phase 4 Step 1 RED）

分类规则（Phase 3 方案）：
- transient（可重试）: network(Timeout/ConnectionError/5xx/status=0)、db(保存失败)
- permanent（不重试）: not_found(404/未找到记录)、data_missing(未找到描述/未找到税率)
- limited（有限重试）: parse(解析失败)、unknown
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from src.db.database import classify_failure


# ===== transient（可重试）=====
def test_timeout_exception_is_transient():
    assert classify_failure("超时", exc_type="Timeout") == 'transient'


def test_connection_error_is_transient():
    assert classify_failure("连接失败", exc_type="ConnectionError") == 'transient'


def test_5xx_status_is_transient():
    assert classify_failure("服务器错误", status=503) == 'transient'


def test_zero_status_is_transient():
    """status=0（scrape_with_retry 异常返回）→ transient"""
    assert classify_failure("抓取异常", status=0) == 'transient'


def test_save_failed_is_transient():
    """DB 保存失败（如 database is locked）→ transient"""
    assert classify_failure("保存失败: database is locked") == 'transient'


def test_network_keyword_is_transient():
    assert classify_failure("英国数据抓取失败: ConnectionTimeout") == 'transient'


# ===== permanent（不重试）=====
def test_404_status_is_permanent():
    assert classify_failure("不存在", status=404) == 'permanent'


def test_not_found_record_is_permanent():
    assert classify_failure("未找到商品编码 X 的记录") == 'permanent'


def test_no_description_is_permanent():
    assert classify_failure("未找到商品描述 for code: X") == 'permanent'


def test_no_rate_is_permanent():
    assert classify_failure("未找到税率 for code: X") == 'permanent'


# ===== limited（有限重试）=====
def test_parse_error_is_limited():
    assert classify_failure("解析页面失败: KeyError") == 'limited'


def test_unknown_defaults_limited():
    """未匹配任何模式 → 保守 limited（有限重试）"""
    assert classify_failure("某种未知的错误情况") == 'limited'
