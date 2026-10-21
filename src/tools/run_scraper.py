#!/usr/bin/env python3
"""
GitHub Actions 脚本: 执行关税数据爬取
使用 scraper.py 进行数据爬取和更新
"""

import asyncio
import json
import os
import sys
import time
from datetime import datetime

# 添加项目根目录到 Python 路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))


async def run_initial_scrape():
    """运行初始爬取（数据库为空时使用）

    Returns:
        爬取结果字典
    """
    try:
        from src.core.scraper import TariffScraper
        from src.db.database import TariffDB

        print("🔧 执行初始数据爬取...")

        scraper = TariffScraper()
        start_time = time.time()

        # 执行爬取
        await scraper.scrape_tariffs()

        processing_time = (time.time() - start_time) / 60

        # 获取数据库记录数
        record_count = scraper.get_db_count()

        # 获取北爱尔兰数据数量（不再硬编码为0）
        db = TariffDB()
        ni_count = db.get_north_ireland_count()

        results = {
            'total': record_count,
            'completed': record_count,
            'successful': record_count,
            'failed': 0,
            'skipped': 0,
            'uk_updated': record_count,
            'ni_updated': ni_count,  # 从数据库统计实际数量
            'processing_time_minutes': processing_time,
            'mode': 'initial_scrape'
        }

        print(f"✅ 初始爬取完成: 共爬取 {record_count} 条记录")
        return results

    except ImportError as e:
        print(f"❌ 爬虫导入失败: {e}", file=sys.stderr)
        return None
    except Exception as e:
        print(f"❌ 初始爬取失败: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return None


async def run_update_scrape(update_uk: bool = True, update_ni: bool = True, batch_size: int = 100, delay: float = 0.2):
    """运行更新爬取（数据库有数据时使用）

    Args:
        update_uk: 是否更新英国数据
        update_ni: 是否更新北爱尔兰数据
        batch_size: 批量大小
        delay: 批次间延迟（秒）

    Returns:
        爬取结果字典
    """
    try:
        from scraper import BatchUpdateManager

        print(f"🔧 执行数据更新 (UK={update_uk}, NI={update_ni}, 批量={batch_size}, 延迟={delay}s)")

        # 创建进度回调
        def progress_callback(completed, total, message):
            if completed % 100 == 0 or completed == total:
                print(f"📊 进度: {completed}/{total} - {message}")

        def status_callback(message):
            print(f"📝 状态: {message}")

        manager = BatchUpdateManager(
            progress_callback=progress_callback,
            status_callback=status_callback
        )

        start_time = time.time()

        results = await manager.update_all_tariffs(
            update_uk=update_uk,
            update_ni=update_ni,
            batch_size=batch_size,
            delay_between_batches=delay
        )

        processing_time = (time.time() - start_time) / 60
        results['processing_time_minutes'] = processing_time
        results['mode'] = 'update'

        print(f"✅ 更新完成: 成功={results.get('successful', 0)}, 失败={results.get('failed', 0)}")
        return results

    except ImportError as e:
        print(f"❌ 爬虫导入失败: {e}", file=sys.stderr)
        return None
    except Exception as e:
        print(f"❌ 更新失败: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return None


def check_database_status():
    """检查数据库状态

    Returns:
        (exists: bool, record_count: int)
    """
    try:
        from tariff_db import TariffDB

        db = TariffDB()
        record_count = db.get_record_count()

        return True, record_count
    except Exception as e:
        print(f"⚠️ 检查数据库状态失败: {e}")
        return False, 0


def save_results(results: dict, output_path: str = 'update_results.json'):
    """保存结果到文件"""
    try:
        # 确保所有数值都是可序列化的
        serializable_results = {}
        for key, value in results.items():
            if isinstance(value, (int, float, str, bool, type(None))):
                serializable_results[key] = value
            elif isinstance(value, (list, tuple)):
                serializable_results[key] = list(value)
            elif isinstance(value, dict):
                serializable_results[key] = {k: v for k, v in value.items() if isinstance(v, (int, float, str, bool, type(None), list, dict))}
            else:
                serializable_results[key] = str(value)

        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(serializable_results, f, indent=2, default=str, ensure_ascii=False)

        print(f"💾 结果已保存: {output_path}")
        return True

    except Exception as e:
        print(f"❌ 保存结果失败: {e}", file=sys.stderr)
        return False


def print_summary(results: dict):
    """打印结果摘要"""
    if not results:
        print("❌ 没有有效的结果")
        return

    print("\n" + "="*50)
    print("📊 爬取结果摘要")
    print("="*50)

    mode = results.get('mode', 'unknown')
    print(f"🔧 运行模式: {'初始爬取' if mode == 'initial_scrape' else '数据更新'}")

    total = results.get('total', 0)
    completed = results.get('completed', 0)
    successful = results.get('successful', 0)
    failed = results.get('failed', 0)
    skipped = results.get('skipped', 0)
    uk_updated = results.get('uk_updated', 0)
    ni_updated = results.get('ni_updated', 0)
    processing_time = results.get('processing_time_minutes', 0)

    print(f"📋 总记录数: {total:,}")
    print(f"✅ 已完成: {completed:,}")
    print(f"🎯 成功: {successful:,}")
    print(f"❌ 失败: {failed:,}")
    print(f"⏭️ 跳过: {skipped:,}")

    if uk_updated > 0 or ni_updated > 0:
        print(f"🇬🇧 英国更新: {uk_updated:,}")
        print(f"🇮🇪 北爱尔兰更新: {ni_updated:,}")

    if processing_time > 0:
        rate = completed / processing_time if processing_time > 0 else 0
        print(f"⏱️ 处理时间: {processing_time:.1f} 分钟")
        print(f"🚀 处理速率: {rate:.1f} 条/分钟")

    if completed > 0:
        success_rate = successful / completed * 100
        print(f"📈 成功率: {success_rate:.2f}%")

    print("="*50)


def main():
    """主函数"""
    # 解析环境变量参数
    args = {
        'update_uk': os.getenv('INPUT_UPDATE_UK', 'true').lower() == 'true',
        'update_ni': os.getenv('INPUT_UPDATE_NI', 'true').lower() == 'true',
        'batch_size': int(os.getenv('INPUT_BATCH_SIZE', '100')),
        'delay': float(os.getenv('INPUT_DELAY', '0.2')),
        'output_file': os.getenv('OUTPUT_FILE', 'update_results.json'),
        'force_initial': os.getenv('FORCE_INITIAL_SCRAPE', 'false').lower() == 'true'
    }

    print("🚀 开始执行关税数据爬取...")
    print(f"📋 配置: UK={args['update_uk']}, NI={args['update_ni']}")
    print(f"⚙️ 参数: 批量={args['batch_size']}, 延迟={args['delay']}s")

    try:
        # 检查数据库状态
        db_exists, record_count = check_database_status()

        print(f"📊 数据库状态: {'存在' if db_exists else '不存在'}, 记录数: {record_count}")

        # 决定运行模式
        if args['force_initial'] or not db_exists or record_count == 0:
            print("📍 模式: 初始爬取（数据库为空或强制初始化）")
            results = asyncio.run(run_initial_scrape())
        else:
            print("📍 模式: 数据更新")
            results = asyncio.run(run_update_scrape(
                update_uk=args['update_uk'],
                update_ni=args['update_ni'],
                batch_size=args['batch_size'],
                delay=args['delay']
            ))

        if not results:
            print("❌ 爬虫执行失败", file=sys.stderr)
            sys.exit(1)

        # 保存结果
        if not save_results(results, args['output_file']):
            sys.exit(1)

        # 打印摘要
        print_summary(results)

        # 输出环境变量供 GitHub Actions 使用（新版语法）
        github_output = os.getenv('GITHUB_OUTPUT')
        if github_output:
            with open(github_output, 'a') as f:
                f.write(f"successful={results.get('successful', 0)}\n")
                f.write(f"failed={results.get('failed', 0)}\n")
                f.write(f"uk_updated={results.get('uk_updated', 0)}\n")
                f.write(f"ni_updated={results.get('ni_updated', 0)}\n")
                f.write(f"total={results.get('total', 0)}\n")
                f.write(f"completed={results.get('completed', 0)}\n")
                f.write(f"mode={results.get('mode', 'unknown')}\n")
        else:
            # 兼容旧版语法
            print(f"::set-output name=successful::{results.get('successful', 0)}")
            print(f"::set-output name=failed::{results.get('failed', 0)}")
            print(f"::set-output name=uk_updated::{results.get('uk_updated', 0)}")
            print(f"::set-output name=ni_updated::{results.get('ni_updated', 0)}")
            print(f"::set-output name=total::{results.get('total', 0)}")
            print(f"::set-output name=completed::{results.get('completed', 0)}")

        # 如果失败率过高，设置警告
        completed_count = results.get('completed', 0)
        failed_count = results.get('failed', 0)
        if completed_count > 0:
            failure_rate = failed_count / completed_count
            if failure_rate > 0.1:  # 失败率超过10%
                print(f"::warning::失败率过高: {failure_rate:.2%}")

    except KeyboardInterrupt:
        print("⏹️ 用户中断执行", file=sys.stderr)
        sys.exit(130)
    except Exception as e:
        print(f"❌ 执行失败: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
