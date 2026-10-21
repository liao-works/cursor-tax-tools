#!/usr/bin/env python3
"""
数据库合并器 - 合并多个 shard 数据库并处理去重

功能：
1. 合并多个 shard 数据库
2. 基于主键去重（commodity_code）
3. 保留最新的更新时间戳
4. 生成合并统计报告
"""

import sqlite3
import sys
import os
import json
import glob
from typing import List, Dict, Tuple
from pathlib import Path

# 添加项目根目录到 Python 路径
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)


class DatabaseMerger:
    """数据库合并器 - 合并多个分片数据库"""

    def __init__(self, main_db_path: str = "tariffs.db"):
        """
        初始化数据库合并器

        Args:
            main_db_path: 主数据库文件路径
        """
        self.main_db_path = main_db_path
        self.stats = {
            "total_shards": 0,
            "successful_shards": 0,
            "failed_shards": 0,
            "total_records": 0,
            "total_shard_records": 0,  # 新增：所有分片的记录总数
            "duplicate_records_removed": 0,
            "merge_time_seconds": 0,
            "shard_details": []
        }

    def merge_shard_databases(
        self,
        shard_paths: List[str],
        output_path: str = None
    ) -> dict:
        """
        合并多个 shard 数据库（智能合并模式）

        合并策略：
        1. 创建主数据库（如果不存在）
        2. 收集所有分片数据到内存中
        3. 对每个commodity code，智能选择最佳数据：
           - 优先保留URL不包含 /xi/commodities/ 的记录
           - 如果数据质量相同，保留先遇到的记录
        4. 批量写入主数据库
        5. 记录每个分片的统计信息

        Args:
            shard_paths: 分片数据库路径列表
            output_path: 输出数据库路径，默认为 main_db_path

        Returns:
            dict: 合并统计信息
        """
        import time
        start_time = time.time()

        if output_path is None:
            output_path = self.main_db_path

        print(f"🔧 开始合并 {len(shard_paths)} 个分片数据库...")
        print(f"📝 使用智能合并模式（优先选择正确的URL）")

        # 初始化主数据库
        self._initialize_main_database(output_path)

        # 第一步：收集所有分片数据
        print(f"\n📦 第1步：收集所有分片数据...")
        all_records = {}  # {code: (record_dict, shard_path, quality_score)}
        total_shard_records = 0
        all_column_names = set()  # 收集所有列名

        for i, shard_path in enumerate(shard_paths):
            if not os.path.exists(shard_path):
                print(f"⚠️  分片不存在: {shard_path}")
                self.stats["failed_shards"] += 1
                continue

            try:
                # 独立连接读取分片数据
                shard_conn = sqlite3.connect(
                    f"file:{shard_path}?mode=ro",
                    uri=True,
                    timeout=30.0
                )
                shard_cursor = shard_conn.cursor()

                # 获取分片的列结构
                shard_cursor.execute("PRAGMA table_info(tariffs)")
                shard_columns = [row[1] for row in shard_cursor.fetchall()]
                column_indices = {name: idx for idx, name in enumerate(shard_columns)}

                # 收集所有列名（用于构建最完整的列结构）
                all_column_names.update(shard_columns)
                print(f"   📋 分片 {i+1} 列结构 ({len(shard_columns)} 列): {shard_columns}")

                # 获取分片记录数
                shard_count = shard_cursor.execute(
                    "SELECT COUNT(*) FROM tariffs"
                ).fetchone()[0]
                total_shard_records += shard_count

                # 读取所有记录
                columns_str = ", ".join(shard_columns)
                shard_cursor.execute(f"SELECT {columns_str} FROM tariffs")
                records = shard_cursor.fetchall()

                # 分析每条记录的质量并保存（转换为字典格式）
                for record in records:
                    code = record[0]  # code是第一列
                    url = record[column_indices.get('url', 3)] if 'url' in column_indices else ""

                    # 将记录转换为字典，方便后续统一列结构
                    record_dict = {col: record[idx] for idx, col in enumerate(shard_columns)}

                    # 计算数据质量分数
                    quality_score = self._calculate_record_quality(record, column_indices)

                    # 智能选择：如果该code已存在，比较质量
                    if code not in all_records:
                        all_records[code] = (record_dict, shard_path, quality_score)
                    else:
                        # 如果新记录质量更高，替换
                        existing_record, existing_shard, existing_score = all_records[code]
                        if quality_score > existing_score:
                            all_records[code] = (record_dict, shard_path, quality_score)

                shard_conn.close()
                self.stats["successful_shards"] += 1
                print(f"   ✅ 分片 {i+1}: {shard_count} 条记录")

            except Exception as e:
                print(f"   ❌ 分片 {i+1} 读取失败: {e}")
                self.stats["failed_shards"] += 1
                continue

        # 构建统一的列顺序（基于主数据库的预期列顺序）
        canonical_columns = [
            'code', 'description', 'rate', 'url',
            'north_ireland_rate', 'north_ireland_url',
            'other_rate', 'anti_dumping_rate', 'countervailing_rate', 'last_updated'
        ]
        # 只保留实际存在的列
        shard_columns_list = [col for col in canonical_columns if col in all_column_names]
        # 添加任何不在预期列表中的列
        for col in all_column_names:
            if col not in shard_columns_list:
                shard_columns_list.append(col)

        print(f"\n   📊 收集完成：{len(all_records)} 条唯一记录（总分片记录：{total_shard_records}）")
        print(f"   📊 统一列结构 ({len(shard_columns_list)} 列): {shard_columns_list}")

        # 第二步：批量写入主数据库
        print(f"\n💾 第2步：批量写入主数据库...")

        main_conn = sqlite3.connect(output_path, timeout=60.0)
        main_conn.execute("PRAGMA journal_mode=WAL")
        main_cursor = main_conn.cursor()

        # 准备插入数据
        records_to_insert = list(all_records.values())

        # 动态构建 INSERT 语句
        if records_to_insert and shard_columns_list:
            num_columns = len(shard_columns_list)
            placeholders = ", ".join(["?"] * num_columns)

            # 使用统一的列名构建 INSERT 语句
            columns_str = ", ".join(shard_columns_list)

            # 检查主数据库是否有所有需要的列
            main_cursor.execute("PRAGMA table_info(tariffs)")
            main_columns = [row[1] for row in main_cursor.fetchall()]

            print(f"   📋 主数据库列结构 ({len(main_columns)} 列): {main_columns}")
            print(f"   📋 统一列结构 ({len(shard_columns_list)} 列): {shard_columns_list}")

            # 检查主数据库是否缺少某些列，如果缺少则添加
            missing_columns = set(shard_columns_list) - set(main_columns)
            if missing_columns:
                print(f"   ⚠️  主数据库缺少列: {missing_columns}，正在添加...")
                for col in missing_columns:
                    try:
                        main_cursor.execute(f"ALTER TABLE tariffs ADD COLUMN {col} TEXT")
                        print(f"      ✅ 已添加列: {col}")
                    except Exception as e:
                        print(f"      ⚠️  添加列 {col} 失败: {e}")

            insert_sql = f"""
                INSERT OR REPLACE INTO tariffs ({columns_str})
                VALUES ({placeholders})
            """

            print(f"   📝 INSERT 语句列数: {num_columns}, 值占位符数: {num_columns}")

            # 将字典格式的记录转换为统一列顺序的元组
            normalized_records = []
            for record_dict, shard_path, quality_score in records_to_insert:
                # 按统一列顺序构建元组，缺失的列填充 None
                row = tuple(record_dict.get(col, None) for col in shard_columns_list)
                normalized_records.append(row)

            main_cursor.executemany(insert_sql, normalized_records)
            main_conn.commit()
            print(f"   ✅ 成功写入 {len(normalized_records)} 条记录")

        main_conn.close()

        # 收集统计信息
        self.stats["total_shards"] = len(shard_paths)
        self.stats["total_records"] = len(all_records)
        self.stats["total_shard_records"] = total_shard_records
        self.stats["duplicate_records_removed"] = total_shard_records - len(all_records)
        self.stats["merge_time_seconds"] = time.time() - start_time

        print(f"\n✅ 合并完成!")
        print(f"   总分片数: {self.stats['total_shards']}")
        print(f"   成功合并: {self.stats['successful_shards']}")
        print(f"   失败跳过: {self.stats['failed_shards']}")
        print(f"   总记录数: {self.stats['total_records']:,}")
        print(f"   分片记录总计: {self.stats['total_shard_records']:,}")
        print(f"   智能去重: {self.stats['duplicate_records_removed']:,} 条重复记录已处理")
        print(f"   耗时: {self.stats['merge_time_seconds']:.2f} 秒")

        return self.stats

    def _calculate_record_quality(self, record: tuple, column_indices: dict) -> int:
        """
        计算记录质量分数（用于智能合并）

        评分规则：
        - URL包含 /xi/commodities/: -50分（错误URL）
        - 有描述: +10分
        - 有税率: +10分
        - 有北爱尔兰税率: +5分
        - 有北爱尔兰URL: +5分

        Args:
            record: 数据库记录
            column_indices: 列名到索引的映射

        Returns:
            int: 质量分数（越高越好）
        """
        score = 0

        # 检查URL（最重要的指标）
        url = record[column_indices.get('url', 3)] if 'url' in column_indices else ""
        if url:
            if '/xi/commodities/' in url:
                score -= 50  # 严重惩罚：错误的URL
            else:
                score += 20  # 正确的英国URL

        # 检查数据完整性
        description = record[column_indices.get('description', 1)] if 'description' in column_indices else ""
        rate = record[column_indices.get('rate', 2)] if 'rate' in column_indices else ""
        north_ireland_rate = record[column_indices.get('north_ireland_rate', 4)] if 'north_ireland_rate' in column_indices else ""
        north_ireland_url = record[column_indices.get('north_ireland_url', 5)] if 'north_ireland_url' in column_indices else ""

        if description:
            score += 10
        if rate:
            score += 10
        if north_ireland_rate:
            score += 5
        if north_ireland_url:
            score += 5

        return score

    def _initialize_main_database(self, db_path: str):
        """
        初始化主数据库（创建表结构）

        Args:
            db_path: 数据库路径
        """
        # 如果数据库已存在，检查表结构
        if os.path.exists(db_path):
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            cursor.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='tariffs'"
            )
            if cursor.fetchone():
                conn.close()
                return

        # 创建新数据库
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # 创建 tariffs 表（包含所有字段）
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS tariffs (
                code TEXT PRIMARY KEY,
                description TEXT,
                rate TEXT,
                url TEXT,
                north_ireland_rate TEXT,
                north_ireland_url TEXT,
                other_rate TEXT,
                anti_dumping_rate TEXT,
                countervailing_rate TEXT,
                last_updated DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # 创建索引
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_description
            ON tariffs(description)
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_rate
            ON tariffs(rate)
        """)

        conn.commit()
        conn.close()

        print(f"✅ 主数据库已初始化: {db_path}")

    def discover_shard_databases(
        self,
        input_dir: str = "artifacts",
        pattern: str = "tariffs_shard_*.db",
        recursive: bool = True
    ) -> List[str]:
        """
        自动发现分片数据库文件

        Args:
            input_dir: 搜索目录
            pattern: 文件匹配模式
            recursive: 是否递归搜索子目录

        Returns:
            List[str]: 分片数据库路径列表（按 shard 编号排序）
        """
        shard_files = []

        # DEBUG: 打印输入目录的内容
        print(f"🔍 搜索目录: {input_dir}")
        print(f"🔍 搜索模式: {pattern}")
        print(f"🔍 递归搜索: {recursive}")

        if os.path.exists(input_dir):
            print(f"📂 目录内容:")
            for root, dirs, files in os.walk(input_dir):
                level = root.replace(input_dir, '').count(os.sep)
                indent = ' ' * 2 * level
                print(f'{indent}{os.path.basename(root)}/')
                subindent = ' ' * 2 * (level + 1)
                for file in files:
                    print(f'{subindent}{file}')
        else:
            print(f"❌ 目录不存在: {input_dir}")

        if recursive:
            # 递归搜索所有子目录
            for root, dirs, files in os.walk(input_dir):
                # 使用 fnmatch 过滤文件名
                import fnmatch
                matched_files = [
                    os.path.join(root, f)
                    for f in files
                    if fnmatch.fnmatch(f, pattern)
                ]
                shard_files.extend(matched_files)
        else:
            # 只搜索顶层目录
            search_pattern = os.path.join(input_dir, pattern)
            shard_files = glob.glob(search_pattern)

        # 按 shard 编号排序
        shard_files.sort(key=lambda x: self._extract_shard_number(x))

        print(f"🔍 发现 {len(shard_files)} 个分片数据库:")
        for shard_file in shard_files:
            print(f"   - {shard_file}")

        return shard_files

    @staticmethod
    def _extract_shard_number(filename: str) -> int:
        """
        从文件名提取 shard 编号

        Args:
            filename: 文件名

        Returns:
            int: Shard 编号
        """
        try:
            # 从 "tariffs_shard_0.db" 提取 0
            basename = os.path.basename(filename)
            number = basename.replace("tariffs_shard_", "").replace(".db", "")
            return int(number)
        except (ValueError, IndexError):
            return 9999  # 未知文件排在最后

    def validate_merged_database(self, db_path: str = None) -> dict:
        """
        验证合并后的数据库

        Args:
            db_path: 数据库路径，默认为 main_db_path

        Returns:
            dict: 验证结果
        """
        if db_path is None:
            db_path = self.main_db_path

        if not os.path.exists(db_path):
            return {
                "valid": False,
                "error": "数据库文件不存在"
            }

        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        try:
            # 基本统计
            total_records = cursor.execute(
                "SELECT COUNT(*) FROM tariffs"
            ).fetchone()[0]

            records_with_description = cursor.execute(
                "SELECT COUNT(*) FROM tariffs WHERE description IS NOT NULL"
            ).fetchone()[0]

            records_with_rate = cursor.execute(
                "SELECT COUNT(*) FROM tariffs WHERE rate IS NOT NULL"
            ).fetchone()[0]

            # 检查重复
            duplicates = cursor.execute("""
                SELECT COUNT(*) - COUNT(DISTINCT code)
                FROM tariffs
            """).fetchone()[0]

            # 数据完整性
            completeness = {
                "description": (records_with_description / total_records * 100)
                               if total_records > 0 else 0,
                "rate": (records_with_rate / total_records * 100)
                        if total_records > 0 else 0,
            }

            result = {
                "valid": True,
                "total_records": total_records,
                "records_with_description": records_with_description,
                "records_with_rate": records_with_rate,
                "duplicates": duplicates,
                "completeness": completeness
            }

            conn.close()
            return result

        except Exception as e:
            conn.close()
            return {
                "valid": False,
                "error": str(e)
            }

    def save_merge_results(self, output_file: str = "merge_results.json"):
        """
        保存合并结果到 JSON 文件

        Args:
            output_file: 输出文件路径
        """
        try:
            with open(output_file, 'w', encoding='utf-8') as f:
                json.dump(self.stats, f, indent=2, ensure_ascii=False, default=str)
            print(f"💾 合并结果已保存: {output_file}")
        except Exception as e:
            print(f"⚠️  保存合并结果失败: {e}")


def main():
    """主函数 - 命令行接口"""
    import argparse

    parser = argparse.ArgumentParser(description="数据库合并器")
    parser.add_argument(
        "--input-dir",
        type=str,
        default="artifacts",
        help="分片数据库目录"
    )
    parser.add_argument(
        "--output",
        type=str,
        default="tariffs.db",
        help="输出数据库路径"
    )
    parser.add_argument(
        "--shards",
        type=str,
        nargs="+",
        help="手动指定分片数据库路径"
    )
    parser.add_argument(
        "--validate",
        action="store_true",
        help="合并后验证数据库"
    )
    parser.add_argument(
        "--allow-partial",
        action="store_true",
        help="允许部分失败（部分分片合并失败不影响整体）"
    )

    args = parser.parse_args()

    # 创建合并器
    merger = DatabaseMerger(main_db_path=args.output)

    # 发现或使用指定的分片
    if args.shards:
        shard_paths = args.shards
    else:
        shard_paths = merger.discover_shard_databases(args.input_dir)

    if not shard_paths:
        print("❌ 未找到分片数据库")
        sys.exit(1)

    # 合并数据库
    try:
        stats = merger.merge_shard_databases(shard_paths, args.output)
        merger.save_merge_results()

        # 验证合并结果
        if args.validate:
            print("\n🔍 验证合并结果...")
            validation = merger.validate_merged_database(args.output)

            if validation["valid"]:
                print("✅ 验证通过")
                print(f"   总记录数: {validation['total_records']:,}")
                print(f"   描述完整性: {validation['completeness']['description']:.1f}%")
                print(f"   税率完整性: {validation['completeness']['rate']:.1f}%")
                print(f"   重复记录: {validation['duplicates']}")
            else:
                print(f"❌ 验证失败: {validation['error']}")
                if not args.allow_partial:
                    sys.exit(1)

    except Exception as e:
        print(f"❌ 合并失败: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
