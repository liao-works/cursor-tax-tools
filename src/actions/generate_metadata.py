#!/usr/bin/env python3
"""
GitHub Actions脚本: 生成数据库元数据文件
用于智能更新客户端判断是否需要下载数据库
"""

import hashlib
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from typing import Dict, Optional


def calculate_file_hash(file_path: str, algorithm: str = 'sha256') -> str:
    """计算文件哈希值"""
    hash_func = getattr(hashlib, algorithm)()

    try:
        with open(file_path, 'rb') as f:
            while chunk := f.read(8192):
                hash_func.update(chunk)
        return f"{algorithm}:{hash_func.hexdigest()}"
    except FileNotFoundError:
        return f"{algorithm}:unknown"
    except Exception as e:
        print(f"计算哈希失败: {e}", file=sys.stderr)
        return f"{algorithm}:error"


def write_md5_sidecar(file_path: str) -> Optional[str]:
    """生成 md5sum 兼容的 .md5 校验文件（内容: "<hex>  <文件名>"）。

    除随 Release 发布供人工/脚本 `md5sum -c` 校验外，摘要同时写入
    metadata（file_md5 / compressed.md5）供客户端做第二重完整性校验。
    失败只告警，不阻断发布。
    """
    md5_path = file_path + '.md5'
    try:
        hash_md5 = hashlib.md5()
        with open(file_path, 'rb') as f:
            while chunk := f.read(1024 * 1024):
                hash_md5.update(chunk)
        digest = hash_md5.hexdigest()
        with open(md5_path, 'w', encoding='utf-8') as f:
            f.write(f"{digest}  {os.path.basename(file_path)}\n")
        return digest
    except Exception as e:
        print(f"生成 MD5 校验文件失败（不影响发布）: {e}", file=sys.stderr)
        if os.path.exists(md5_path):
            try:
                os.remove(md5_path)
            except OSError:
                pass
        return None


def compress_database(db_path: str) -> Optional[Dict]:
    """生成 zstd 压缩快照（<db_path>.zst），返回客户端 compressed 元数据块。

    客户端（liao-tools）在清单提供 compressed 块时优先下载压缩资产，
    下载量约为原始快照的 1/5；块缺失时客户端按旧语义下载原始快照。
    zstandard 不可用或压缩失败时返回 None，不影响本次发布。
    """
    zst_path = db_path + '.zst'
    try:
        import zstandard
    except ImportError:
        print("WARN: 未安装 zstandard，跳过压缩快照发布", file=sys.stderr)
        return None

    try:
        # level 15：400MB 级数据库在 CI 上约 1 分钟，压缩比远高于默认级别
        cctx = zstandard.ZstdCompressor(level=15)
        with open(db_path, 'rb') as src, open(zst_path, 'wb') as dst:
            cctx.copy_stream(src, dst)

        stat_info = os.stat(zst_path)
        block = {
            'format': 'zstd',
            'asset': os.path.basename(zst_path),
            'file_size': stat_info.st_size,
            'file_hash': calculate_file_hash(zst_path),
        }
        md5_digest = write_md5_sidecar(zst_path)
        if md5_digest:
            block['md5'] = f"md5:{md5_digest}"
        ratio = stat_info.st_size / max(os.path.getsize(db_path), 1)
        print(f"SUCCESS: 压缩快照已生成: {zst_path}（压缩比 {ratio:.2f}）")
        return block
    except Exception as e:
        print(f"压缩快照生成失败（不影响发布）: {e}", file=sys.stderr)
        if os.path.exists(zst_path):
            try:
                os.remove(zst_path)
            except OSError:
                pass
        return None


def get_database_stats(db_path: str) -> Dict:
    """获取数据库统计信息"""
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        stats = {
            'record_count': cursor.execute("SELECT COUNT(*) FROM tariffs").fetchone()[0],
            'records_with_description': 0,
            'records_with_uk_rate': 0,
            'records_with_ni_rate': 0,
            'common_rates': [],
            'categories_count': 0,
            'active_errors': 0
        }

        # 质量指标
        stats['records_with_description'] = cursor.execute(
            "SELECT COUNT(*) FROM tariffs WHERE description IS NOT NULL AND description != ''"
        ).fetchone()[0]

        stats['records_with_uk_rate'] = cursor.execute(
            "SELECT COUNT(*) FROM tariffs WHERE rate IS NOT NULL AND rate != ''"
        ).fetchone()[0]

        stats['records_with_ni_rate'] = cursor.execute(
            "SELECT COUNT(*) FROM tariffs WHERE north_ireland_rate IS NOT NULL AND north_ireland_rate != ''"
        ).fetchone()[0]

        # 常见税率（增加到10条，与根目录版本一致）
        rate_dist = cursor.execute("""
            SELECT rate, COUNT(*) as count
            FROM tariffs
            WHERE rate IS NOT NULL AND rate != ''
            GROUP BY rate
            ORDER BY count DESC
            LIMIT 10
        """).fetchall()

        stats['common_rates'] = [rate for rate, count in rate_dist]

        # ✅ 动态计算税率范围（从根目录版本合并）
        if rate_dist:
            numeric_rates = []
            for rate, count in rate_dist:
                try:
                    # 提取数字部分
                    rate_value = float(rate.replace('%', ''))
                    numeric_rates.append(rate_value)
                except ValueError:
                    continue

            if numeric_rates:
                stats['uk_rate_min'] = f"{min(numeric_rates)}%"
                stats['uk_rate_max'] = f"{max(numeric_rates)}%"

        # 分类统计
        try:
            stats['categories_count'] = cursor.execute("""
                SELECT COUNT(DISTINCT SUBSTR(code, 1, 2)) FROM tariffs
            """).fetchone()[0]
        except:
            pass

        # ✅ 更新历史统计（从根目录版本合并）
        try:
            recent_updates = cursor.execute("""
                SELECT COUNT(*) FROM update_history
                WHERE timestamp > datetime('now', '-1 day')
            """).fetchone()[0]
            stats['recent_updates_24h'] = recent_updates
        except:
            stats['recent_updates_24h'] = 0

        # 错误统计
        try:
            stats['active_errors'] = cursor.execute("SELECT COUNT(*) FROM scrape_errors").fetchone()[0]
        except:
            pass

        conn.close()
        return stats

    except Exception as e:
        print(f"获取数据库统计失败: {e}", file=sys.stderr)
        return {}


def load_update_results(results_path: str) -> Dict:
    """加载更新结果"""
    try:
        if os.path.exists(results_path):
            with open(results_path, 'r', encoding='utf-8') as f:
                return json.load(f)
    except Exception as e:
        print(f"加载更新结果失败: {e}", file=sys.stderr)

    return {}


def analyze_coverage(merge_results: Dict, task_file: str = None) -> Dict:
    """分析数据覆盖率和缺失信息"""
    coverage_info = {
        'total_chapters': 98,
        'completed_chapters': 0,
        'missing_chapters': [],
        'failed_chapters': {},
        'coverage_percentage': 0.0
    }

    # 从合并结果提取成功/失败信息
    if merge_results:
        shard_details = merge_results.get('shard_details', [])

        # 统计完成的章节
        completed_set = set()
        failed_dict = {}

        for detail in shard_details:
            shard_path = detail.get('shard_path', '')
            shard_records = detail.get('shard_records', 0)
            error = detail.get('error')

            # 如果有记录，认为该 shard 成功
            if shard_records > 0:
                # 从任务文件中读取该 shard 的章节
                # 这里简化处理，后续可以从 task_file 读取
                pass
            elif error:
                # 记录失败原因
                failed_dict[shard_path] = {
                    'reason': error,
                    'retry_count': detail.get('retries', 0)
                }

        # 如果有任务文件，精确计算覆盖率
        if task_file and os.path.exists(task_file):
            try:
                with open(task_file, 'r') as f:
                    tasks = json.load(f)

                all_chapters = set()
                for shard_id, chapters in tasks.items():
                    all_chapters.update(chapters)

                coverage_info['total_chapters'] = len(all_chapters)
                # TODO: 从数据库中查询实际存在的章节
            except Exception as e:
                print(f"读取任务文件失败: {e}", file=sys.stderr)

    # 计算覆盖率
    if coverage_info['total_chapters'] > 0:
        coverage_info['coverage_percentage'] = round(
            (coverage_info['total_chapters'] - len(coverage_info['missing_chapters']))
            / coverage_info['total_chapters'] * 100, 2
        )

    return coverage_info

def collect_failed_codes(search_dirs=None):
    """从各 shard_results JSON 汇总 failed_codes（run_shard 末尾重试后仍失败的 code）。

    failed_codes 由 run_shard.retry_network_errors 产出（400/500 重试 3 次仍失败），
    写入 metadata 供客户端更新后补全。

    Returns:
        list[dict]: 每个 {code, status, error_type, last_attempt, retries}，按 code 去重
    """
    import glob
    if search_dirs is None:
        search_dirs = ['.', 'artifacts']
    failed = []
    seen = set()
    for d in search_dirs:
        for path in glob.glob(f"{d}/**/shard_*_results.json", recursive=True):
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                for fc in data.get('failed_codes', []):
                    code = fc.get('code')
                    if code and code not in seen:
                        seen.add(code)
                        failed.append(fc)
            except Exception:
                pass
    return failed


def generate_metadata(db_path: str = 'tariffs.db',
                     version: str = None,
                     results_path: str = 'update_results.json',
                     output_path: str = 'metadata.json',
                     task_file: str = None) -> Dict:
    """生成数据库元数据"""

    # 检查数据库文件是否存在
    if not os.path.exists(db_path):
        print(f"错误: 数据库文件不存在 {db_path}", file=sys.stderr)
        return {}

    print(f"生成元数据: {db_path}")

    # 文件基本信息
    stat_info = os.stat(db_path)
    file_info = {
        'file_size': stat_info.st_size,
        'file_hash': calculate_file_hash(db_path),
        'last_modified': datetime.fromtimestamp(stat_info.st_mtime, tz=timezone.utc).isoformat(),
    }
    # 原始库的 .md5 校验文件与摘要（客户端第二重校验）
    file_md5 = write_md5_sidecar(db_path)

    # 数据库统计
    db_stats = get_database_stats(db_path)

    # 压缩快照（客户端可选下载，压缩比决定弱网收益）
    compressed_block = compress_database(db_path)

    # 更新结果（merge_results.json）
    update_results = load_update_results(results_path)

    # 分析覆盖率
    coverage_info = analyze_coverage(update_results, task_file)

    # 时间戳
    timestamp = datetime.now(timezone.utc).isoformat()

    # 构建元数据
    metadata = {
        'version': version or f"data-{int(datetime.now().timestamp())}",
        'timestamp': timestamp,
        'last_modified': file_info['last_modified'],
        'file_size': file_info['file_size'],
        'file_hash': file_info['file_hash'],
        'record_count': db_stats.get('record_count', 0),

        'coverage': {
            'total_chapters': coverage_info['total_chapters'],
            'completed_chapters': coverage_info['total_chapters'] - len(coverage_info['missing_chapters']),
            'missing_chapters': coverage_info['missing_chapters'],
            'coverage_percentage': coverage_info['coverage_percentage']
        },

        'missing_details': coverage_info.get('failed_chapters', {}),

        'changes_summary': {
            'total_updates': update_results.get('successful_shards', 0),
            'total_shards': update_results.get('total_shards', 0),
            'failed_shards': update_results.get('failed_shards', 0),
            'total_shard_records': update_results.get('total_shard_records', 0),
            'duplicate_records_removed': update_results.get('duplicate_records_removed', 0)
        },

        'update_statistics': {
            'success_rate': update_results.get('successful', 0) / max(update_results.get('total', 1), 1),
            'processing_time_minutes': update_results.get('processing_time_minutes', 0),
            'errors_count': update_results.get('failed', 0),
            'retry_count': update_results.get('retry_count', 0)
        },

        'quality_metrics': {
            'records_with_description': db_stats.get('records_with_description', 0),
            'records_with_uk_rate': db_stats.get('records_with_uk_rate', 0),
            'records_with_ni_rate': db_stats.get('records_with_ni_rate', 0),
            'completeness_score': db_stats.get('records_with_description', 0) / max(db_stats.get('record_count', 1), 1)
        },

        'data_ranges': {
            'uk_rate_min': '0%',
            'uk_rate_max': '25%',
            'common_rates': db_stats.get('common_rates', []),
            'categories_count': db_stats.get('categories_count', 0)
        },

        'client_instructions': {
            'check_interval_hours': 24,
            'auto_download_enabled': True,
            'backup_before_update': True,
            'verify_checksum': True,
            'min_change_threshold': 10
        },

        'download_urls': {
            'primary': f"https://github.com/{os.getenv('GITHUB_REPOSITORY', 'owner/repo')}/releases/download/latest-data/tariffs.db",
            'metadata': f"https://github.com/{os.getenv('GITHUB_REPOSITORY', 'owner/repo')}/releases/download/latest-data/metadata.json",
            'mirror': []
        },

        # 网络错误（400/500）重试后仍失败的 code，供客户端更新后补全
        # 由 merge job 汇总到 merge_results.json，随 artifact 传递（不依赖本 job 的 shard_results）
        'failed_codes': (update_results or {}).get('failed_codes', [])
    }

    # 压缩快照块：file_size/file_hash 语义不变（始终指解压后的原始库），
    # compressed 描述额外的 zstd 资产；None 时字段缺席 = 客户端走旧链路
    if compressed_block:
        metadata['compressed'] = compressed_block
    if file_md5:
        metadata['file_md5'] = f"md5:{file_md5}"

    # 保存元数据
    try:
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(metadata, f, indent=2, ensure_ascii=False)

        print(f"SUCCESS: 元数据生成完成: {output_path}")
        print(f"INFO: 版本: {metadata['version']}")
        print(f"INFO: 记录数: {metadata['record_count']:,}")
        print(f"INFO: 大小: {metadata['file_size'] / 1024 / 1024:.1f}MB")
        if metadata.get('compressed'):
            print(f"INFO: 压缩快照: {metadata['compressed']['asset']} "
                  f"{metadata['compressed']['file_size'] / 1024 / 1024:.1f}MB")
        print(f"INFO: 哈希: {metadata['file_hash'][:32]}...")

        return metadata

    except Exception as e:
        print(f"保存元数据失败: {e}", file=sys.stderr)
        return {}


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="生成数据库元数据")
    parser.add_argument('db_path', nargs='?', default='tariffs.db', help='数据库路径')
    parser.add_argument('version', nargs='?', help='版本号')
    parser.add_argument('results_path', nargs='?', default='merge_results.json', help='合并结果路径')
    parser.add_argument('output_path', nargs='?', default='metadata.json', help='输出路径')
    parser.add_argument('--task-file', type=str, help='任务分配文件路径')

    args = parser.parse_args()

    version = args.version or os.getenv('VERSION', f"data-{int(datetime.now().timestamp())}")

    # 生成元数据
    metadata = generate_metadata(
        db_path=args.db_path,
        version=version,
        results_path=args.results_path,
        output_path=args.output_path,
        task_file=args.task_file
    )

    if not metadata:
        sys.exit(1)

    # 输出环境变量供GitHub Actions使用
    print(f"::set-output name=version::{metadata['version']}")
    print(f"::set-output name=file_size::{metadata['file_size']}")
    print(f"::set-output name=record_count::{metadata['record_count']}")
    print(f"::set-output name=file_hash::{metadata['file_hash'][:32]}")

    # 计算变更数量
    total_changes = metadata['changes_summary']['total_updates']
    print(f"::set-output name=total_changes::{total_changes}")