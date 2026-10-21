#!/usr/bin/env python3
"""
简化版监控脚本
只监控关键指标，适合个人项目
"""

import json
import os
import sqlite3
import sys
import time
from datetime import datetime


class SimpleMonitor:
    """简化版监控器 - 只监控最关键的指标"""

    def __init__(self, db_path: str = 'tariffs.db'):
        self.db_path = db_path
        self.start_time = time.time()

    def get_basic_stats(self) -> dict:
        """获取基本统计信息"""
        try:
            # 检查数据库文件
            if not os.path.exists(self.db_path):
                return {"status": "error", "message": "数据库文件不存在"}

            file_size = os.path.getsize(self.db_path) / 1024 / 1024  # MB

            # 连接数据库获取统计
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            stats = {
                "status": "success",
                "timestamp": datetime.now().isoformat(),
                "file_size_mb": round(file_size, 2),
                "total_records": cursor.execute("SELECT COUNT(*) FROM tariffs").fetchone()[0],
                "records_with_description": cursor.execute(
                    "SELECT COUNT(*) FROM tariffs WHERE description IS NOT NULL AND description != ''"
                ).fetchone()[0],
                "records_with_uk_rate": cursor.execute(
                    "SELECT COUNT(*) FROM tariffs WHERE rate IS NOT NULL AND rate != ''"
                ).fetchone()[0],
                "records_with_ni_rate": cursor.execute(
                    "SELECT COUNT(*) FROM tariffs WHERE north_ireland_rate IS NOT NULL AND north_ireland_rate != ''"
                ).fetchone()[0],
                "error_count": cursor.execute("SELECT COUNT(*) FROM scrape_errors").fetchone()[0],
                "processing_time_seconds": round(time.time() - self.start_time, 2)
            }

            # 计算完整性
            stats["completeness_rate"] = round(
                stats["records_with_description"] / max(stats["total_records"], 1) * 100, 2
            )

            conn.close()
            return stats

        except Exception as e:
            return {
                "status": "error",
                "message": f"获取统计信息失败: {str(e)}"
            }

    def check_health(self, stats: dict) -> dict:
        """健康检查"""
        if stats["status"] != "success":
            return {"healthy": False, "issues": [stats["message"]]}

        issues = []

        # 检查完整性
        if stats["completeness_rate"] < 95:
            issues.append(f"数据完整性较低: {stats['completeness_rate']}%")

        # 检查错误率
        if stats["error_count"] > stats["total_records"] * 0.01:  # 错误率超过1%
            issues.append(f"错误记录过多: {stats['error_count']}")

        # 检查文件大小异常
        if stats["file_size_mb"] < 1 or stats["file_size_mb"] > 100:
            issues.append(f"文件大小异常: {stats['file_size_mb']}MB")

        return {
            "healthy": len(issues) == 0,
            "issues": issues
        }

    def save_stats(self, stats: dict, output_path: str = 'update_stats.json'):
        """保存统计信息"""
        try:
            with open(output_path, 'w', encoding='utf-8') as f:
                json.dump(stats, f, indent=2, ensure_ascii=False)
            print(f"💾 统计信息已保存: {output_path}")
        except Exception as e:
            print(f"❌ 保存统计信息失败: {e}")

    def print_summary(self, stats: dict):
        """打印摘要"""
        if stats["status"] != "success":
            print(f"❌ 统计失败: {stats['message']}")
            return

        health = self.check_health(stats)

        print("\n" + "="*50)
        print("📊 数据库统计摘要")
        print("="*50)
        print(f"📁 文件大小: {stats['file_size_mb']} MB")
        print(f"📝 总记录数: {stats['total_records']:,}")
        print(f"📖 有描述: {stats['records_with_description']:,}")
        print(f"🇬🇧 英国税率: {stats['records_with_uk_rate']:,}")
        print(f"🇮🇪 北爱税率: {stats['records_with_ni_rate']:,}")
        print(f"❌ 错误记录: {stats['error_count']}")
        print(f"📈 完整性: {stats['completeness_rate']}%")
        print(f"⏱️ 处理时间: {stats['processing_time_seconds']} 秒")

        if health["healthy"]:
            print("✅ 数据库状态: 健康")
        else:
            print("⚠️ 数据库状态: 有问题")
            for issue in health["issues"]:
                print(f"   - {issue}")

        print("="*50)


def main():
    """主函数"""
    import argparse

    parser = argparse.ArgumentParser(description='简化版监控工具')
    parser.add_argument('--db-path', '-d', default='tariffs.db',
                       help='数据库文件路径')
    parser.add_argument('--output', '-o', default='update_stats.json',
                       help='输出文件路径')
    parser.add_argument('--quiet', '-q', action='store_true',
                       help='静默模式，只输出JSON')

    args = parser.parse_args()

    monitor = SimpleMonitor(args.db_path)
    stats = monitor.get_basic_stats()

    # 保存统计信息
    monitor.save_stats(stats, args.output)

    # 输出摘要（除非静默模式）
    if not args.quiet:
        monitor.print_summary(stats)

    # 输出环境变量供GitHub Actions使用
    if stats["status"] == "success":
        print(f"::set-output name=total_records::{stats['total_records']}")
        print(f"::set-output name=file_size_mb::{stats['file_size_mb']}")
        print(f"::set-output name=completeness_rate::{stats['completeness_rate']}")
        print(f"::set-output name=error_count::{stats['error_count']}")
        print(f"::set-output name=processing_time::{stats['processing_time_seconds']}")

        # 设置健康状态
        health = monitor.check_health(stats)
        if not health["healthy"]:
            for issue in health["issues"]:
                print(f"::warning::{issue}")

    sys.exit(0 if stats["status"] == "success" else 1)


if __name__ == "__main__":
    main()