"""
添加反倾销税和反补贴税字段到 tariffs 表
"""
import sqlite3
import sys
import os

def upgrade(db_path: str):
    """添加新字段"""
    # 检查数据库文件是否存在
    if not os.path.exists(db_path):
        print(f"INFO: Database file not found: {db_path}")
        print("INFO: Table structure will be created on first run")
        return

    conn = sqlite3.connect(db_path)

    try:
        # 检查表是否存在
        cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='tariffs'")
        if not cur.fetchone():
            print(f"INFO: tariffs table not found: {db_path}")
            print("INFO: Table structure will be created on first run")
            return

        # 添加反倾销税字段
        conn.execute("ALTER TABLE tariffs ADD COLUMN anti_dumping_rate TEXT")
        print("SUCCESS: Added anti_dumping_rate column")

        # 添加反补贴税字段
        conn.execute("ALTER TABLE tariffs ADD COLUMN countervailing_rate TEXT")
        print("SUCCESS: Added countervailing_rate column")

        conn.commit()
        print("SUCCESS: Successfully added anti-dumping and countervailing duty fields")
    except sqlite3.OperationalError as e:
        if "duplicate column name" in str(e).lower():
            print("INFO: Columns already exist, skipping")
        else:
            raise e
    finally:
        conn.close()

if __name__ == "__main__":
    # 使用当前目录的 tariffs.db
    db_path = "tariffs.db"
    upgrade(db_path)
