"""
数据库层模块 - 关税数据存储和查询

此模块提供关税数据的持久化存储功能。

使用方式：
    from src.db.database import TariffDB, get_writable_db_path

    db = TariffDB()
    db.add_tariff(code='1234567890', description='商品描述', rate='5%')
"""

import sqlite3
import logging
import os
import sys
import shutil
from typing import List, Dict, Optional
import threading

logger = logging.getLogger(__name__)


def get_writable_db_path(db_path: str = "tariffs.db") -> str:
    """获取可写的数据库文件路径

    路径优先级：
    1. 绝对路径 → 直接使用
    2. 当前目录的数据库文件（如果存在且可写）
    3. 用户数据目录（打包环境的回退方案）
    4. 打包资源（仅用于初始化，复制到可写位置）

    Args:
        db_path: 数据库文件路径（相对或绝对）

    Returns:
        str: 可写的数据库文件绝对路径
    """
    # 1. 如果是绝对路径，直接返回
    if os.path.isabs(db_path):
        return db_path

    # 2. 检查当前目录是否已有数据库文件且可写
    current_dir_db = os.path.abspath(db_path)
    if os.path.exists(current_dir_db):
        # 检查是否可写
        if os.access(os.path.dirname(current_dir_db), os.W_OK):
            logger.info(f"✅ 使用当前目录的数据库: {current_dir_db}")
            return current_dir_db
        else:
            logger.warning(f"⚠️ 当前目录不可写，将使用用户数据目录")

    # 3. 尝试在当前目录创建（如果可写）
    if os.access(os.path.dirname(current_dir_db) or ".", os.W_OK):
        target_db = current_dir_db
    else:
        # 当前目录不可写（如打包后的 Program Files），使用用户数据目录
        if sys.platform == "win32":
            app_data_dir = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "TariffTools")
        elif sys.platform == "darwin":
            app_data_dir = os.path.join(os.path.expanduser("~"), "Library", "Application Support", "TariffTools")
        else:
            app_data_dir = os.path.join(os.path.expanduser("~"), ".tarifftools")

        # 确保目录存在
        os.makedirs(app_data_dir, exist_ok=True)
        target_db = os.path.join(app_data_dir, os.path.basename(db_path))
        logger.info(f"ℹ️ 使用用户数据目录: {target_db}")

    # 4. 如果在打包环境中且目标数据库不存在，从打包资源复制
    if hasattr(sys, '_MEIPASS') and not os.path.exists(target_db):
        resource_db = os.path.join(sys._MEIPASS, os.path.basename(db_path))
        if os.path.exists(resource_db):
            try:
                shutil.copy2(resource_db, target_db)
                logger.info(f"✅ 从打包资源初始化数据库: {target_db}")
            except Exception as e:
                logger.warning(f"⚠️ 无法从打包资源复制数据库: {e}")

    return target_db


# 失败类型常量（决定是否重试）
FAILURE_TRANSIENT = "transient"   # 可重试：network/db
FAILURE_PERMANENT = "permanent"   # 不重试：not_found/data_missing
FAILURE_LIMITED = "limited"       # 有限重试：parse/unknown


def classify_failure(error_message, status=None, exc_type=None):
    """分类失败类型，决定是否重试更新。

    信号优先级：status（最强）> exc_type > error_message 关键词。
    用于 add_scrape_error 写入 failure_type，供末尾重试筛选。

    Args:
        error_message: 错误描述文本
        status: HTTP 状态码（404/5xx/0=异常等），可选
        exc_type: 异常类型（类名或类型对象），可选

    Returns:
        'transient'(可重试) | 'permanent'(不重试) | 'limited'(有限重试)
    """
    msg = (error_message or '').lower()

    # 1. status 信号（最强）
    if status is not None:
        if status == 404:
            return FAILURE_PERMANENT
        if status == 0 or status >= 500:
            return FAILURE_TRANSIENT

    # 2. exc_type 信号（超时/连接异常）
    if exc_type:
        exc_name = str(exc_type).lower()
        if 'timeout' in exc_name or 'connection' in exc_name:
            return FAILURE_TRANSIENT

    # 3. error_message 关键词
    if '保存失败' in msg or 'database' in msg or 'locked' in msg:
        return FAILURE_TRANSIENT
    if '超时' in msg or '连接' in msg or 'timeout' in msg or 'connection' in msg:
        return FAILURE_TRANSIENT
    if '未找到' in msg or '不存在' in msg:
        return FAILURE_PERMANENT
    if '解析' in msg:
        return FAILURE_LIMITED

    # 默认保守：有限重试
    return FAILURE_LIMITED


class TariffDB:
    """关税数据库操作类

    提供线程安全的数据库访问，支持增删改查操作。
    """

    def __init__(self, db_path: str = "tariffs.db"):
        """初始化数据库连接

        Args:
            db_path: 数据库文件路径
        """
        # 确保使用可写的数据库路径
        self.db_path = get_writable_db_path(db_path)
        self._local = threading.local()
        self._create_tables()
        # 检测数据库是否有 last_updated 列
        self._has_last_updated = self._check_column_exists('last_updated')

    def _check_column_exists(self, column_name: str) -> bool:
        """检查表中是否存在指定列"""
        try:
            cursor = self.conn.execute(f"PRAGMA table_info(tariffs)")
            columns = [row[1] for row in cursor.fetchall()]
            return column_name in columns
        except Exception:
            return False

    @property
    def conn(self) -> sqlite3.Connection:
        """获取当前线程的数据库连接"""
        if not hasattr(self._local, "conn"):
            # 确保数据库文件的父目录存在
            db_dir = os.path.dirname(self.db_path)
            if db_dir and not os.path.exists(db_dir):
                try:
                    os.makedirs(db_dir, exist_ok=True)
                    logger.info(f"✅ 创建数据库目录: {db_dir}")
                except Exception as e:
                    logger.error(f"❌ 无法创建数据库目录 {db_dir}: {e}")
                    raise

            self._local.conn = sqlite3.connect(self.db_path)
        return self._local.conn

    def _create_tables(self):
        """创建数据表"""
        try:
            with self.conn:
                self.conn.execute("""
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

                # 检查是否需要添加other_rate字段（为了向后兼容旧版本数据库）
                try:
                    self.conn.execute("ALTER TABLE tariffs ADD COLUMN other_rate TEXT")
                    logger.info("✅ 成功添加other_rate列")
                except sqlite3.OperationalError as e:
                    if "duplicate column name" in str(e).lower():
                        logger.debug("ℹ️ other_rate列已存在，跳过")
                    else:
                        raise e

                # 检查是否需要添加last_updated字段（为了向后兼容旧版本数据库）
                try:
                    # SQLite的ALTER TABLE不支持DEFAULT CURRENT_TIMESTAMP，需要分两步
                    self.conn.execute("ALTER TABLE tariffs ADD COLUMN last_updated DATETIME")
                    # 为现有记录设置默认时间戳
                    self.conn.execute("UPDATE tariffs SET last_updated = CURRENT_TIMESTAMP WHERE last_updated IS NULL")
                    logger.info("✅ 成功添加last_updated列")
                except sqlite3.OperationalError as e:
                    if "duplicate column name" in str(e).lower():
                        logger.debug("ℹ️ last_updated列已存在，跳过")
                    else:
                        raise e

                # 检查是否需要添加anti_dumping_rate字段（为了向后兼容旧版本数据库）
                try:
                    self.conn.execute("ALTER TABLE tariffs ADD COLUMN anti_dumping_rate TEXT")
                    logger.info("✅ 成功添加anti_dumping_rate列")
                except sqlite3.OperationalError as e:
                    if "duplicate column name" in str(e).lower():
                        logger.debug("ℹ️ anti_dumping_rate列已存在，跳过")
                    else:
                        raise e

                # 检查是否需要添加countervailing_rate字段（为了向后兼容旧版本数据库）
                try:
                    self.conn.execute("ALTER TABLE tariffs ADD COLUMN countervailing_rate TEXT")
                    logger.info("✅ 成功添加countervailing_rate列")
                except sqlite3.OperationalError as e:
                    if "duplicate column name" in str(e).lower():
                        logger.debug("ℹ️ countervailing_rate列已存在，跳过")
                    else:
                        raise e

                # 创建索引
                self.conn.execute("CREATE INDEX IF NOT EXISTS idx_code ON tariffs(code)")

                # 添加错误记录表
                self.conn.execute("""
                CREATE TABLE IF NOT EXISTS scrape_errors (
                    code TEXT PRIMARY KEY,
                    error_message TEXT,
                    failure_type TEXT,
                    retry_count INTEGER DEFAULT 0,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
                )
                """)

                # 向后兼容：为旧库补充 failure_type / retry_count 字段
                for _col, _decl in [('failure_type', 'TEXT'), ('retry_count', 'INTEGER DEFAULT 0')]:
                    try:
                        self.conn.execute(f"ALTER TABLE scrape_errors ADD COLUMN {_col} {_decl}")
                        logger.info(f"✅ 成功添加 scrape_errors.{_col} 列")
                    except sqlite3.OperationalError as _e:
                        if "duplicate column name" in str(_e).lower():
                            logger.debug(f"ℹ️ scrape_errors.{_col} 列已存在，跳过")
                        else:
                            raise _e

                logger.info("✅ 数据库表结构创建完成")
        except Exception as e:
            logger.error(f"创建表失败: {str(e)}")
            raise

    def add_tariff(self, code: str, description: str, rate: str, url: str = None,
                   other_rate: str = None, north_ireland_url: str = None,
                   anti_dumping_rate: str = None, countervailing_rate: str = None):
        """添加关税记录

        Args:
            code: 商品编码
            description: 商品描述
            rate: 英国税率
            url: 英国URL（可选，默认自动生成）
            other_rate: 其他税率（可选）
            north_ireland_url: 北爱尔兰URL（可选，默认自动生成）
            anti_dumping_rate: 反倾销税税率（可选）
            countervailing_rate: 反补贴税税率（可选）
        """
        if url is None:
            url = f"https://www.trade-tariff.service.gov.uk/commodities/{code}"

        # 如果没有提供北爱尔兰URL，自动生成
        if north_ireland_url is None:
            north_ireland_url = f"https://www.trade-tariff.service.gov.uk/xi/commodities/{code}"

        try:
            with self.conn:
                # 根据是否有 last_updated 列使用不同的插入语句
                if self._has_last_updated:
                    self.conn.execute(
                        "INSERT OR REPLACE INTO tariffs (code, description, rate, url, other_rate, north_ireland_url, anti_dumping_rate, countervailing_rate, last_updated) VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)",
                        (code, description, rate, url, other_rate, north_ireland_url, anti_dumping_rate, countervailing_rate)
                    )
                else:
                    self.conn.execute(
                        "INSERT OR REPLACE INTO tariffs (code, description, rate, url, other_rate, north_ireland_url, anti_dumping_rate, countervailing_rate) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (code, description, rate, url, other_rate, north_ireland_url, anti_dumping_rate, countervailing_rate)
                    )
        except Exception as e:
            logger.error(f"添加记录失败: {str(e)}")
            raise

    def get_tariff(self, code: str) -> Optional[Dict]:
        """精确查询关税记录"""
        try:
            # 根据是否有 last_updated 列使用不同的查询
            if self._has_last_updated:
                cur = self.conn.execute(
                    "SELECT code, description, rate, url, north_ireland_rate, north_ireland_url, other_rate, anti_dumping_rate, countervailing_rate, last_updated FROM tariffs WHERE code = ?",
                    (code,)
                )
                row = cur.fetchone()
                if row:
                    return {
                        'code': row[0],
                        'description': row[1],
                        'rate': row[2],
                        'url': row[3],
                        'north_ireland_rate': row[4],
                        'north_ireland_url': row[5],
                        'other_rate': row[6],
                        'anti_dumping_rate': row[7],
                        'countervailing_rate': row[8],
                        'last_updated': row[9]
                    }
            else:
                cur = self.conn.execute(
                    "SELECT code, description, rate, url, north_ireland_rate, north_ireland_url, other_rate, anti_dumping_rate, countervailing_rate FROM tariffs WHERE code = ?",
                    (code,)
                )
                row = cur.fetchone()
                if row:
                    return {
                        'code': row[0],
                        'description': row[1],
                        'rate': row[2],
                        'url': row[3],
                        'north_ireland_rate': row[4],
                        'north_ireland_url': row[5],
                        'other_rate': row[6],
                        'anti_dumping_rate': row[7],
                        'countervailing_rate': row[8],
                        'last_updated': None
                    }
            return None
        except Exception as e:
            logger.error(f"查询记录失败: {str(e)}")
            raise

    def get_all_tariffs(self) -> List[Dict]:
        """获取所有关税记录"""
        try:
            # 根据是否有 last_updated 列使用不同的查询
            if self._has_last_updated:
                cur = self.conn.execute("SELECT code, description, rate, url, north_ireland_url, north_ireland_rate, anti_dumping_rate, countervailing_rate, last_updated FROM tariffs")
                return [
                    {
                        'code': row[0],
                        'description': row[1],
                        'rate': row[2],
                        'url': row[3],
                        'north_ireland_url': row[4],
                        'north_ireland_rate': row[5],
                        'anti_dumping_rate': row[6],
                        'countervailing_rate': row[7],
                        'last_updated': row[8]
                    }
                    for row in cur.fetchall()
                ]
            else:
                cur = self.conn.execute("SELECT code, description, rate, url, north_ireland_url, north_ireland_rate, anti_dumping_rate, countervailing_rate FROM tariffs")
                return [
                    {
                        'code': row[0],
                        'description': row[1],
                        'rate': row[2],
                        'url': row[3],
                        'north_ireland_url': row[4],
                        'north_ireland_rate': row[5],
                        'anti_dumping_rate': row[6],
                        'countervailing_rate': row[7],
                        'last_updated': None
                    }
                    for row in cur.fetchall()
                ]
        except Exception as e:
            logger.error(f"获取所有记录失败: {str(e)}")
            raise

    def get_record_count(self) -> int:
        """获取数据库中的记录数"""
        try:
            cur = self.conn.execute("SELECT COUNT(*) FROM tariffs")
            return cur.fetchone()[0]
        except Exception as e:
            logger.error(f"获取记录数失败: {str(e)}")
            raise

    def add_tariffs_batch(self, tariffs: List[Dict]):
        """批量添加关税记录"""
        try:
            with self.conn:
                self.conn.executemany(
                    "INSERT OR REPLACE INTO tariffs (code, description, rate, url, other_rate) VALUES (?, ?, ?, ?, ?)",
                    [(t['code'], t['description'], t['rate'], t.get('url'), t.get('other_rate')) for t in tariffs]
                )
        except Exception as e:
            logger.error(f"批量添加记录失败: {str(e)}")
            raise

    def save_to_db(self, tariffs: List[Dict]):
        """保存到数据库"""
        try:
            self.add_tariffs_batch(tariffs)
            logger.info(f"成功保存 {len(tariffs)} 条记录")
        except Exception as e:
            logger.error(f"保存记录失败: {str(e)}")

    def get_existing_codes(self) -> set:
        """获取已存在的所有商品编码"""
        try:
            cur = self.conn.execute("SELECT code FROM tariffs")
            return set(row[0] for row in cur.fetchall())
        except Exception as e:
            logger.error(f"获取已存在编码失败: {str(e)}")
            return set()

    def get_existing_codes_north_ireland(self) -> set:
        """获取已存在的所有北爱尔兰商品编码"""
        try:
            cur = self.conn.execute("SELECT code FROM tariffs WHERE north_ireland_rate IS NOT NULL")
            return set(row[0] for row in cur.fetchall())
        except Exception as e:
            logger.error(f"获取已存在北爱尔兰编码失败: {str(e)}")
            return set()

    def get_north_ireland_count(self) -> int:
        """获取有北爱尔兰数据的记录数量"""
        try:
            cur = self.conn.execute(
                "SELECT COUNT(*) FROM tariffs WHERE north_ireland_rate IS NOT NULL AND north_ireland_rate != ''"
            )
            return cur.fetchone()[0]
        except Exception as e:
            logger.error(f"获取北爱尔兰记录数量失败: {str(e)}")
            return 0

    def update_north_ireland_tariff(self, code: str, north_ireland_rate: str, north_ireland_url: str):
        """更新北爱尔兰关税记录"""
        try:
            with self.conn:
                # 根据是否有 last_updated 列使用不同的更新语句
                if self._has_last_updated:
                    self.conn.execute(
                        "UPDATE tariffs SET north_ireland_rate = ?, north_ireland_url = ?, last_updated = CURRENT_TIMESTAMP WHERE code = ?",
                        (north_ireland_rate, north_ireland_url, code)
                    )
                else:
                    self.conn.execute(
                        "UPDATE tariffs SET north_ireland_rate = ?, north_ireland_url = ? WHERE code = ?",
                        (north_ireland_rate, north_ireland_url, code)
                    )
        except Exception as e:
            logger.error(f"更新北爱尔兰关税记录失败: {str(e)}")
            raise

    def update_tariff(self, code: str, description: str = None, rate: str = None, url: str = None,
                      north_ireland_rate: str = None, north_ireland_url: str = None, other_rate: str = None,
                      anti_dumping_rate: str = None, countervailing_rate: str = None):
        """更新关税记录（支持部分字段更新）"""
        try:
            # 构建动态更新SQL
            updates = []
            params = []

            if description is not None:
                updates.append("description = ?")
                params.append(description)
            if rate is not None:
                updates.append("rate = ?")
                params.append(rate)
            if url is not None:
                updates.append("url = ?")
                params.append(url)
            if north_ireland_rate is not None:
                updates.append("north_ireland_rate = ?")
                params.append(north_ireland_rate)
            if north_ireland_url is not None:
                updates.append("north_ireland_url = ?")
                params.append(north_ireland_url)
            if other_rate is not None:
                updates.append("other_rate = ?")
                params.append(other_rate)
            if anti_dumping_rate is not None:
                updates.append("anti_dumping_rate = ?")
                params.append(anti_dumping_rate)
            if countervailing_rate is not None:
                updates.append("countervailing_rate = ?")
                params.append(countervailing_rate)

            if not updates:
                logger.warning("没有提供任何要更新的字段")
                return

            # 如果有 last_updated 列，则更新时间戳
            if self._has_last_updated:
                updates.append("last_updated = CURRENT_TIMESTAMP")

            params.append(code)  # WHERE条件

            sql = f"UPDATE tariffs SET {', '.join(updates)} WHERE code = ?"
            with self.conn:
                self.conn.execute(sql, params)
            logger.info(f"成功更新商品编码 {code} 的记录")
        except Exception as e:
            logger.error(f"更新关税记录失败: {str(e)}")
            raise

    def delete_tariff(self, code: str):
        """删除关税记录"""
        try:
            with self.conn:
                # 删除tariffs表中的记录
                self.conn.execute("DELETE FROM tariffs WHERE code = ?", (code,))
                # 同时删除scrape_errors表中的错误记录
                self.conn.execute("DELETE FROM scrape_errors WHERE code = ?", (code,))
            logger.info(f"已删除商品编码 {code} 的记录")
        except Exception as e:
            logger.error(f"删除关税记录失败: {str(e)}")
            raise

    def add_scrape_error(self, code: str, error_message: str, failure_type: str = None,
                         status: int = None, exc_type=None):
        """记录抓取错误，自动分类失败类型。

        Args:
            code: 商品编码
            error_message: 错误描述
            failure_type: 失败类型（transient/permanent/limited）。None 时自动 classify_failure
            status: HTTP 状态码，传给 classify_failure
            exc_type: 异常类型，传给 classify_failure

        重复记录（同 code）时保留 retry_count（重试逻辑单独递增）。
        """
        if failure_type is None:
            failure_type = classify_failure(error_message, status=status, exc_type=exc_type)
        try:
            with self.conn:
                # ON CONFLICT 保留 retry_count，仅更新 message/type/timestamp
                self.conn.execute(
                    """INSERT INTO scrape_errors (code, error_message, failure_type, retry_count, timestamp)
                       VALUES (?, ?, ?, 0, CURRENT_TIMESTAMP)
                       ON CONFLICT(code) DO UPDATE SET
                           error_message = excluded.error_message,
                           failure_type = excluded.failure_type,
                           timestamp = CURRENT_TIMESTAMP""",
                    (code, error_message, failure_type)
                )
        except Exception as e:
            logger.error(f"记录抓取错误失败: {str(e)}")

    def get_scrape_errors(self, code: str = None) -> List[Dict]:
        """获取抓取错误记录"""
        try:
            if code:
                # 获取指定编码的错误记录
                cur = self.conn.execute(
                    "SELECT code, error_message, failure_type, retry_count, timestamp FROM scrape_errors WHERE code = ?",
                    (code,)
                )
            else:
                # 获取所有错误记录
                cur = self.conn.execute(
                    "SELECT code, error_message, failure_type, retry_count, timestamp FROM scrape_errors"
                )

            return [
                {
                    'code': row[0],
                    'error_message': row[1],
                    'failure_type': row[2],
                    'retry_count': row[3],
                    'timestamp': row[4]
                }
                for row in cur.fetchall()
            ]
        except Exception as e:
            logger.error(f"获取抓取错误记录失败: {str(e)}")
            return []

    def clear_scrape_error(self, code: str):
        """清除指定编码的抓取错误记录"""
        try:
            with self.conn:
                self.conn.execute("DELETE FROM scrape_errors WHERE code = ?", (code,))
        except Exception as e:
            logger.error(f"清除抓取错误记录失败: {str(e)}")

    def increment_retry_count(self, code: str):
        """递增失败记录的重试计数（重试仍失败时调用）"""
        try:
            with self.conn:
                self.conn.execute(
                    "UPDATE scrape_errors SET retry_count = retry_count + 1 WHERE code = ?",
                    (code,)
                )
        except Exception as e:
            logger.error(f"递增重试计数失败: {str(e)}")
