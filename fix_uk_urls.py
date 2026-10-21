"""
修复数据库中英国税率URL错误的脚本

问题：部分记录的英国税率URL错误地使用了北爱尔兰的URL（包含 /xi/）
解决：将英国税率URL中的 /xi/ 移除
"""

import sqlite3
import sys
import os

# 添加项目根目录到路径
sys.path.insert(0, os.path.dirname(__file__))

from src.db.database import get_writable_db_path


def fix_uk_urls(db_path: str = "tariffs.db", dry_run: bool = False):
    """修复数据库中的英国URL

    Args:
        db_path: 数据库路径
        dry_run: 是否为试运行模式（只检查，不修改）
    """
    # 获取可写的数据库路径
    db_path = get_writable_db_path(db_path)

    print(f"[INFO] 使用数据库: {db_path}")
    print(f"[INFO] 模式: {'试运行（只检查）' if dry_run else '修复模式'}")
    print("=" * 60)

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    try:
        # 1. 查找所有英国URL包含 /xi/ 的记录
        cursor.execute("""
            SELECT code, url
            FROM tariffs
            WHERE url LIKE '%/xi/commodities/%'
        """)

        wrong_urls = cursor.fetchall()

        if not wrong_urls:
            print("[OK] 没有发现需要修复的记录！")
            return

        print(f"[WARNING] 发现 {len(wrong_urls)} 条需要修复的记录：\n")

        fixed_count = 0
        for code, url in wrong_urls:
            # 生成正确的URL（移除 /xi/）
            correct_url = url.replace('/xi/commodities/', '/commodities/')

            print(f"商品编码: {code}")
            print(f"  [ERROR] 错误URL: {url}")
            print(f"  [FIXED] 正确URL: {correct_url}")
            print()

            if not dry_run:
                # 执行修复
                cursor.execute(
                    "UPDATE tariffs SET url = ? WHERE code = ?",
                    (correct_url, code)
                )
                fixed_count += 1

        if not dry_run:
            # 提交更改
            conn.commit()
            print("=" * 60)
            print(f"[SUCCESS] 成功修复 {fixed_count} 条记录！")
        else:
            print("=" * 60)
            print(f"[INFO] 试运行模式：发现 {len(wrong_urls)} 条需要修复的记录")
            print(f"[TIP] 运行 'python fix_uk_urls.py --fix' 来执行实际修复")

    except Exception as e:
        print(f"[ERROR] 错误: {str(e)}")
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="修复数据库中的英国税率URL")
    parser.add_argument("--db", default="tariffs.db", help="数据库文件路径")
    parser.add_argument("--fix", action="store_true", help="执行实际修复（默认为试运行）")

    args = parser.parse_args()

    # 试运行模式：dry_run=True（默认）
    # 修复模式：dry_run=False（需要 --fix 参数）
    fix_uk_urls(args.db, dry_run=not args.fix)
