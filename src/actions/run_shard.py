#!/usr/bin/env python3
"""
单个 Shard 执行器 - 执行单个分片的爬取任务

功能：
1. 读取分配的章节列表
2. 执行爬取并实时写入分片数据库
3. 定期检查超时并保存进度
4. 超时时优雅退出并保存当前状态
"""

import asyncio
import sys
import os
import json
import time
from typing import List, Dict, Optional
from pathlib import Path

# 添加项目根目录到 Python 路径
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)

from src.actions.chapter_scheduler import ChapterScheduler
from src.actions.progress_monitor import ProgressMonitor, ShardProgress


class ShardExecutor:
    """单个 Shard 执行器"""

    def __init__(
        self,
        shard_id: str,
        chapters: List[str],
        data_type: str = "uk",
        output_db: str = None
    ):
        """
        初始化 Shard 执行器

        Args:
            shard_id: Shard ID (如 "shard_0")
            chapters: 分配的章节列表
            data_type: 数据类型 ("uk" 或 "ni")
            output_db: 输出数据库路径
        """
        self.shard_id = shard_id
        self.chapters = chapters
        self.data_type = data_type
        self.output_db = output_db or f"tariffs_{shard_id}.db"

        self.monitor = ProgressMonitor()
        self.network_errors = []  # 累积网络错误 code（400/500），末尾重试
        self.shard_db = None  # 保存数据库引用以便后续关闭
        self.results = {
            "shard_id": shard_id,
            "status": "unknown",
            "chapters_assigned": chapters,
            "chapters_completed": [],
            "chapters_failed": [],
            "start_time": None,
            "end_time": None,
            "interrupted": False,
            "interrupt_reason": None,
        }

    async def execute(self):
        """执行 shard 爬取任务"""
        self.results["start_time"] = time.time()

        # 注册 shard 到监控器
        self.monitor.register_shard(self.shard_id, len(self.chapters))

        print(f"\n{'='*60}")
        print(f"🚀 Shard {self.shard_id} 开始执行")
        print(f"📋 分配章节: {len(self.chapters)} 个")
        print(f"📁 输出数据库: {self.output_db}")
        print(f"{'='*60}\n")

        try:
            # 动态导入爬虫模块（使用新的 src 模块结构）
            from src.core.scraper import TariffScraper
            from src.db.database import TariffDB

            # 创建独立的数据库实例
            shard_db = TariffDB(db_path=self.output_db)
            self.shard_db = shard_db  # 保存引用以便后续关闭
            print(f"✅ 使用独立数据库: {self.output_db}")

            # 🔧 启用 WAL 模式以允许更好的并发读取
            # 这样在合并时不会被锁定
            try:
                shard_db.conn.execute("PRAGMA journal_mode=WAL")
                shard_db.conn.execute("PRAGMA synchronous=NORMAL")
                print(f"✅ 已启用 WAL 模式")
            except Exception as e:
                print(f"⚠️  启用 WAL 模式失败: {e}")

            scraper = TariffScraper()
            # 替换为分片数据库
            scraper.db = shard_db
            # 清空现有编码缓存（因为我们要爬取新的数据）
            scraper.existing_codes = set()

            total_urls = 0
            completed_urls = []

            # 遍历每个章节
            for i, chapter in enumerate(self.chapters):
                # 检查超时
                should_stop, reason = self.monitor.should_create_partial(self.shard_id)
                if should_stop:
                    print(f"\n⏸️  收到停止信号: {reason}")
                    self.results["interrupted"] = True
                    self.results["interrupt_reason"] = reason
                    break

                # 更新进度
                print(f"📖 [{i+1}/{len(self.chapters)}] 处理章节 {chapter}...")
                self.monitor.update_shard_progress(
                    self.shard_id,
                    completed_chapters=i,
                    current_chapter=chapter,
                    urls_processed=total_urls,
                    status="in_progress"
                )

                # 爬取章节
                try:
                    chapter_urls = await self._scrape_chapter(
                        scraper, chapter
                    )

                    if chapter_urls:
                        completed_urls.extend(chapter_urls)
                        total_urls = len(completed_urls)
                        self.results["chapters_completed"].append(chapter)
                    else:
                        print(f"⚠️  章节 {chapter} 无数据")
                        self.results["chapters_failed"].append(chapter)

                except Exception as e:
                    print(f"❌ 章节 {chapter} 失败: {e}")
                    self.results["chapters_failed"].append(chapter)
                    continue

            # 最终更新进度
            self.monitor.update_shard_progress(
                self.shard_id,
                completed_chapters=len(self.results["chapters_completed"]),
                current_chapter="",
                urls_processed=total_urls,
                status="interrupted" if self.results["interrupted"] else "completed"
            )

            # 设置最终状态
            if self.results["interrupted"]:
                self.results["status"] = "partial"
            elif self.results["chapters_failed"]:
                self.results["status"] = "partial"
            else:
                self.results["status"] = "success"

            # 末尾重试网络错误 code（400/500），仍失败的记入 failed_codes 供客户端补全
            self.results["failed_codes"] = []
            if self.network_errors:
                print(f"\n🔄 末尾重试 {len(self.network_errors)} 个网络错误 code...")
                try:
                    self.results["failed_codes"] = await scraper.retry_network_errors(self.network_errors)
                    if self.results["failed_codes"]:
                        print(f"   ⚠️ 仍有 {len(self.results['failed_codes'])} 个失败（记入 failed_codes 供客户端补全）")
                except Exception as re:
                    print(f"⚠️ 末尾重试失败: {re}")

        except Exception as e:
            print(f"\n❌ Shard 执行失败: {e}")
            import traceback
            traceback.print_exc()
            self.results["status"] = "failed"
            self.results["error"] = str(e)

        finally:
            self.results["end_time"] = time.time()
            self._save_results()

        # 打印结果摘要
        self._print_summary()

        # 🔧 关闭数据库连接以释放文件锁
        self._close_database()

        # 🔧 重要：复制数据库到当前目录以便上传 artifact
        # 必须在关闭连接后才能复制，否则会遇到 "database is locked" 错误
        self._copy_db_to_current_dir()

        return self.results

    async def _scrape_chapter(
        self,
        scraper,
        chapter: str
    ) -> List[str]:
        """
        爬取单个章节

        Args:
            scraper: TariffScraper 实例
            chapter: 章节号

        Returns:
            List[str]: 处理的 URL 列表
        """
        from bs4 import BeautifulSoup
        import logging
        import re
        from src.core.scraper import classify_commodity

        logger = logging.getLogger(__name__)
        processed_urls = []

        try:
            # 1. 构建章节URL
            chapter_url = f"{scraper.base_url}/chapters/{chapter}"
            logger.info(f"正在爬取章节: {chapter_url}")

            # 2. 爬取章节页面
            results = await scraper.scrape_with_retry([chapter_url])
            status, content = results[0]

            if status != 200 or not content:
                logger.warning(f"章节 {chapter} 页面失败 (status={status})")
                return []

            # 3. 解析heading链接（使用scraper的现有方法）
            heading_urls = scraper.parse_heading_links(content)
            logger.info(f"章节 {chapter} 找到 {len(heading_urls)} 个heading")

            # 4. 分批爬取heading页面并解析commodity链接
            batch_size = 20
            for i in range(0, len(heading_urls), batch_size):
                heading_batch = heading_urls[i:i + batch_size]

                heading_results = await scraper.scrape_with_retry(heading_batch)
                commodity_urls = []

                for idx, (h_status, h_content) in enumerate(heading_results):
                    if h_status == 200 and h_content:
                        # DEBUG: 保存第一个 heading 页面用于调试
                        if i == 0 and idx == 0:
                            debug_file = f"debug_heading_{chapter}.html"
                            try:
                                with open(debug_file, 'w', encoding='utf-8') as f:
                                    f.write(h_content)
                                logger.info(f"  [DEBUG] 保存第一个 heading 页面到: {debug_file}")
                            except:
                                pass

                        # 使用scraper的parse_commodity_links方法
                        heading_commodities = scraper.parse_commodity_links(h_content)
                        commodity_urls.extend(heading_commodities)

                        logger.info(f"    Heading {idx+1}: 找到 {len(heading_commodities)} 个 commodity")
                    else:
                        logger.warning(f"    Heading {idx+1} 失败 (status={h_status})")

                logger.info(f"  Heading批次 {i//batch_size + 1}: 找到 {len(commodity_urls)} 个commodity")

                # 5. 分批爬取commodity页面并解析保存
                for j in range(0, len(commodity_urls), batch_size):
                    commodity_batch = commodity_urls[j:j + batch_size]

                    commodity_results = await scraper.scrape_with_retry(commodity_batch)

                    # 收集需要更新北爱尔兰数据的商品
                    ni_updates = []

                    for k, (c_status, c_content) in enumerate(commodity_results):
                        # 提取请求的 code（删除/保存都需要）
                        code_match = re.search(r'/commodities/(\d+)', commodity_batch[k])
                        req_code = code_match.group(1) if code_match else None
                        # 仅 200 且有内容时才解析，避免对 404/异常页解析
                        tariff = scraper.parse_commodity_page(
                            c_content, url=commodity_batch[k]
                        ) if (c_status == 200 and c_content) else None
                        action = classify_commodity(c_status, tariff)

                        if action == 'delete':
                            # 废弃（404 或 200 无税率，如 9403208000 重定向 subheading）→ 删除
                            if req_code:
                                scraper.db.delete_tariff(req_code)
                                logger.info(f"  Commodity {req_code} 已删除 (废弃/404)")
                        elif action == 'save':
                            # 有效 → 保存并记录北爱尔兰更新
                            ni_url = f"https://www.trade-tariff.service.gov.uk/xi/commodities/{tariff['code']}"
                            scraper.db.add_tariff(
                                code=tariff['code'],
                                description=tariff['description'],
                                rate=tariff['rate'],
                                url=tariff.get('url'),
                                other_rate=tariff.get('other_rate'),
                                north_ireland_url=ni_url,  # 添加北爱尔兰 URL
                                anti_dumping_rate=tariff.get('anti_dumping_rate'),
                                countervailing_rate=tariff.get('countervailing_rate')
                            )
                            processed_urls.append(commodity_batch[k])
                            # 记录需要更新北爱尔兰数据的商品
                            ni_updates.append(tariff['code'])
                        else:  # action == 'skip'
                            # 网络错误（400/500/0 等非200非404）→ 记录待末尾重试
                            if req_code and c_status != 200 and c_status != 404:
                                self.network_errors.append((req_code, c_status))

                    # 6. 批量爬取北爱尔兰数据
                    if ni_updates:
                        ni_urls = [
                            f"https://www.trade-tariff.service.gov.uk/xi/commodities/{code}"
                            for code in ni_updates
                        ]
                        ni_results = await scraper.scrape_with_retry(ni_urls)

                        ni_success_count = 0
                        ni_failed_count = 0

                        for ni_idx, (ni_status, ni_content) in enumerate(ni_results):
                            code = ni_updates[ni_idx]

                            if ni_status == 200 and ni_content:
                                ni_tariff = scraper.parse_commodity_page(
                                    ni_content,
                                    url=ni_urls[ni_idx]
                                )
                                if ni_tariff and ni_tariff.get('rate'):
                                    scraper.db.update_north_ireland_tariff(
                                        code=code,
                                        north_ireland_rate=ni_tariff['rate'],
                                        north_ireland_url=ni_urls[ni_idx]
                                    )
                                    ni_success_count += 1
                                else:
                                    # 页面存在但无法解析税率
                                    logger.warning(f"      商品 {code} 北爱尔兰页面无税率数据")
                                    ni_failed_count += 1
                            else:
                                # 请求失败或404
                                logger.warning(f"      商品 {code} 北爱尔兰数据获取失败 (status={ni_status})")
                                ni_failed_count += 1

                        logger.info(f"    北爱尔兰数据更新: {ni_success_count}/{len(ni_updates)} 成功, {ni_failed_count} 失败")

                logger.info(f"  Commodity批次完成，本批处理了 {len(commodity_urls)} 个URL")

            return processed_urls

        except Exception as e:
            logger.error(f"爬取章节 {chapter} 失败: {e}")
            import traceback
            traceback.print_exc()
            return []

    def _save_results(self):
        """保存结果到文件"""
        result_file = f"shard_{self.shard_id}_results.json"

        try:
            with open(result_file, 'w', encoding='utf-8') as f:
                json.dump(self.results, f, indent=2, ensure_ascii=False, default=str)
            print(f"💾 结果已保存: {result_file}")
        except Exception as e:
            print(f"⚠️  保存结果失败: {e}")

    def _copy_db_to_current_dir(self):
        """复制数据库到当前目录以便上传 artifact"""
        import shutil
        import os
        import time

        try:
            # TariffDB 将数据库保存在用户目录
            # 例如：/home/runner/.uk-tax-tools/tariffs_shard_0.db
            # 我们需要复制到当前目录以便上传 artifact
            target_filename = f"tariffs_{self.shard_id}.db"

            # 构建可能的源路径
            if os.name == 'nt':  # Windows
                user_dir = os.path.expanduser("~")
                source_dir = os.path.join(user_dir, "uk-tax-tools")
            else:  # macOS/Linux
                user_dir = os.path.expanduser("~")
                source_dir = os.path.join(user_dir, ".uk-tax-tools")

            source_path = os.path.join(source_dir, target_filename)

            if os.path.exists(source_path):
                # 等待文件系统同步（确保数据库完全写入磁盘）
                time.sleep(0.5)

                # 删除目标文件（如果存在）
                if os.path.exists(target_filename):
                    os.remove(target_filename)

                # 使用 shutil.copy() 而不是 copy2() 以减少元数据操作
                # 在 GitHub Actions 环境中更可靠
                shutil.copy(source_path, target_filename)

                # 再次等待确保复制完成
                time.sleep(0.2)

                # 验证复制成功
                if os.path.exists(target_filename):
                    file_size = os.path.getsize(target_filename)
                    size_mb = file_size / (1024 * 1024)
                    print(f"✅ 数据库已复制到当前目录: {target_filename}")
                    print(f"   文件大小: {size_mb:.2f} MB ({file_size:,} 字节)")
                else:
                    print(f"❌ 复制验证失败: 目标文件不存在")
            else:
                print(f"⚠️  数据库文件不存在: {source_path}")

        except PermissionError as e:
            print(f"❌ 权限错误，数据库可能仍被锁定: {e}")
            import traceback
            traceback.print_exc()
        except Exception as e:
            print(f"⚠️  复制数据库失败: {e}")
            import traceback
            traceback.print_exc()

    def _print_summary(self):
        """打印执行摘要"""
        elapsed = self.results["end_time"] - self.results["start_time"]

        print(f"\n{'='*60}")
        print(f"📊 Shard {self.shard_id} 执行摘要")
        print(f"{'='*60}")
        print(f"📁 状态: {self.results['status']}")
        print(f"✅ 完成章节: {len(self.results['chapters_completed'])}/{len(self.chapters)}")
        print(f"❌ 失败章节: {len(self.results['chapters_failed'])}")

        if self.results["interrupted"]:
            print(f"⏸️  中断原因: {self.results['interrupt_reason']}")

        print(f"⏱️  耗时: {elapsed:.2f} 秒")
        print(f"{'='*60}\n")

    def _close_database(self):
        """关闭数据库连接以释放文件锁"""
        if self.shard_db:
            try:
                # TariffDB 使用 threading.local() 管理连接
                # 需要访问 _local.conn 来关闭连接
                if hasattr(self.shard_db, '_local') and hasattr(self.shard_db._local, 'conn'):
                    conn = self.shard_db._local.conn

                    # 1. 提交所有未提交的事务
                    try:
                        conn.commit()
                    except:
                        pass  # 忽略提交错误

                    # 2. 执行 WAL checkpoint 将所有更改刷新到主数据库
                    try:
                        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                        conn.execute("PRAGMA optimize")
                    except:
                        pass  # 忽略优化错误

                    # 3. 关闭连接
                    conn.close()

                    # 4. 删除连接引用，防止后续访问
                    delattr(self.shard_db._local, 'conn')
                    print(f"✅ 数据库连接已关闭")

                # 5. 强制垃圾回收以释放任何残留引用
                import gc
                gc.collect()

                # 6. 删除数据库对象引用
                self.shard_db = None

                # 7. 显式删除 WAL 文件以释放文件锁
                import os
                # 构建数据库文件路径
                if os.name == 'nt':  # Windows
                    user_dir = os.path.expanduser("~")
                    db_dir = os.path.join(user_dir, "uk-tax-tools")
                else:  # macOS/Linux
                    user_dir = os.path.expanduser("~")
                    db_dir = os.path.join(user_dir, ".uk-tax-tools")

                db_filename = f"tariffs_{self.shard_id}.db"
                db_path = os.path.join(db_dir, db_filename)

                # 删除 WAL 和 SHM 文件
                wal_file = f"{db_path}-wal"
                shm_file = f"{db_path}-shm"

                for f in [wal_file, shm_file]:
                    if os.path.exists(f):
                        try:
                            os.remove(f)
                            print(f"✅ 已删除 WAL 文件: {os.path.basename(f)}")
                        except Exception as e:
                            print(f"⚠️  删除 WAL 文件失败 ({os.path.basename(f)}): {e}")

            except Exception as e:
                print(f"⚠️  关闭数据库连接失败: {e}")
                import traceback
                traceback.print_exc()


def main():
    """主函数 - 命令行接口"""
    import argparse

    parser = argparse.ArgumentParser(description="单个 Shard 执行器")
    parser.add_argument(
        "--shard-id",
        type=str,
        required=True,
        help="Shard ID (如 shard_0)"
    )
    parser.add_argument(
        "--chapters",
        type=str,
        required=False,
        help="章节列表（JSON 数组格式或逗号分隔）"
    )
    parser.add_argument(
        "--data-type",
        type=str,
        default="uk",
        choices=["uk", "ni"],
        help="数据类型"
    )
    parser.add_argument(
        "--output-db",
        type=str,
        help="输出数据库路径"
    )
    parser.add_argument(
        "--task-file",
        type=str,
        help="从文件读取任务分配（JSON 格式）"
    )

    args = parser.parse_args()

    # 解析章节列表
    if args.task_file:
        # 从文件读取任务分配
        with open(args.task_file, 'r') as f:
            task_assignment = json.load(f)
        chapters = task_assignment.get(args.shard_id, [])
    elif args.chapters:
        if args.chapters.startswith("["):
            # JSON 格式
            chapters = json.loads(args.chapters)
        else:
            # 逗号分隔
            chapters = args.chapters.split(",")
    else:
        print(f"❌ 必须提供 --chapters 或 --task-file 参数")
        sys.exit(1)

    if not chapters:
        print(f"❌ 没有分配章节给 {args.shard_id}")
        sys.exit(1)

    # 创建并执行 shard
    executor = ShardExecutor(
        shard_id=args.shard_id,
        chapters=chapters,
        data_type=args.data_type,
        output_db=args.output_db
    )

    # 执行
    result = asyncio.run(executor.execute())

    # 根据状态设置退出码
    if result["status"] == "failed":
        sys.exit(1)
    elif result["status"] == "partial":
        sys.exit(0)  # 部分成功也算成功
    else:
        sys.exit(0)


if __name__ == "__main__":
    main()
