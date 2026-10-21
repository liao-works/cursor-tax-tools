#!/usr/bin/env python3
"""
北爱尔兰税率补齐脚本 - 补齐现有数据库中缺失的北爱尔兰税率

功能：
1. 查询所有 north_ireland_rate IS NULL 的记录
2. 批量爬取北爱尔兰页面
3. 更新数据库
4. 生成补齐报告
"""

import asyncio
import sys
import os
import json
import time
from typing import List, Dict, Optional
from datetime import datetime

# 添加项目根目录到 Python 路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from src.core.scraper import TariffScraper
from src.db.database import TariffDB


class NIRateBackfiller:
    """北爱尔兰税率补齐器"""

    def __init__(self, db_path: str = None, batch_size: int = 50, delay: float = 0.1):
        """
        初始化补齐器

        Args:
            db_path: 数据库路径，None 则使用默认路径
            batch_size: 批量大小
            delay: 批次间延迟（秒）
        """
        self.db_path = db_path
        self.batch_size = batch_size
        self.delay = delay
        self.stats = {
            'total_missing': 0,
            'total_processed': 0,
            'successful': 0,
            'failed': 0,
            'no_data': 0,  # 北爱尔兰页面不存在或无数据
            'start_time': None,
            'end_time': None,
            'failed_codes': []
        }

    async def execute(self):
        """执行补齐任务"""
        self.stats['start_time'] = time.time()

        print(f"\n{'='*60}")
        print(f"🔧 北爱尔兰税率补齐任务")
        print(f"{'='*60}")
        print(f"数据库: {self.db_path or '默认路径'}")
        print(f"批量大小: {self.batch_size}")
        print(f"批次延迟: {self.delay}s")
        print(f"{'='*60}\n")

        try:
            # 动态导入模块
            from src.core.scraper import TariffScraper
            from src.db.database import TariffDB

            # 连接数据库
            db = TariffDB(db_path=self.db_path)
            scraper = TariffScraper()
            scraper.db = db

            # 1. 查询缺失北爱尔兰税率的记录
            missing_records = self._get_missing_ni_records(db)
            self.stats['total_missing'] = len(missing_records)

            if not missing_records:
                print("✅ 所有记录都有北爱尔兰税率，无需补齐")
                return self.stats

            print(f"📊 发现 {len(missing_records)} 条记录缺失北爱尔兰税率")
            print(f"   将分 {(len(missing_records) + self.batch_size - 1) // self.batch_size} 批处理\n")

            # 2. 分批处理
            for i in range(0, len(missing_records), self.batch_size):
                batch = missing_records[i:i + self.batch_size]
                batch_num = i // self.batch_size + 1
                total_batches = (len(missing_records) + self.batch_size - 1) // self.batch_size

                print(f"📦 批次 {batch_num}/{total_batches} ({len(batch)} 条记录)...")

                await self._process_batch(scraper, batch)

                # 延迟
                if i + self.batch_size < len(missing_records):
                    await asyncio.sleep(self.delay)

            # 3. 保存统计
            self.stats['end_time'] = time.time()
            self._save_report()
            self._print_summary()

        except Exception as e:
            print(f"\n❌ 补齐任务失败: {e}")
            import traceback
            traceback.print_exc()
            self.stats['end_time'] = time.time()
            return self.stats

        return self.stats

    def _get_missing_ni_records(self, db) -> List[Dict]:
        """
        获取缺失北爱尔兰税率的记录

        Args:
            db: TariffDB 实例

        Returns:
            List[Dict]: 缺失记录列表
        """
        try:
            conn = db.conn
            cursor = conn.execute("""
                SELECT code, description, url
                FROM tariffs
                WHERE north_ireland_rate IS NULL OR north_ireland_rate = ''
                ORDER BY code
            """)

            records = []
            for row in cursor.fetchall():
                records.append({
                    'code': row[0],
                    'description': row[1],
                    'url': row[2]
                })

            return records

        except Exception as e:
            print(f"❌ 查询失败: {e}")
            return []

    async def _process_batch(self, scraper, batch: List[Dict]):
        """
        处理一批记录

        Args:
            scraper: TariffScraper 实例
            batch: 记录列表
        """
        # 构建北爱尔兰 URLs
        ni_urls = [
            f"https://www.trade-tariff.service.gov.uk/xi/commodities/{record['code']}"
            for record in batch
        ]

        # 批量爬取
        results = await scraper.scrape_with_retry(ni_urls)

        # 处理结果
        success_count = 0
        fail_count = 0
        no_data_count = 0

        for idx, (status, content) in enumerate(results):
            record = batch[idx]
            code = record['code']
            self.stats['total_processed'] += 1

            if status == 200 and content:
                # 解析北爱尔兰页面
                ni_tariff = scraper.parse_commodity_page(
                    content,
                    url=ni_urls[idx]
                )

                if ni_tariff and ni_tariff.get('rate'):
                    # 更新数据库
                    try:
                        scraper.db.update_north_ireland_tariff(
                            code=code,
                            north_ireland_rate=ni_tariff['rate'],
                            north_ireland_url=ni_urls[idx]
                        )
                        success_count += 1
                        self.stats['successful'] += 1
                    except Exception as e:
                        print(f"   ⚠️  {code}: 更新失败 - {e}")
                        fail_count += 1
                        self.stats['failed'] += 1
                        self.stats['failed_codes'].append({
                            'code': code,
                            'reason': f'db_update_error: {e}'
                        })
                else:
                    # 页面存在但无税率数据
                    no_data_count += 1
                    self.stats['no_data'] += 1
            else:
                # 请求失败
                fail_count += 1
                self.stats['failed'] += 1
                self.stats['failed_codes'].append({
                    'code': code,
                    'reason': f'http_error: {status}'
                })

        print(f"   ✅ 成功: {success_count}, ⚠️  无数据: {no_data_count}, ❌ 失败: {fail_count}")

    def _save_report(self):
        """保存补齐报告"""
        report_file = f"backfill_ni_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"

        elapsed = self.stats['end_time'] - self.stats['start_time']
        self.stats['elapsed_seconds'] = elapsed

        try:
            with open(report_file, 'w', encoding='utf-8') as f:
                json.dump(self.stats, f, indent=2, ensure_ascii=False)
            print(f"\n💾 补齐报告已保存: {report_file}")
        except Exception as e:
            print(f"\n⚠️  保存报告失败: {e}")

    def _print_summary(self):
        """打印补齐摘要"""
        elapsed = self.stats['end_time'] - self.stats['start_time']

        print(f"\n{'='*60}")
        print(f"📊 补齐任务完成")
        print(f"{'='*60}")
        print(f"总缺失记录: {self.stats['total_missing']:,}")
        print(f"已处理: {self.stats['total_processed']:,}")
        print(f"✅ 成功补齐: {self.stats['successful']:,}")
        print(f"⚠️  无北爱尔兰数据: {self.stats['no_data']:,}")
        print(f"❌ 失败: {self.stats['failed']:,}")
        print(f"⏱️  耗时: {elapsed:.2f} 秒")

        if self.stats['successful'] > 0:
            success_rate = self.stats['successful'] / self.stats['total_processed'] * 100
            print(f"📈 成功率: {success_rate:.1f}%")

        # 显示失败记录（前10个）
        if self.stats['failed_codes']:
            print(f"\n❌ 失败记录（前10个）:")
            for failed in self.stats['failed_codes'][:10]:
                print(f"   - {failed['code']}: {failed['reason']}")

            if len(self.stats['failed_codes']) > 10:
                print(f"   ... 还有 {len(self.stats['failed_codes']) - 10} 个失败记录")

        print(f"{'='*60}\n")


def main():
    """主函数 - 命令行接口"""
    import argparse

    parser = argparse.ArgumentParser(description="北爱尔兰税率补齐脚本")
    parser.add_argument(
        "--db-path",
        type=str,
        help="数据库路径（默认使用 TariffDB 默认路径）"
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=50,
        help="批量大小，默认 50"
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.1,
        help="批次间延迟（秒），默认 0.1"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="试运行模式（只查询不更新）"
    )

    args = parser.parse_args()

    if args.dry_run:
        print("🔍 试运行模式 - 只查询缺失记录\n")

        from src.db.database import TariffDB
        db = TariffDB(db_path=args.db_path)

        backfiller = NIRateBackfiller(
            db_path=args.db_path,
            batch_size=args.batch_size,
            delay=args.delay
        )

        missing_records = backfiller._get_missing_ni_records(db)
        print(f"📊 发现 {len(missing_records)} 条缺失记录")

        if missing_records:
            print(f"\n前10条缺失记录:")
            for record in missing_records[:10]:
                print(f"   - {record['code']}: {record['description'][:50]}")

            if len(missing_records) > 10:
                print(f"   ... 还有 {len(missing_records) - 10} 条")

        sys.exit(0)

    # 执行补齐
    backfiller = NIRateBackfiller(
        db_path=args.db_path,
        batch_size=args.batch_size,
        delay=args.delay
    )

    result = asyncio.run(backfiller.execute())

    # 根据结果设置退出码
    if result['failed'] > result['successful']:
        sys.exit(1)
    else:
        sys.exit(0)


if __name__ == "__main__":
    main()

