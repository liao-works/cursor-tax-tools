import pytest
import sqlite3
import tempfile
import os
from scripts.migrations.fix_missing_ni_urls import fix_missing_ni_urls


def test_fix_missing_ni_urls():
    """验证迁移脚本正确修复缺失的北爱尔兰 URL"""
    # 创建临时数据库
    db_fd, db_path = tempfile.mkstemp(suffix='.db')
    os.close(db_fd)

    try:
        # 创建测试数据
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # 创建表结构
        cursor.execute("""
            CREATE TABLE tariffs (
                code TEXT PRIMARY KEY,
                description TEXT,
                rate TEXT,
                url TEXT,
                north_ireland_rate TEXT,
                north_ireland_url TEXT,
                other_rate TEXT
            )
        """)

        # 插入测试数据（2条缺失 NI URL，1条有 NI URL）
        cursor.execute("INSERT INTO tariffs VALUES ('0000000001', 'Test 1', '5%', 'https://test.com/1', '5%', NULL, NULL)")
        cursor.execute("INSERT INTO tariffs VALUES ('0000000002', 'Test 2', '8%', 'https://test.com/2', '8%', '', NULL)")
        cursor.execute("INSERT INTO tariffs VALUES ('0000000003', 'Test 3', '10%', 'https://test.com/3', '10%', 'https://test.com/xi/3', NULL)")
        conn.commit()
        conn.close()

        # 执行修复
        result = fix_missing_ni_urls(db_path=db_path, dry_run=False)

        # 验证结果
        assert result['success'] is True
        assert result['fixed_count'] == 2

        # 验证数据库内容
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # 检查第1条记录（NULL -> 已修复）
        cursor.execute("SELECT north_ireland_url FROM tariffs WHERE code='0000000001'")
        assert cursor.fetchone()[0] == 'https://www.trade-tariff.service.gov.uk/xi/commodities/0000000001'

        # 检查第2条记录（空字符串 -> 已修复）
        cursor.execute("SELECT north_ireland_url FROM tariffs WHERE code='0000000002'")
        assert cursor.fetchone()[0] == 'https://www.trade-tariff.service.gov.uk/xi/commodities/0000000002'

        # 检查第3条记录（已有 URL -> 未修改）
        cursor.execute("SELECT north_ireland_url FROM tariffs WHERE code='0000000003'")
        assert cursor.fetchone()[0] == 'https://test.com/xi/3'

        conn.close()

    finally:
        os.unlink(db_path)


def test_fix_missing_ni_urls_dry_run():
    """验证演练模式不修改数据库"""
    # 创建临时数据库
    db_fd, db_path = tempfile.mkstemp(suffix='.db')
    os.close(db_fd)

    conn = None
    try:
        # 创建测试数据
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # 创建表结构
        cursor.execute("""
            CREATE TABLE tariffs (
                code TEXT PRIMARY KEY,
                description TEXT,
                rate TEXT,
                url TEXT,
                north_ireland_rate TEXT,
                north_ireland_url TEXT,
                other_rate TEXT
            )
        """)

        # 插入测试数据（1条缺失 NI URL）
        cursor.execute("INSERT INTO tariffs VALUES ('0000000001', 'Test 1', '5%', 'https://test.com/1', '5%', NULL, NULL)")
        conn.commit()
        conn.close()

        # 执行演练模式
        result = fix_missing_ni_urls(db_path=db_path, dry_run=True)

        # 验证结果
        assert result['success'] is True
        assert result.get('dry_run') is True
        assert result.get('fixed_count') == 0

        # 验证数据库未被修改
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        cursor.execute("SELECT north_ireland_url FROM tariffs WHERE code='0000000001'")
        ni_url = cursor.fetchone()[0]
        assert ni_url is None  # 仍为 NULL

        conn.close()

    finally:
        # Windows 下需要确保文件句柄完全关闭
        if conn:
            try:
                conn.close()
            except:
                pass
        # 等待文件释放
        import time
        time.sleep(0.1)
        try:
            os.unlink(db_path)
        except:
            pass


def test_fix_missing_ni_urls_no_missing():
    """验证当所有记录都有 NI URL 时不执行修复"""
    # 创建临时数据库
    db_fd, db_path = tempfile.mkstemp(suffix='.db')
    os.close(db_fd)

    conn = None
    try:
        # 创建测试数据
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # 创建表结构
        cursor.execute("""
            CREATE TABLE tariffs (
                code TEXT PRIMARY KEY,
                description TEXT,
                rate TEXT,
                url TEXT,
                north_ireland_rate TEXT,
                north_ireland_url TEXT,
                other_rate TEXT
            )
        """)

        # 插入测试数据（已有 NI URL）
        cursor.execute("INSERT INTO tariffs VALUES ('0000000001', 'Test 1', '5%', 'https://test.com/1', '5%', 'https://test.com/xi/1', NULL)")
        conn.commit()
        conn.close()

        # 执行修复
        result = fix_missing_ni_urls(db_path=db_path, dry_run=False)

        # 验证结果
        assert result['success'] is True
        assert result['fixed_count'] == 0

    finally:
        # Windows 下需要确保文件句柄完全关闭
        if conn:
            try:
                conn.close()
            except:
                pass
        # 等待文件释放
        import time
        time.sleep(0.1)
        try:
            os.unlink(db_path)
        except:
            pass


def test_fix_missing_ni_urls_database_not_exists():
    """验证数据库不存在时的错误处理"""
    result = fix_missing_ni_urls(db_path='nonexistent.db', dry_run=False)

    assert result['success'] is False
    assert 'error' in result
