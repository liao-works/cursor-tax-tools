#!/usr/bin/env python3
"""
进度监控器 - 实时监控进度，决策何时停止并创建部分发布

功能：
1. 实时监控所有 shard 进度
2. 计算剩余时间
3. 动态决策是否创建部分发布
4. 保存进度报告
"""

import json
import sys
import os
import time
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta

# 添加项目根目录到 Python 路径
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)


@dataclass
class ShardProgress:
    """单个 Shard 的进度数据"""
    shard_id: str
    total_chapters: int
    completed_chapters: int
    current_chapter: str
    urls_processed: int
    status: str  # in_progress, completed, interrupted
    start_time: float
    last_update: float

    def to_dict(self) -> dict:
        """转换为字典"""
        return asdict(self)

    def get_progress_percent(self) -> float:
        """计算完成百分比"""
        if self.total_chapters == 0:
            return 0.0
        return (self.completed_chapters / self.total_chapters) * 100


class ProgressMonitor:
    """进度监控器 - 实时监控并决策超时"""

    # GitHub Actions 超时时间（秒）
    GITHUB_ACTIONS_TIMEOUT = 7200  # 120分钟 = 7200秒

    def __init__(self, timeout_buffer: int = 20):
        """
        初始化进度监控器

        Args:
            timeout_buffer: 提前停止的时间缓冲（分钟），默认 20 分钟
        """
        self.timeout_buffer = timeout_buffer
        self.start_time = time.time()
        self.check_interval = 300  # 5分钟检查一次
        self.last_check_time = self.start_time

        self.shards: Dict[str, ShardProgress] = {}
        self.global_metrics = {
            "requests_total": 0,
            "requests_successful": 0,
            "requests_failed": 0,
        }

    def register_shard(self, shard_id: str, total_chapters: int):
        """
        注册一个新的 shard

        Args:
            shard_id: Shard ID
            total_chapters: 该 shard 的总章节数
        """
        self.shards[shard_id] = ShardProgress(
            shard_id=shard_id,
            total_chapters=total_chapters,
            completed_chapters=0,
            current_chapter="",
            urls_processed=0,
            status="in_progress",
            start_time=time.time(),
            last_update=time.time()
        )
        print(f"📊 注册 Shard: {shard_id} (共 {total_chapters} 个章节)")

    def update_shard_progress(
        self,
        shard_id: str,
        completed_chapters: int,
        current_chapter: str,
        urls_processed: int,
        status: str = "in_progress"
    ):
        """
        更新 shard 进度

        Args:
            shard_id: Shard ID
            completed_chapters: 已完成章节数
            current_chapter: 当前章节
            urls_processed: 已处理 URL 数
            status: 状态 (in_progress, completed, interrupted)
        """
        if shard_id not in self.shards:
            print(f"⚠️  未注册的 shard: {shard_id}")
            return

        shard = self.shards[shard_id]
        shard.completed_chapters = completed_chapters
        shard.current_chapter = current_chapter
        shard.urls_processed = urls_processed
        shard.status = status
        shard.last_update = time.time()

        # 更新全局指标
        self.global_metrics["requests_total"] = urls_processed

        # 保存进度到文件
        self._save_shard_progress(shard_id)

    def _save_shard_progress(self, shard_id: str):
        """
        保存 shard 进度到文件

        Args:
            shard_id: Shard ID
        """
        if shard_id not in self.shards:
            return

        progress_file = f"shard_{shard_id}_progress.json"
        shard = self.shards[shard_id]

        progress_data = {
            **shard.to_dict(),
            "progress_percent": shard.get_progress_percent(),
            "elapsed_minutes": (time.time() - shard.start_time) / 60,
        }

        try:
            with open(progress_file, 'w', encoding='utf-8') as f:
                json.dump(progress_data, f, indent=2, ensure_ascii=False, default=str)
        except Exception as e:
            print(f"⚠️  保存进度失败 ({shard_id}): {e}")

    def should_create_partial(self, shard_id: str) -> Tuple[bool, str]:
        """
        判断是否应该停止并创建部分发布

        决策逻辑：
        1. 计算剩余时间
        2. 评估当前任务进度
        3. 如果任务快完成（>80%）继续
        4. 如果剩余时间不足，停止

        Args:
            shard_id: 当前 shard ID

        Returns:
            Tuple[bool, str]: (是否停止, 原因)
        """
        elapsed = time.time() - self.start_time
        remaining = self.GITHUB_ACTIONS_TIMEOUT - elapsed
        remaining_minutes = int(remaining / 60)

        # 剩余时间不足
        if remaining < self.timeout_buffer * 60:
            if shard_id in self.shards:
                current_progress = self.shards[shard_id].get_progress_percent()

                if current_progress > 80:
                    return (
                        False,
                        f"当前任务接近完成 ({current_progress:.1f}%)，继续执行"
                    )
                else:
                    return (
                        True,
                        f"剩余时间不足 {remaining_minutes} 分钟（当前进度 {current_progress:.1f}%），创建部分发布"
                    )
            else:
                return (
                    True,
                    f"剩余时间不足 {remaining_minutes} 分钟，创建部分发布"
                )

        return (False, "继续执行")

    def get_global_progress(self) -> dict:
        """
        获取全局进度统计

        Returns:
            dict: 全局进度数据
        """
        elapsed = time.time() - self.start_time
        remaining = self.GITHUB_ACTIONS_TIMEOUT - elapsed

        total_chapters = sum(s.total_chapters for s in self.shards.values())
        total_completed = sum(s.completed_chapters for s in self.shards.values())
        total_urls = sum(s.urls_processed for s in self.shards.values())

        overall_progress = (total_completed / total_chapters * 100) if total_chapters > 0 else 0

        return {
            "elapsed_minutes": int(elapsed / 60),
            "remaining_minutes": int(remaining / 60),
            "total_chapters": total_chapters,
            "completed_chapters": total_completed,
            "overall_progress_percent": overall_progress,
            "total_urls_processed": total_urls,
            "active_shards": sum(1 for s in self.shards.values() if s.status == "in_progress"),
            "completed_shards": sum(1 for s in self.shards.values() if s.status == "completed"),
            "shard_details": {
                shard_id: {
                    "progress": s.get_progress_percent(),
                    "status": s.status,
                    "completed": s.completed_chapters,
                    "total": s.total_chapters
                }
                for shard_id, s in self.shards.items()
            }
        }

    def save_progress_report(self, filename: str = "progress_report.json"):
        """
        保存进度报告

        Args:
            filename: 报告文件名
        """
        report = {
            "timestamp": datetime.now().isoformat(),
            "monitoring_start": datetime.fromtimestamp(self.start_time).isoformat(),
            **self.get_global_progress()
        }

        try:
            with open(filename, 'w', encoding='utf-8') as f:
                json.dump(report, f, indent=2, ensure_ascii=False, default=str)
            print(f"💾 进度报告已保存: {filename}")
        except Exception as e:
            print(f"⚠️  保存报告失败: {e}")

    def print_progress_summary(self):
        """打印进度摘要到控制台"""
        progress = self.get_global_progress()

        print("\n" + "=" * 60)
        print("📊 并行爬取进度")
        print("=" * 60)
        print(f"⏱️  已用时间: {progress['elapsed_minutes']} 分钟")
        print(f"⏳ 剩余时间: {progress['remaining_minutes']} 分钟")
        print(f"📈 总进度: {progress['overall_progress_percent']:.1f}%")
        print(f"✅ 已完成章节: {progress['completed_chapters']}/{progress['total_chapters']}")
        print(f"🔗 已处理URL: {progress['total_urls_processed']}")
        print(f"🔄 进行中Shard: {progress['active_shards']}")
        print(f"✅ 完成Shard: {progress['completed_shards']}")
        print("\n各Shard详情:")

        for shard_id, detail in progress['shard_details'].items():
            progress_bar = self._create_progress_bar(detail['progress'])
            print(f"  {shard_id}: {progress_bar} {detail['progress']:.1f}% "
                  f"({detail['completed']}/{detail['total']}) [{detail['status']}]")

        print("=" * 60 + "\n")

    def _create_progress_bar(self, percent: float, width: int = 20) -> str:
        """
        创建进度条

        Args:
            percent: 进度百分比
            width: 进度条宽度

        Returns:
            str: 进度条字符串
        """
        filled = int(width * percent / 100)
        bar = "█" * filled + "░" * (width - filled)
        return bar

    def check_timeout_risk(self) -> Tuple[bool, str]:
        """
        检查超时风险

        Returns:
            Tuple[bool, str]: (是否有风险, 风险描述)
        """
        elapsed = time.time() - self.start_time
        remaining = self.GITHUB_ACTIONS_TIMEOUT - elapsed
        remaining_minutes = int(remaining / 60)

        if remaining_minutes < 10:
            return (True, f"🚨 严重: 仅剩 {remaining_minutes} 分钟")
        elif remaining_minutes < 20:
            return (True, f"⚠️  警告: 剩余 {remaining_minutes} 分钟")
        elif remaining_minutes < 40:
            return (True, f"ℹ️  注意: 剩余 {remaining_minutes} 分钟")
        else:
            return (False, f"✅ 时间充足: 剩余 {remaining_minutes} 分钟")


def main():
    """主函数 - 命令行接口"""
    import argparse

    parser = argparse.ArgumentParser(description="进度监控器")
    parser.add_argument(
        "--timeout-buffer",
        type=int,
        default=20,
        help="提前停止的时间缓冲（分钟）"
    )
    parser.add_argument(
        "--check",
        type=str,
        help="检查指定 shard 的进度"
    )
    parser.add_argument(
        "--report",
        type=str,
        default="progress_report.json",
        help="生成进度报告文件"
    )

    args = parser.parse_args()

    # 创建监控器
    monitor = ProgressMonitor(timeout_buffer=args.timeout_buffer)

    # 如果指定了 shard，模拟检查进度
    if args.check:
        # 这里应该读取实际的进度文件
        progress_file = f"shard_{args.check}_progress.json"
        if os.path.exists(progress_file):
            with open(progress_file, 'r') as f:
                progress_data = json.load(f)
            should_stop, reason = monitor.should_create_partial(args.check)
            print(f"Shard {args.check}: {reason}")
        else:
            print(f"⚠️  进度文件不存在: {progress_file}")

    # 打印全局进度
    monitor.print_progress_summary()


if __name__ == "__main__":
    main()
