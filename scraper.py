"""
向后兼容层 - 请使用 src.core.scraper

此模块为了保持向后兼容性而保留，新代码应直接导入：
    from src.core.scraper import TariffScraper, BatchUpdateManager
"""

from src.core.scraper import TariffScraper, BatchUpdateManager

__all__ = ['TariffScraper', 'BatchUpdateManager']
