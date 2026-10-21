#!/usr/bin/env python3
"""
数据迁移脚本：添加 last_updated 字段

用途：为现有数据库添加最后更新时间字段
影响：所有现有记录将获得当前时间作为 last_updated 值
"""

import sys
import os
import sqlite3
import logging
from pathlib import Path
from datetime import datetime

# 添加项目根目录到 Python 路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger(__name__)


def add_last_updated_field(db_path: str = "tariffs.db", dry_run: bool = False):
    """
    添加 last_updated 字段

    Args:
        db_path: 数据库文件路径
        dry_run: 是否为演练模式（不实际修改数据库）

    Returns:
        dict: 迁移统计信息
    """
    if not os.path.exists(db_path):
        logger.error(f"数据库文件不存在: {db_path}")
        return {'success': False, 'error': '数据库文件不存在'}

    logger.info(f"正在检查数据库: {db_path}")

    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # 检查字段是否已存在
        cursor.execute("PRAGMA table_info(tariffs)")
        columns = [col[1] for col in cursor.fetchall()]

        if 'last_updated' in columns:
            logger.info("✅ last_updated 字段已存在，无需迁移")
            return {'success': True, 'added': False}

        logger.info("📋 last_updated 字段不存在，准备添加...")

        if dry_run:
            logger.info("演练模式：将添加 last_updated 字段")
            return {'success': True, 'added': False, 'dry_run': True}

        # 添加字段
        logger.info("🔧 正在添加 last_updated 字段...")
        cursor.execute("""
            ALTER TABLE tariffs
            ADD COLUMN last_updated DATETIME DEFAULT CURRENT_TIMESTAMP
        """)

        # 为所有现有记录设置当前时间
        logger.info("🕐 正在为现有记录设置更新时间...")
        current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        cursor.execute("""
            UPDATE tariffs
            SET last_updated = ?
            WHERE last_updated IS NULL
        """, (current_time,))

        affected_rows = cursor.rowcount

        conn.commit()
        conn.close()

        logger.info(f"✅ 迁移完成！")
        logger.info(f"   添加字段: last_updated")
        logger.info(f"   更新记录数: {affected_rows}")

        return {
            'success': True,
            'added': True,
            'affected_rows': affected_rows
        }

    except Exception as e:
        logger.error(f"迁移失败: {e}")
        import traceback
        traceback.print_exc()
        return {'success': False, 'error': str(e)}


def main():
    import argparse

    parser = argparse.ArgumentParser(description="添加 last_updated 字段")
    parser.add_argument(
        '--db-path',
        type=str,
        default='tariffs.db',
        help='数据库文件路径（默认: tariffs.db）'
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='演练模式：不实际修改数据库'
    )

    args = parser.parse_args()

    print("=" * 60)
    print("🕐 last_updated 字段迁移工具")
    print("=" * 60)
    print()

    result = add_last_updated_field(
        db_path=args.db_path,
        dry_run=args.dry_run
    )

    print()
    print("=" * 60)

    if result['success']:
        if result.get('dry_run'):
            print("OK 演练完成")
            print("   如需实际迁移，请运行: python scripts/migrations/add_last_updated_field.py")
        elif result.get('added'):
            print(f"OK 迁移成功！更新了 {result.get('affected_rows', 0)} 条记录")
        else:
            print("OK 字段已存在，无需迁移")
        return 0
    else:
        print(f"ERROR 迁移失败: {result.get('error', '未知错误')}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
