"""
智能更新客户端
基于元数据文件判断是否需要更新数据库
"""

import requests
import json
import os
import hashlib
import sqlite3
from datetime import datetime, timezone
from typing import Dict, Optional, Tuple
import logging
import sys

# 添加项目根目录到 Python 路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

# ✅ 导入自定义异常类
from src.core.exceptions import (
    UpdateError,
    NetworkError,
    DatabaseError,
    MetadataError,
    IntegrityError,
    FileOperationError,
    BackupError
)

# ✅ 导入重试机制库
import backoff

# ✅ 导入数据库路径处理函数
from src.db.database import get_writable_db_path

logger = logging.getLogger(__name__)

class SmartUpdateChecker:
    """智能更新检查器"""

    def __init__(self, metadata_url: str, db_path: str = 'tariffs.db'):
        self.metadata_url = metadata_url
        # 确保使用可写的数据库路径
        self.db_path = get_writable_db_path(db_path)
        self.local_metadata_path = f"{self.db_path}.metadata.json"
        self.max_backups = 3  # ✅ 保留最近3个备份

        # ✅ 确保数据库文件所在的目录存在
        db_dir = os.path.dirname(self.db_path)
        if db_dir and not os.path.exists(db_dir):
            os.makedirs(db_dir, exist_ok=True)
            logger.info(f"📁 创建数据目录: {db_dir}")

    def _get_backup_path(self, index: int = 0) -> str:
        """获取备份文件路径（统一命名规则）"""
        if index == 0:
            return f"{self.db_path}.backup"
        return f"{self.db_path}.backup.{index}"

    def _create_backup(self) -> Optional[str]:
        """创建备份并管理备份数量"""
        if not os.path.exists(self.db_path):
            return None

        try:
            # ✅ 轮转备份：.backup -> .backup.1 -> .backup.2
            for i in range(self.max_backups - 1, 0, -1):
                old_path = self._get_backup_path(i - 1)
                new_path = self._get_backup_path(i)
                if os.path.exists(old_path):
                    os.replace(old_path, new_path)

            # 创建新备份
            backup_path = self._get_backup_path(0)
            os.replace(self.db_path, backup_path)
            logger.info(f"💾 创建备份: {backup_path}")
            return backup_path
        except Exception as e:
            logger.error(f"❌ 创建备份失败: {str(e)}")
            return None

    def _restore_backup(self) -> bool:
        """恢复最新的备份"""
        backup_path = self._get_backup_path(0)
        if os.path.exists(backup_path):
            try:
                os.replace(backup_path, self.db_path)
                logger.info(f"♻️ 已恢复备份: {backup_path}")
                return True
            except Exception as e:
                logger.error(f"❌ 恢复备份失败: {str(e)}")
                return False
        return False

    @backoff.on_exception(
        backoff.expo,
        (requests.Timeout, requests.ConnectionError),
        max_tries=3,
        max_time=60,
        on_backoff=lambda details: logger.warning(
            f"⏱️ 重试下载元数据 (第{details['tries']}次尝试，等待{details['wait']:.1f}秒)..."
        )
    )
    def download_metadata(self, timeout: int = 30) -> Optional[Dict]:
        """下载远程元数据（带重试机制）

        重试策略：
        - 最多重试3次
        - 最长重试时间60秒
        - 指数退避策略（1s, 2s, 4s...）
        - 仅对超时和连接错误重试

        Raises:
            NetworkError: 网络连接失败、超时或HTTP错误
            MetadataError: 元数据格式错误或解析失败
        """
        try:
            logger.info(f"📡 下载元数据: {self.metadata_url}")
            response = requests.get(self.metadata_url, timeout=timeout)
            response.raise_for_status()

            metadata = response.json()
            logger.info(f"✅ 元数据下载成功: 版本 {metadata.get('version')}")
            return metadata

        except requests.Timeout:
            error_msg = f"下载元数据超时（{timeout}秒）"
            logger.error(f"❌ {error_msg}")
            raise NetworkError(error_msg)
        except requests.ConnectionError as e:
            error_msg = f"网络连接失败: {str(e)}"
            logger.error(f"❌ {error_msg}")
            raise NetworkError(error_msg)
        except requests.HTTPError as e:
            error_msg = f"HTTP错误 {e.response.status_code}: {str(e)}"
            logger.error(f"❌ {error_msg}")
            raise NetworkError(error_msg)
        except requests.RequestException as e:
            error_msg = f"请求失败: {str(e)}"
            logger.error(f"❌ {error_msg}")
            raise NetworkError(error_msg)
        except json.JSONDecodeError as e:
            error_msg = f"元数据格式错误: {str(e)}"
            logger.error(f"❌ {error_msg}")
            raise MetadataError(error_msg)

    def load_local_metadata(self) -> Optional[Dict]:
        """加载本地元数据"""
        try:
            if os.path.exists(self.local_metadata_path):
                with open(self.local_metadata_path, 'r', encoding='utf-8') as f:
                    metadata = json.load(f)
                logger.info(f"📂 加载本地元数据: 版本 {metadata.get('version')}")
                return metadata
        except Exception as e:
            logger.error(f"❌ 加载本地元数据失败: {str(e)}")

        return None

    def save_local_metadata(self, metadata: Dict):
        """保存本地元数据"""
        try:
            with open(self.local_metadata_path, 'w', encoding='utf-8') as f:
                json.dump(metadata, f, indent=2, ensure_ascii=False)
            logger.info(f"💾 保存本地元数据: 版本 {metadata.get('version')}")
        except Exception as e:
            logger.error(f"❌ 保存本地元数据失败: {str(e)}")

    def get_local_db_info(self) -> Dict:
        """获取本地数据库信息

        Raises:
            FileOperationError: 文件读取失败
            DatabaseError: 数据库查询失败
        """
        if not os.path.exists(self.db_path):
            return {'exists': False}

        try:
            stat_info = os.stat(self.db_path)

            # 计算本地文件哈希
            hash_sha256 = hashlib.sha256()
            try:
                with open(self.db_path, 'rb') as f:
                    while chunk := f.read(8192):
                        hash_sha256.update(chunk)
            except IOError as e:
                raise FileOperationError(f"读取数据库文件失败: {str(e)}")

            # ✅ 使用 with 语句管理数据库连接（避免资源泄漏）
            record_count = 0
            try:
                with sqlite3.connect(self.db_path) as conn:
                    cursor = conn.cursor()
                    record_count = cursor.execute("SELECT COUNT(*) FROM tariffs").fetchone()[0]
            except sqlite3.Error as e:
                # 如果表不存在，记录警告但不抛出异常，返回 record_count = 0
                logger.warning(f"⚠️ 数据库查询失败（可能是空数据库或表不存在）: {str(e)}")
                record_count = 0

            return {
                'exists': True,
                'file_size': stat_info.st_size,
                'file_hash': f"sha256:{hash_sha256.hexdigest()}",
                'last_modified': datetime.fromtimestamp(stat_info.st_mtime, tz=timezone.utc).isoformat(),
                'record_count': record_count
            }

        except FileOperationError:
            # 重新抛出文件操作异常
            raise
        except Exception as e:
            logger.error(f"❌ 获取本地数据库信息失败: {str(e)}")
            raise FileOperationError(f"获取数据库信息失败: {str(e)}")

    def check_update_needed(self, remote_metadata: Dict, local_db_info: Dict, local_metadata: Dict = None) -> Tuple[bool, str, Dict]:
        """检查是否需要更新"""
        reasons = []
        update_needed = False

        # 1. 检查文件是否存在
        if not local_db_info.get('exists'):
            update_needed = True
            reasons.append("本地数据库文件不存在")
            return True, " | ".join(reasons), {'priority': 'high'}

        # 2. 检查版本号
        remote_version = remote_metadata.get('version')
        local_version = local_metadata.get('version') if local_metadata else None

        if remote_version != local_version:
            update_needed = True
            reasons.append(f"版本不匹配: {local_version} → {remote_version}")

        # 3. 检查文件哈希
        remote_hash = remote_metadata.get('file_hash')
        local_hash = local_db_info.get('file_hash')

        if remote_hash != local_hash:
            update_needed = True
            reasons.append("文件内容已变更")

        # 4. 检查更新时间
        remote_time = remote_metadata.get('timestamp')
        local_time = local_db_info.get('last_modified')

        if remote_time and local_time:
            try:
                remote_dt = datetime.fromisoformat(remote_time.replace('Z', '+00:00'))
                local_dt = datetime.fromisoformat(local_time.replace('Z', '+00:00'))

                if remote_dt > local_dt:
                    reasons.append(f"远程数据更新时间更新: {remote_time} > {local_time}")
                    # 如果时间更新但哈希相同，可能是元数据更新，不强制数据库更新
                    if not update_needed:
                        reasons.append("仅元数据更新，数据库文件无需更新")
                        return False, " | ".join(reasons), {'priority': 'low'}
            except ValueError:
                logger.warning("⚠️ 时间戳解析失败")

        # 5. 检查变更数量阈值
        changes_summary = remote_metadata.get('changes_summary', {})
        min_threshold = remote_metadata.get('client_instructions', {}).get('min_change_threshold', 10)
        total_changes = changes_summary.get('total_updates', 0)

        if total_changes < min_threshold and update_needed:
            reasons.append(f"变更数量({total_changes})少于阈值({min_threshold})，可跳过更新")
            update_needed = False

        # 6. 检查数据完整性
        if local_db_info.get('record_count', 0) != remote_metadata.get('record_count', 0):
            update_needed = True
            reasons.append(f"记录数量不匹配: {local_db_info.get('record_count')} → {remote_metadata.get('record_count')}")

        # 确定更新优先级
        priority = 'medium'
        if update_needed:
            if any("不存在" in reason or "不匹配" in reason for reason in reasons):
                priority = 'high'
            elif total_changes > 100:
                priority = 'high'
            elif total_changes > 50:
                priority = 'medium'
            else:
                priority = 'low'

        return update_needed, " | ".join(reasons) if reasons else "无需更新", {'priority': priority}

    @backoff.on_exception(
        backoff.expo,
        (requests.Timeout, requests.ConnectionError),
        max_tries=2,
        max_time=120,
        on_backoff=lambda details: logger.warning(
            f"⏱️ 重试下载数据库 (第{details['tries']}次尝试，等待{details['wait']:.1f}秒)..."
        )
    )
    def download_database(self, download_url: str, verify_checksum: bool = True, progress_callback=None) -> bool:
        """下载数据库文件（带重试机制）

        重试策略：
        - 最多重试2次（大文件下载，避免过多重试）
        - 最长重试时间120秒
        - 指数退避策略
        - 仅对超时和连接错误重试

        Args:
            download_url: 数据库下载URL
            verify_checksum: 是否验证文件完整性
            progress_callback: 进度回调函数，接收参数(downloaded_bytes, total_bytes, percentage)

        Returns:
            bool: 下载是否成功
        """
        backup_path = None
        try:
            logger.info(f"📥 开始下载数据库: {download_url}")

            # ✅ 使用统一的备份方法
            backup_path = self._create_backup()

            # 下载文件
            response = requests.get(download_url, stream=True, timeout=300)  # 5分钟超时
            response.raise_for_status()

            total_size = int(response.headers.get('content-length', 0))
            downloaded_size = 0

            with open(self.db_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
                        downloaded_size += len(chunk)

                        # 计算下载进度
                        if total_size > 0:
                            progress = (downloaded_size / total_size) * 100

                            # 调用进度回调函数（如果提供）
                            if progress_callback:
                                try:
                                    progress_callback(downloaded_size, total_size, progress)
                                except Exception as e:
                                    logger.warning(f"进度回调函数执行失败: {str(e)}")

                            # 控制台显示进度
                            print(f"\r📥 下载进度: {progress:.1f}%", end="", flush=True)

            print()  # 换行
            logger.info(f"✅ 数据库下载完成: {downloaded_size / 1024 / 1024:.1f}MB")

            # 验证文件完整性
            if verify_checksum:
                logger.info("🔒 验证文件完整性...")
                remote_metadata = self.download_metadata()
                if remote_metadata:
                    local_info = self.get_local_db_info()
                    if local_info.get('file_hash') != remote_metadata.get('file_hash'):
                        logger.error("❌ 文件完整性验证失败")
                        # ✅ 使用统一的恢复方法
                        self._restore_backup()
                        raise IntegrityError("文件完整性验证失败")

            logger.info("✅ 数据库更新完成")
            return True

        except (requests.Timeout, requests.ConnectionError):
            # 重新抛出，让装饰器处理重试
            raise
        except IntegrityError:
            # 完整性错误不重试，直接失败
            logger.error("❌ 文件完整性验证失败，不重试")
            if backup_path:
                self._restore_backup()
            return False
        except Exception as e:
            logger.error(f"❌ 数据库下载失败: {str(e)}")
            # ✅ 使用统一的恢复方法
            if backup_path:
                self._restore_backup()
            return False

    def check_and_update(self, force_update: bool = False, progress_callback=None) -> Dict:
        """检查并执行更新

        Args:
            force_update: 是否强制更新
            progress_callback: 进度回调函数，接收参数(downloaded_bytes, total_bytes, percentage)
        """
        result = {
            'status': 'unknown',
            'message': '',
            'details': {}
        }

        try:
            # 下载远程元数据
            remote_metadata = self.download_metadata()
            if not remote_metadata:
                result['status'] = 'error'
                result['message'] = '无法下载远程元数据'
                return result

            # 获取本地信息
            local_db_info = self.get_local_db_info()
            local_metadata = self.load_local_metadata()

            # 检查是否需要更新
            if not force_update:
                update_needed, reason, check_details = self.check_update_needed(
                    remote_metadata, local_db_info, local_metadata
                )
            else:
                update_needed = True
                reason = "强制更新"
                check_details = {'priority': 'high'}

            result['details'] = {
                'remote_version': remote_metadata.get('version'),
                'local_version': local_metadata.get('version') if local_metadata else None,
                'remote_record_count': remote_metadata.get('record_count'),
                'local_record_count': local_db_info.get('record_count'),
                'remote_file_size': remote_metadata.get('file_size'),
                'local_file_size': local_db_info.get('file_size'),
                'changes_summary': remote_metadata.get('changes_summary'),
                'priority': check_details.get('priority')
            }

            if not update_needed:
                result['status'] = 'up_to_date'
                result['message'] = reason
                return result

            # 需要更新
            logger.info(f"🔄 检测到需要更新: {reason}")
            download_urls = remote_metadata.get('download_urls', {})
            download_url = download_urls.get('primary')

            if not download_url:
                result['status'] = 'error'
                result['message'] = '无法获取下载链接'
                return result

            # 下载数据库（传递进度回调）
            if self.download_database(download_url, verify_checksum=True, progress_callback=progress_callback):
                # 保存新的元数据
                self.save_local_metadata(remote_metadata)

                # ✅ 统一返回状态为 'success'（与GUI期望一致）
                result['status'] = 'success'
                result['message'] = f"更新完成: {reason}"
                result['details']['new_version'] = remote_metadata.get('version')
                return result
            else:
                result['status'] = 'error'
                result['message'] = '数据库下载失败'
                return result

        except NetworkError as e:
            logger.error(f"❌ 网络错误: {str(e)}")
            result['status'] = 'error'
            result['message'] = f'网络错误: {str(e)}'
            result['error_type'] = 'network'
            return result
        except MetadataError as e:
            logger.error(f"❌ 元数据错误: {str(e)}")
            result['status'] = 'error'
            result['message'] = f'元数据错误: {str(e)}'
            result['error_type'] = 'metadata'
            return result
        except DatabaseError as e:
            logger.error(f"❌ 数据库错误: {str(e)}")
            result['status'] = 'error'
            result['message'] = f'数据库错误: {str(e)}'
            result['error_type'] = 'database'
            return result
        except FileOperationError as e:
            logger.error(f"❌ 文件操作错误: {str(e)}")
            result['status'] = 'error'
            result['message'] = f'文件操作错误: {str(e)}'
            result['error_type'] = 'file'
            return result
        except UpdateError as e:
            logger.error(f"❌ 更新错误: {str(e)}")
            result['status'] = 'error'
            result['message'] = f'更新错误: {str(e)}'
            result['error_type'] = 'update'
            return result
        except Exception as e:
            logger.error(f"❌ 未知错误: {str(e)}")
            result['status'] = 'error'
            result['message'] = f'未知错误: {str(e)}'
            result['error_type'] = 'unknown'
            return result

# 使用示例
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

    # 配置元数据URL（实际使用时替换为真实的GitHub Release URL）
    METADATA_URL = "https://github.com/owner/repo/releases/download/latest-data/metadata.json"

    # 创建更新检查器
    checker = SmartUpdateChecker(METADATA_URL)

    # 检查并更新
    result = checker.check_and_update()

    print(f"\n🎯 更新结果: {result['status']}")
    print(f"📝 消息: {result['message']}")

    if result['details']:
        print(f"\n📊 详细信息:")
        for key, value in result['details'].items():
            print(f"  {key}: {value}")