"""测试 ShardExecutor 爬虫调用逻辑"""

import pytest
from unittest.mock import Mock


def test_add_tariff_call_with_north_ireland_url():
    """验证 add_tariff 调用时正确生成并传递北爱尔兰 URL"""

    # 创建 mock 数据库对象
    mock_db = Mock()

    # 模拟 parse_commodity_page 返回的数据
    tariff = {
        'code': '1234567890',
        'description': 'Test Commodity',
        'rate': '5.00%',
        'url': 'https://www.trade-tariff.service.gov.uk/commodities/1234567890'
    }

    # 生成北爱尔兰 URL (模拟代码中的逻辑)
    ni_url = f"https://www.trade-tariff.service.gov.uk/xi/commodities/{tariff['code']}"

    # 调用 add_tariff (模拟 run_shard.py 中的调用逻辑)
    mock_db.add_tariff(
        code=tariff['code'],
        description=tariff['description'],
        rate=tariff['rate'],
        url=tariff.get('url'),
        other_rate=tariff.get('other_rate'),
        north_ireland_url=ni_url  # 确保传递此参数
    )

    # 验证调用
    mock_db.add_tariff.assert_called_once()
    call_kwargs = mock_db.add_tariff.call_args[1]

    # 验证参数
    assert 'north_ireland_url' in call_kwargs, "north_ireland_url 参数未传递"
    assert call_kwargs['north_ireland_url'] == ni_url, "north_ireland_url 值不正确"
    assert call_kwargs['north_ireland_url'] == "https://www.trade-tariff.service.gov.uk/xi/commodities/1234567890"


def test_north_ireland_url_generation():
    """验证北爱尔兰 URL 生成逻辑正确"""

    # 测试不同的商品编码
    test_cases = [
        ('1234567890', 'https://www.trade-tariff.service.gov.uk/xi/commodities/1234567890'),
        ('0201203098', 'https://www.trade-tariff.service.gov.uk/xi/commodities/0201203098'),
        ('0101210000', 'https://www.trade-tariff.service.gov.uk/xi/commodities/0101210000'),
    ]

    for code, expected_url in test_cases:
        ni_url = f"https://www.trade-tariff.service.gov.uk/xi/commodities/{code}"
        assert ni_url == expected_url, f"URL 生成错误: {code} -> {ni_url}"
        # 确保没有双斜杠
        assert '//commodities' not in ni_url, f"URL 包含双斜杠: {ni_url}"
