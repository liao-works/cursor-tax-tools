"""
向后兼容层 - 请使用 src.api.tariff_api

此模块为了保持向后兼容性而保留，新代码应直接导入：
    from src.api.tariff_api import TariffAPI
"""

from src.api.tariff_api import TariffAPI

__all__ = ['TariffAPI']
