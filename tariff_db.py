"""
向后兼容层 - 请使用 src.db.database

此模块为了保持向后兼容性而保留，新代码应直接导入：
    from src.db.database import TariffDB, get_writable_db_path
"""

from src.db.database import TariffDB, get_writable_db_path

__all__ = ['TariffDB', 'get_writable_db_path']
