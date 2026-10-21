#!/usr/bin/env python3
"""
缺失章节任务生成器 - 根据元数据中的缺失信息生成补齐任务

功能：
1. 读取上次的 metadata.json
2. 提取 missing_chapters 列表
3. 根据缺失章节生成任务分配
4. 支持动态 shard 数量
"""

import json
import sys
import os
from typing import Dict, List, Optional
from pathlib import Path

# 添加项目根目录到 Python 路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))


class MissingTasksGenerator:
    """缺失章节任务生成器"""

    def __init__(self, metadata_path: str, shard_count: int = 15):
        """
        初始化生成器

        Args:
            metadata_path: metadata.json 路径
            shard_count: 分片数量，默认 10
        """
        self.metadata_path = metadata_path
        self.shard_count = shard_count
        self.metadata = self._load_metadata()

    def _load_metadata(self) -> Optional[Dict]:
        """加载元数据"""
        if not os.path.exists(self.metadata_path):
            print(f"⚠️  元数据文件不存在: {self.metadata_path}")
            return None

        try:
            with open(self.metadata_path, 'r', encoding='utf-8') as f:
                metadata = json.load(f)
            print(f"✅ 已加载元数据: {self.metadata_path}")
            return metadata
        except Exception as e:
            print(f"❌ 加载元数据失败: {e}")
            return None

    def get_missing_chapters(self) -> List[str]:
        """
        获取缺失章节列表

        Returns:
            List[str]: 缺失章节列表
        """
        if not self.metadata:
            return []

        coverage = self.metadata.get('coverage', {})
        missing_chapters = coverage.get('missing_chapters', [])

        # 如果 metadata 没有 coverage 信息，尝试从 missing_details 提取
        if not missing_chapters:
            missing_details = self.metadata.get('missing_details', {})
            missing_chapters = list(missing_details.keys())

        print(f"📋 发现 {len(missing_chapters)} 个缺失章节")
        return missing_chapters

    def generate_tasks(self, missing_chapters: List[str]) -> Dict[str, List[str]]:
        """
        根据缺失章节生成任务分配

        Args:
            missing_chapters: 缺失章节列表

        Returns:
            Dict[str, List[str]]: shard_id -> 章节列表
        """
        if not missing_chapters:
            print("⚠️  没有缺失章节，无需生成任务")
            return {}

        # 均匀分配到各个 shard
        tasks = {f"shard_{i}": [] for i in range(self.shard_count)}

        for idx, chapter in enumerate(sorted(missing_chapters)):
            shard_idx = idx % self.shard_count
            tasks[f"shard_{shard_idx}"].append(chapter)

        # 移除空 shard
        tasks = {k: v for k, v in tasks.items() if v}

        print(f"✅ 已生成 {len(tasks)} 个 shard 的任务分配")
        return tasks

    def save_tasks(self, tasks: Dict[str, List[str]], output_path: str):
        """
        保存任务分配到文件

        Args:
            tasks: 任务分配
            output_path: 输出文件路径
        """
        try:
            with open(output_path, 'w', encoding='utf-8') as f:
                json.dump(tasks, f, indent=2, ensure_ascii=False)
            print(f"💾 任务分配已保存: {output_path}")
        except Exception as e:
            print(f"❌ 保存任务分配失败: {e}")
            sys.exit(1)

    def print_summary(self, tasks: Dict[str, List[str]]):
        """
        打印任务摘要

        Args:
            tasks: 任务分配
        """
        total_chapters = sum(len(chapters) for chapters in tasks.values())

        print(f"\n{'='*60}")
        print(f"📊 补齐任务摘要")
        print(f"{'='*60}")
        print(f"总 Shard 数: {len(tasks)}")
        print(f"总章节数: {total_chapters}")
        print()

        for shard_id, chapters in sorted(tasks.items()):
            chapter_preview = ', '.join(chapters[:5])
            if len(chapters) > 5:
                chapter_preview += f" ... (+{len(chapters) - 5} more)"
            print(f"  {shard_id}: {len(chapters)} 章节 [{chapter_preview}]")

        print(f"{'='*60}\n")


def main():
    """主函数 - 命令行接口"""
    import argparse

    parser = argparse.ArgumentParser(description="缺失章节任务生成器")
    parser.add_argument(
        "--metadata",
        type=str,
        required=True,
        help="metadata.json 路径"
    )
    parser.add_argument(
        "--output",
        type=str,
        default="task_assignment.json",
        help="输出任务分配文件路径"
    )
    parser.add_argument(
        "--shard-count",
        type=int,
        default=15,
        help="并行分片数量，默认 15"
    )
    parser.add_argument(
        "--summary",
        action="store_true",
        help="显示任务摘要"
    )

    args = parser.parse_args()

    # 创建生成器
    generator = MissingTasksGenerator(
        metadata_path=args.metadata,
        shard_count=args.shard_count
    )

    # 获取缺失章节
    missing_chapters = generator.get_missing_chapters()

    if not missing_chapters:
        print("✅ 没有缺失章节，无需补齐")
        # 生成空任务文件
        generator.save_tasks({}, args.output)
        sys.exit(0)

    # 生成任务分配
    tasks = generator.generate_tasks(missing_chapters)

    # 保存任务
    generator.save_tasks(tasks, args.output)

    # 显示摘要
    if args.summary or True:  # 总是显示摘要
        generator.print_summary(tasks)


if __name__ == "__main__":
    main()

