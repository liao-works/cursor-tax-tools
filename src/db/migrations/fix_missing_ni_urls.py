#!/usr/bin/env python3
"""
数据迁移脚本：修复缺失的北爱尔兰 URL

用途：为现有数据库中缺失 north_ireland_url 的记录补充 URL
影响：修复 414 条缺失北爱尔兰 URL 的记录
"""

import sys
import os
import sqlite3
import logging
from pathlib import Path

# 添加项目根目录到 Python 路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger(__name__)


def fix_missing_ni_urls(db_path: str = "tariffs.db", dry_run: bool = False):
    """
    修复缺失的北爱尔兰 URL

    Args:
        db_path: 数据库文件路径
        dry_run: 是否为演练模式（不实际修改数据库）

    Returns:
        dict: 修复统计信息
    """
    if not os.path.exists(db_path):
        logger.error(f"数据库文件不存在: {db_path}")
        return {'success': False, 'error': '数据库文件不存在'}

    logger.info(f"正在检查数据库: {db_path}")

    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # 检查缺失 NI URL 的记录数
        cursor.execute("""
            SELECT COUNT(*)
            FROM tariffs
            WHERE north_ireland_url IS NULL OR north_ireland_url = ''
        """)
        missing_count = cursor.fetchone()[0]

        if missing_count == 0:
            logger.info("OK 所有记录都包含北爱尔兰 URL，无需修复")
            return {'success': True, 'fixed_count': 0}

        logger.info(f"发现 {missing_count} 条记录缺失北爱尔兰 URL")

        if dry_run:
            logger.info("演练模式：显示将要修复的记录")
            cursor.execute("""
                SELECT code, url
                FROM tariffs
                WHERE north_ireland_url IS NULL OR north_ireland_url = ''
                LIMIT 10
            """)
            for code, url in cursor.fetchall():
                ni_url = f"https://www.trade-tariff.service.gov.uk/xi/commodities/{code}"
                logger.info(f"  {code}: {ni_url}")

            return {'success': True, 'fixed_count': 0, 'dry_run': True}

        # 执行修复
        logger.info("开始修复...")
        cursor.execute("""
            UPDATE tariffs
            SET north_ireland_url = 'https://www.trade-tariff.service.gov.uk/xi/commodities/' || code
            WHERE north_ireland_url IS NULL OR north_ireland_url = ''
        """)

        conn.commit()
        fixed_count = cursor.rowcount

        # 验证修复结果
        cursor.execute("""
            SELECT COUNT(*)
            FROM tariffs
            WHERE north_ireland_url IS NULL OR north_ireland_url = ''
        """)
        remaining_count = cursor.fetchone()[0]

        conn.close()

        logger.info(f"修复完成！")
        logger.info(f"   修复记录数: {fixed_count}")
        logger.info(f"   剩余缺失: {remaining_count}")

        return {
            'success': True,
            'fixed_count': fixed_count,
            'remaining_count': remaining_count
        }

    except Exception as e:
        logger.error(f"修复失败: {e}")
        import traceback
        traceback.print_exc()
        return {'success': False, 'error': str(e)}


def main():
    import argparse

    parser = argparse.ArgumentParser(description="修复缺失的北爱尔兰 URL")
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
    print("北爱尔兰 URL 修复工具")
    print("=" * 60)
    print()

    result = fix_missing_ni_urls(
        db_path=args.db_path,
        dry_run=args.dry_run
    )

    print()
    print("=" * 60)

    if result['success']:
        if result.get('dry_run'):
            print("OK 演练完成")
            print("   如需实际修复，请运行: python scripts/migrations/fix_missing_ni_urls.py")
        else:
            print(f"OK 修复成功！共修复 {result.get('fixed_count', 0)} 条记录")
        return 0
    else:
        print(f"ERROR 修复失败: {result.get('error', '未知错误')}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
