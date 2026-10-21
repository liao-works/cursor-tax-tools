#!/usr/bin/env python3
"""
性能监控脚本
监控系统资源使用情况和爬虫性能指标
"""

import json
import os
import psutil
import sys
import time
from datetime import datetime, timezone
from typing import Dict, Optional


class PerformanceMonitor:
    """性能监控器"""

    def __init__(self):
        self.start_time = time.time()
        self.cpu_samples = []
        self.memory_samples = []
        self.disk_samples = []
        self.network_stats = {}
        self.sample_interval = 5  # 5秒采样间隔

    def collect_system_metrics(self) -> Dict:
        """收集系统指标"""
        try:
            # CPU信息
            cpu_percent = psutil.cpu_percent(interval=1)
            cpu_count = psutil.cpu_count()
            cpu_freq = psutil.cpu_freq()

            # 内存信息
            memory = psutil.virtual_memory()

            # 磁盘信息
            disk = psutil.disk_usage('/')
            disk_io = psutil.disk_io_counters()

            # 网络信息
            network = psutil.net_io_counters()

            # 进程信息
            process = psutil.Process()
            process_memory = process.memory_info()
            process_cpu = process.cpu_percent()

            metrics = {
                'timestamp': datetime.now(timezone.utc).isoformat(),
                'cpu': {
                    'percent': cpu_percent,
                    'count': cpu_count,
                    'freq_current': cpu_freq.current if cpu_freq else None,
                    'freq_min': cpu_freq.min if cpu_freq else None,
                    'freq_max': cpu_freq.max if cpu_freq else None
                },
                'memory': {
                    'total': memory.total,
                    'available': memory.available,
                    'used': memory.used,
                    'percent': memory.percent
                },
                'disk': {
                    'total': disk.total,
                    'used': disk.used,
                    'free': disk.free,
                    'percent': (disk.used / disk.total) * 100
                },
                'network': {
                    'bytes_sent': network.bytes_sent,
                    'bytes_recv': network.bytes_recv,
                    'packets_sent': network.packets_sent,
                    'packets_recv': network.packets_recv
                },
                'process': {
                    'pid': process.pid,
                    'memory_rss': process_memory.rss,
                    'memory_vms': process_memory.vms,
                    'memory_percent': process.memory_percent(),
                    'cpu_percent': process_cpu,
                    'num_threads': process.num_threads(),
                    'create_time': process.create_time()
                }
            }

            # 保存采样数据
            self.cpu_samples.append(cpu_percent)
            self.memory_samples.append(memory.percent)

            if disk_io:
                self.disk_samples.append({
                    'read_bytes': disk_io.read_bytes,
                    'write_bytes': disk_io.write_bytes,
                    'read_count': disk_io.read_count,
                    'write_count': disk_io.write_count
                })

            return metrics

        except Exception as e:
            print(f"❌ 收集系统指标失败: {e}", file=sys.stderr)
            return {}

    def get_summary_stats(self) -> Dict:
        """获取汇总统计"""
        elapsed_time = time.time() - self.start_time

        summary = {
            'monitoring_duration_seconds': elapsed_time,
            'sample_count': len(self.cpu_samples),
            'cpu': {
                'average': sum(self.cpu_samples) / len(self.cpu_samples) if self.cpu_samples else 0,
                'max': max(self.cpu_samples) if self.cpu_samples else 0,
                'min': min(self.cpu_samples) if self.cpu_samples else 0
            },
            'memory': {
                'average': sum(self.memory_samples) / len(self.memory_samples) if self.memory_samples else 0,
                'max': max(self.memory_samples) if self.memory_samples else 0,
                'min': min(self.memory_samples) if self.memory_samples else 0
            }
        }

        return summary

    def save_metrics(self, output_path: str = 'performance_metrics.json'):
        """保存指标到文件"""
        try:
            current_metrics = self.collect_system_metrics()
            summary_stats = self.get_summary_stats()

            data = {
                'timestamp': datetime.now(timezone.utc).isoformat(),
                'current_metrics': current_metrics,
                'summary_statistics': summary_stats,
                'samples': {
                    'cpu_samples': self.cpu_samples[-100:],  # 只保存最近100个样本
                    'memory_samples': self.memory_samples[-100:]
                }
            }

            with open(output_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)

            print(f"💾 性能指标已保存: {output_path}")
            return data

        except Exception as e:
            print(f"❌ 保存性能指标失败: {e}", file=sys.stderr)
            return {}

    def monitor_duration(self, duration_seconds: int, output_path: str = 'performance_metrics.json'):
        """监控指定时间"""
        print(f"🔍 开始性能监控，持续时间: {duration_seconds}秒")

        end_time = time.time() + duration_seconds
        sample_count = 0

        while time.time() < end_time:
            try:
                metrics = self.collect_system_metrics()
                sample_count += 1

                if sample_count % 10 == 0:  # 每10次采样输出一次
                    cpu_avg = sum(self.cpu_samples[-10:]) / min(len(self.cpu_samples), 10)
                    mem_avg = sum(self.memory_samples[-10:]) / min(len(self.memory_samples), 10)
                    print(f"📊 采样 #{sample_count}: CPU={cpu_avg:.1f}%, 内存={mem_avg:.1f}%")

                time.sleep(self.sample_interval)

            except KeyboardInterrupt:
                print("\n⏹️ 监控被用户中断")
                break
            except Exception as e:
                print(f"❌ 监控采样失败: {e}")
                time.sleep(self.sample_interval)

        # 保存最终结果
        final_data = self.save_metrics(output_path)

        if final_data:
            print(f"\n📊 监控完成!")
            print(f"⏱️ 总时长: {final_data['summary_statistics']['monitoring_duration_seconds']:.1f}秒")
            print(f"📈 采样次数: {final_data['summary_statistics']['sample_count']}")
            print(f"💻 平均CPU: {final_data['summary_statistics']['cpu']['average']:.1f}%")
            print(f"💾 平均内存: {final_data['summary_statistics']['memory']['average']:.1f}%")

        return final_data


def check_system_requirements():
    """检查系统要求"""
    print("🔍 检查系统要求...")

    # 检查Python版本
    python_version = sys.version_info
    if python_version < (3, 7):
        print(f"❌ Python版本过低: {python_version.major}.{python_version.minor} (需要 >= 3.7)")
        return False
    else:
        print(f"✅ Python版本: {python_version.major}.{python_version.minor}.{python_version.micro}")

    # 检查内存
    memory = psutil.virtual_memory()
    memory_gb = memory.total / 1024 / 1024 / 1024
    if memory_gb < 2:
        print(f"⚠️ 内存较低: {memory_gb:.1f}GB (建议 >= 2GB)")
    else:
        print(f"✅ 系统内存: {memory_gb:.1f}GB")

    # 检查磁盘空间
    disk = psutil.disk_usage('.')
    disk_gb = disk.free / 1024 / 1024 / 1024
    if disk_gb < 1:
        print(f"⚠️ 磁盘空间不足: {disk_gb:.1f}GB (建议 >= 1GB)")
    else:
        print(f"✅ 可用磁盘空间: {disk_gb:.1f}GB")

    # 检查CPU核心数
    cpu_count = psutil.cpu_count()
    print(f"✅ CPU核心数: {cpu_count}")

    return True


def main():
    """主函数"""
    import argparse

    parser = argparse.ArgumentParser(description='性能监控工具')
    parser.add_argument('--duration', '-d', type=int, default=60,
                       help='监控持续时间(秒) (默认: 60)')
    parser.add_argument('--output', '-o', default='performance_metrics.json',
                       help='输出文件路径 (默认: performance_metrics.json)')
    parser.add_argument('--interval', '-i', type=int, default=5,
                       help='采样间隔(秒) (默认: 5)')
    parser.add_argument('--check', action='store_true',
                       help='只检查系统要求，不进行监控')

    args = parser.parse_args()

    print("🚀 性能监控工具 v1.0")
    print("="*40)

    if args.check:
        success = check_system_requirements()
        sys.exit(0 if success else 1)

    # 检查系统要求
    check_system_requirements()
    print()

    # 创建监控器
    monitor = PerformanceMonitor()
    monitor.sample_interval = args.interval

    # 开始监控
    try:
        result = monitor.monitor_duration(args.duration, args.output)

        # 输出环境变量供GitHub Actions使用
        if result and 'summary_statistics' in result:
            stats = result['summary_statistics']
            print(f"::set-output name=avg_cpu::{stats['cpu']['average']:.2f}")
            print(f"::set-output name=avg_memory::{stats['memory']['average']:.2f}")
            print(f"::set-output name=max_cpu::{stats['cpu']['max']:.2f}")
            print(f"::set-output name=max_memory::{stats['memory']['max']:.2f}")
            print(f"::set-output name=sample_count::{stats['sample_count']}")

    except KeyboardInterrupt:
        print("\n⏹️ 监控被中断")
        sys.exit(130)
    except Exception as e:
        print(f"❌ 监控失败: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()