#!/usr/bin/env python3
"""清理僵尸商品编码（范围盲区修复）

背景：run_shard 只抓 heading 链接内的 commodity。已从链接消失的废弃 code
（如 9403208000 重定向到 subheading）成为僵尸，永久残留——这是 run_shard 架构上
无法解决的"范围盲区"。

方案 B（差集 last_updated）：找出 last_updated 早于本次抓取窗口的 code
（未抓到 = 潜在僵尸），调 auto_update_single（PR#2 should_delete_code 双地验证），
废弃则删除。聚焦（不全量抓 16636）+ 准确（验证而非盲删）。
"""
import argparse
import asyncio
import logging
import os
import sys
from datetime import datetime, timedelta
from typing import List, Dict

logger = logging.getLogger(__name__)


class DeprecatedCleaner:
    """僵尸 code 清理器（差集 last_updated + 验证删除）"""

    def __init__(self, db, scraper):
        self.db = db
        self.scraper = scraper

    def get_stale_codes(self, hours: int = 6) -> List[str]:
        """差集筛选：last_updated 早于 now-hours 的 code（本次未抓到 = 潜在僵尸）

        CI 抓取+merge 应在 hours 窗口内完成；窗口外的 code 视为本次未抓到。
        """
        threshold = datetime.utcnow() - timedelta(hours=hours)
        threshold_str = threshold.strftime('%Y-%m-%d %H:%M:%S')
        try:
            cur = self.db.conn.execute(
                "SELECT code FROM tariffs WHERE last_updated < ? OR last_updated IS NULL",
                (threshold_str,)
            )
            return [row[0] for row in cur.fetchall()]
        except Exception as e:
            logger.error(f"差集筛选失败: {e}")
            return []

    async def cleanup(self, hours: int = 6, concurrency: int = 20) -> Dict:
        """对差集 code 并发验证，废弃则删除。

        复用 scraper.auto_update_single（含 PR#2 should_delete_code 双地判定）。
        """
        stale = self.get_stale_codes(hours)
        if not stale:
            logger.info("无差集 code 需清理")
            return {'checked': 0, 'deleted': 0}

        logger.info(f"差集清理: {len(stale)} 个潜在僵尸 code 待验证")
        semaphore = asyncio.Semaphore(concurrency)
        deleted = 0

        async def check(code):
            nonlocal deleted
            async with semaphore:
                uk_url = f"https://www.trade-tariff.service.gov.uk/commodities/{code}"
                ni_url = f"https://www.trade-tariff.service.gov.uk/xi/commodities/{code}"
                try:
                    # auto_update_single 内部 should_delete_code 判定，废弃则 delete_tariff
                    await self.scraper.auto_update_single(code, uk_url, ni_url)
                    if self.db.get_tariff(code) is None:
                        deleted += 1
                        logger.info(f"已删除僵尸 code: {code}")
                except Exception as e:
                    logger.warning(f"验证 {code} 失败: {e}")

        await asyncio.gather(*[check(c) for c in stale])
        logger.info(f"差集清理完成: 验证 {len(stale)}, 删除 {deleted}")
        return {'checked': len(stale), 'deleted': deleted}


def main():
    """命令行入口（CI 集成用）"""
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

    parser = argparse.ArgumentParser(description="清理僵尸商品编码（范围盲区修复）")
    parser.add_argument("--db", default="tariffs.db", help="数据库路径")
    parser.add_argument("--hours", type=int, default=6, help="抓取窗口（小时），窗口外的 code 视为本次未抓到")
    parser.add_argument("--concurrency", type=int, default=20, help="并发验证数")
    args = parser.parse_args()

    from src.db.database import TariffDB
    from src.core.scraper import TariffScraper

    logging.basicConfig(level=logging.INFO)

    db = TariffDB(args.db)
    scraper = TariffScraper()
    scraper.db = db

    cleaner = DeprecatedCleaner(db, scraper)
    result = asyncio.run(cleaner.cleanup(hours=args.hours, concurrency=args.concurrency))

    print(f"\n{'='*60}")
    print(f"🧹 僵尸 code 清理完成")
    print(f"   验证: {result['checked']}")
    print(f"   删除: {result['deleted']}")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
