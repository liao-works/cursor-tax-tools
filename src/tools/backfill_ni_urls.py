#!/usr/bin/env python3
"""
为现有数据批量生成北爱尔兰 URL

这个脚本会：
1. 扫描数据库中所有记录
2. 为缺少 north_ireland_url 的记录自动生成 URL
3. 更新数据库

使用方法：
    python scripts/actions/backfill_ni_urls.py
"""

import sys
import os
from tqdm import tqdm

# 添加项目根目录到 Python 路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from src.db.database import TariffDB


def backfill_ni_urls(dry_run=False):
    """为所有记录生成北爱尔兰 URL

    Args:
        dry_run: 是否只是预览而不实际更新

    Returns:
        dict: 包含统计信息的字典
    """
    db = TariffDB()

    # 获取所有记录
    print("📊 正在扫描数据库...")
    cur = db.conn.execute("SELECT code, url, north_ireland_url FROM tariffs")
    all_records = cur.fetchall()

    total = len(all_records)
    missing = []

    # 找出缺少 north_ireland_url 的记录
    for record in all_records:
        code, uk_url, ni_url = record
        if not ni_url or ni_url.strip() == '':
            missing.append((code, uk_url))

    print(f"\n📈 统计信息:")
    print(f"   总记录数: {total}")
    print(f"   已有北爱尔兰URL: {total - len(missing)}")
    print(f"   缺少北爱尔兰URL: {len(missing)}")

    if dry_run:
        print("\n🔍 预览模式 - 不会实际更新数据库")
        for code, uk_url in missing[:5]:  # 只显示前5个
            ni_url = f"https://www.trade-tariff.service.gov.uk/xi/commodities/{code}"
            print(f"   {code}: {ni_url}")
        if len(missing) > 5:
            print(f"   ... 还有 {len(missing) - 5} 条记录")
        return {
            'total': total,
            'missing': len(missing),
            'updated': 0
        }

    # 实际更新
    if not missing:
        print("\n✅ 所有记录都已有北爱尔兰 URL，无需更新")
        return {
            'total': total,
            'missing': 0,
            'updated': 0
        }

    print(f"\n🔧 开始更新 {len(missing)} 条记录...")

    updated = 0
    failed = 0

    for code, uk_url in tqdm(missing, desc="处理进度"):
        try:
            # 生成北爱尔兰 URL
            ni_url = f"https://www.trade-tariff.service.gov.uk/xi/commodities/{code}"

            # 更新数据库
            db.update_tariff(
                code,
                north_ireland_url=ni_url
            )
            updated += 1

        except Exception as e:
            print(f"\n❌ 更新记录 {code} 失败: {str(e)}")
            failed += 1

    print(f"\n✅ 更新完成！")
    print(f"   成功更新: {updated}")
    print(f"   更新失败: {failed}")
    print(f"   覆盖率: {(updated + (total - len(missing))) / total * 100:.1f}%")

    return {
        'total': total,
        'missing': len(missing),
        'updated': updated,
        'failed': failed
    }


def main():
    """主函数"""
    import argparse

    parser = argparse.ArgumentParser(description='为现有数据批量生成北爱尔兰 URL')
    parser.add_argument('--dry-run', action='store_true',
                       help='预览模式，不实际更新数据库')

    args = parser.parse_args()

    print("=" * 60)
    print("🔧 北爱尔兰 URL 补全工具")
    print("=" * 60)
    print()

    try:
        results = backfill_ni_urls(dry_run=args.dry_run)

        if not args.dry_run and results['updated'] > 0:
            print("\n💡 提示: 运行 GitHub Actions 或执行批量更新来获取北爱尔兰税率数据")
            print("   命令: python scripts/actions/run_scraper.py")

    except Exception as e:
        print(f"\n❌ 执行失败: {str(e)}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
