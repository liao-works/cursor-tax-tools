import pytest
import sqlite3
import tempfile
import os
from tariff_db import TariffDB

def test_add_tariff_with_north_ireland_url():
    """验证 add_tariff 方法可以保存北爱尔兰 URL"""
    # 创建临时数据库
    db_fd, db_path = tempfile.mkstemp(suffix='.db')
    os.close(db_fd)

    try:
        # 初始化数据库
        db = TariffDB(db_path=db_path)

        # 添加记录（包含北爱尔兰 URL）
        db.add_tariff(
            code="1234567890",
            description="Test Commodity",
            rate="5.00%",
            url="https://www.trade-tariff.service.gov.uk/commodities/1234567890",
            north_ireland_url="https://www.trade-tariff.service.gov.uk/xi/commodities/1234567890"
        )

        # 验证北爱尔兰 URL 被正确保存
        tariff = db.get_tariff("1234567890")
        assert tariff['north_ireland_url'] == "https://www.trade-tariff.service.gov.uk/xi/commodities/1234567890"

    finally:
        try:
            os.unlink(db_path)
        except PermissionError:
            pass  # Windows 上文件可能被锁定，忽略错误

def test_add_tariff_auto_generates_north_ireland_url():
    """验证 add_tariff 在未提供 north_ireland_url 时自动生成"""
    # 创建临时数据库
    db_fd, db_path = tempfile.mkstemp(suffix='.db')
    os.close(db_fd)

    try:
        # 初始化数据库
        db = TariffDB(db_path=db_path)

        # 添加记录（不提供北爱尔兰 URL）
        db.add_tariff(
            code="1234567890",
            description="Test Commodity",
            rate="5.00%",
            url="https://www.trade-tariff.service.gov.uk/commodities/1234567890"
        )

        # 验证北爱尔兰 URL 被自动生成
        tariff = db.get_tariff("1234567890")
        assert tariff['north_ireland_url'] == "https://www.trade-tariff.service.gov.uk/xi/commodities/1234567890"

    finally:
        try:
            os.unlink(db_path)
        except PermissionError:
            pass  # Windows 上文件可能被锁定，忽略错误
