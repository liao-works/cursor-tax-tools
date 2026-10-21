#!/usr/bin/env python3
"""测试 scrape_errors 表扩展（Phase 4 Step 2 RED）

验证：
- add_scrape_error 自动分类 failure_type（调 classify_failure）
- 显式 failure_type 参数
- retry_count 字段默认 0
- 重复 add 不重置 retry_count（ON CONFLICT 保留，供重试逻辑递增）
- get_scrape_errors 返回新字段
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from src.db.database import TariffDB


def _db(tmp_path):
    return TariffDB(str(tmp_path / "test.db"))


def test_add_auto_classifies_failure_type(tmp_path):
    """未显式传 failure_type 时，自动按 error_message 分类"""
    db = _db(tmp_path)
    db.add_scrape_error("X", "未找到税率 for code: X")
    err = db.get_scrape_errors("X")[0]
    assert err['failure_type'] == 'permanent'


def test_add_explicit_failure_type(tmp_path):
    db = _db(tmp_path)
    db.add_scrape_error("X", "err", failure_type='transient')
    assert db.get_scrape_errors("X")[0]['failure_type'] == 'transient'


def test_retry_count_defaults_zero(tmp_path):
    db = _db(tmp_path)
    db.add_scrape_error("X", "err", failure_type='transient')
    assert db.get_scrape_errors("X")[0]['retry_count'] == 0


def test_repeat_add_preserves_retry_count(tmp_path):
    """重复失败不应重置 retry_count（重试逻辑单独递增）"""
    db = _db(tmp_path)
    db.add_scrape_error("X", "err1", failure_type='transient')
    # 模拟重试逻辑已递增 retry_count
    db.conn.execute("UPDATE scrape_errors SET retry_count = 3 WHERE code = ?", ("X",))
    db.conn.commit()
    # 再次记录失败（同 code）
    db.add_scrape_error("X", "err2", failure_type='transient')
    assert db.get_scrape_errors("X")[0]['retry_count'] == 3, "重复 add 不应重置 retry_count"


def test_get_returns_new_fields(tmp_path):
    db = _db(tmp_path)
    db.add_scrape_error("X", "err", failure_type='limited')
    err = db.get_scrape_errors("X")[0]
    assert 'failure_type' in err
    assert 'retry_count' in err
    assert err['failure_type'] == 'limited'


def test_old_db_migration_adds_columns(tmp_path):
    """旧 DB（无新字段）打开后自动 ALTER 加字段（向后兼容）"""
    db_path = str(tmp_path / "old.db")
    # 模拟旧 schema
    import sqlite3
    with sqlite3.connect(db_path) as c:
        c.execute("CREATE TABLE scrape_errors (code TEXT PRIMARY KEY, error_message TEXT, timestamp DATETIME DEFAULT CURRENT_TIMESTAMP)")
        c.execute("INSERT INTO scrape_errors (code, error_message) VALUES ('OLD', 'legacy err')")
    # 用 TariffDB 打开 → 应自动迁移加字段
    db = TariffDB(db_path)
    err = db.get_scrape_errors("OLD")[0]
    assert 'failure_type' in err and 'retry_count' in err  # 字段已迁移存在
    # 新写入应正常工作
    db.add_scrape_error("NEW", "未找到税率", )
    assert db.get_scrape_errors("NEW")[0]['failure_type'] == 'permanent'
