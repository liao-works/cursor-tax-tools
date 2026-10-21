#!/usr/bin/env python3
"""
数据库验证和健康检查脚本
"""

import sqlite3
import sys
import os
import json
from datetime import datetime


def check_database_health(db_path: str) -> dict:
    """检查数据库健康状况"""
    if not os.path.exists(db_path):
        return {"healthy": False, "error": "数据库文件不存在"}

    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # 检查表是否存在
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='tariffs'")
        if not cursor.fetchone():
            return {"healthy": False, "error": "tariffs表不存在"}

        # 基本统计
        stats = {}

        # 记录数量
        stats['record_count'] = cursor.execute("SELECT COUNT(*) FROM tariffs").fetchone()[0]

        # 有描述的记录数量
        stats['records_with_description'] = cursor.execute(
            "SELECT COUNT(*) FROM tariffs WHERE description IS NOT NULL AND description != ''"
        ).fetchone()[0]

        # 有英国税率的记录数量
        stats['records_with_uk_rate'] = cursor.execute(
            "SELECT COUNT(*) FROM tariffs WHERE rate IS NOT NULL"
        ).fetchone()[0]

        # 有北爱尔兰税率的记录数量
        stats['records_with_ni_rate'] = cursor.execute(
            "SELECT COUNT(*) FROM tariffs WHERE north_ireland_rate IS NOT NULL"
        ).fetchone()[0]

        # 错误记录数量
        try:
            stats['error_count'] = cursor.execute(
                "SELECT COUNT(*) FROM scrape_errors"
            ).fetchone()[0]
        except sqlite3.OperationalError:
            stats['error_count'] = 0

        # 文件大小
        stats['file_size_mb'] = round(os.path.getsize(db_path) / (1024 * 1024), 2)

        # 数据质量指标
        stats['quality_score'] = calculate_quality_score(stats)

        # 错误率检查
        if stats['record_count'] > 0:
            stats['error_rate'] = (stats['error_count'] / stats['record_count']) * 100
        else:
            stats['error_rate'] = 0

        conn.close()

        # 健康状态判断
        healthy = (
            stats['record_count'] > 0 and
            stats['error_rate'] < 5 and  # 错误率低于5%
            stats['quality_score'] > 50  # 质量分数高于50
        )

        return {
            "healthy": healthy,
            "stats": stats,
            "timestamp": datetime.now().isoformat()
        }

    except Exception as e:
        return {"healthy": False, "error": str(e)}


def calculate_quality_score(stats: dict) -> float:
    """计算数据质量分数 (0-100)"""
    if stats['record_count'] == 0:
        return 0

    score = 0

    # 描述完整性 (40分)
    description_ratio = stats['records_with_description'] / stats['record_count']
    score += description_ratio * 40

    # 英国税率完整性 (30分)
    uk_rate_ratio = stats['records_with_uk_rate'] / stats['record_count']
    score += uk_rate_ratio * 30

    # 北爱尔兰税率完整性 (30分)
    ni_rate_ratio = stats['records_with_ni_rate'] / stats['record_count']
    score += ni_rate_ratio * 30

    return min(100, round(score, 2))


def print_validation_report(db_path: str, results: dict):
    """打印验证报告"""
    print(f"\n📊 数据库验证报告 - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"📁 数据库路径: {db_path}")

    if not results["healthy"]:
        print(f"❌ 验证失败: {results.get('error', '未知错误')}")
        return False

    stats = results["stats"]

    print(f"\n✅ 验证通过!")
    print(f"\n📈 基本统计:")
    print(f"   📝 总记录数: {stats['record_count']:,}")
    print(f"   💾 文件大小: {stats['file_size_mb']} MB")
    print(f"   📊 质量分数: {stats['quality_score']}/100")

    print(f"\n🎯 数据完整性:")
    print(f"   📄 有描述: {stats['records_with_description']:,} ({stats['records_with_description']/stats['record_count']*100:.1f}%)")
    print(f"   🇬🇧 有英国税率: {stats['records_with_uk_rate']:,} ({stats['records_with_uk_rate']/stats['record_count']*100:.1f}%)")
    print(f"   🇮🇪 有北爱税率: {stats['records_with_ni_rate']:,} ({stats['records_with_ni_rate']/stats['record_count']*100:.1f}%)")

    print(f"\n⚠️ 错误统计:")
    print(f"   ❌ 错误记录: {stats['error_count']}")
    print(f"   📈 错误率: {stats['error_rate']:.2f}%")

    # 质量警告
    if stats['error_rate'] > 5:
        print(f"\n⚠️ 警告: 错误率过高 ({stats['error_rate']:.2f}% > 5%)")

    if stats['quality_score'] < 70:
        print(f"\n⚠️ 警告: 数据质量偏低 ({stats['quality_score']}/100 < 70)")

    return True


def main():
    """主函数"""
    if len(sys.argv) != 2:
        print("用法: python validate_database.py <数据库路径>")
        sys.exit(1)

    db_path = sys.argv[1]

    # 执行验证
    results = check_database_health(db_path)

    # 打印报告
    is_valid = print_validation_report(db_path, results)

    # 保存结果到JSON文件
    with open('validation_results.json', 'w', encoding='utf-8') as f:
        json.dump(results, f, indent=2, ensure_ascii=False, default=str)

    # 退出状态
    if not is_valid:
        print("\n❌ 数据库验证失败!")
        sys.exit(1)
    else:
        print("\n✅ 数据库验证通过!")
        sys.exit(0)


if __name__ == "__main__":
    main()