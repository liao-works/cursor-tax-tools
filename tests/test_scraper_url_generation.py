import pytest
from scraper import TariffScraper

def test_ni_url_format_without_double_slash():
    """验证北爱尔兰 URL 格式正确（无双斜杠）"""
    scraper = TariffScraper()

    # 模拟 parse_commodity_page 返回的结果
    # 需要包含 commodity 链接以便正则表达式提取代码
    test_html = '''
    <html>
        <a href="/commodities/1234567890">Commodity Link</a>
        <h1 class="commodity-header">Test Commodity</h1>
        <table class="small-table">
            <tr><th>Duty rate</th></tr>
            <tr><td><span class="duty-expression"><span>5.00%</span></span></td></tr>
        </table>
    </html>
    '''

    result = scraper.parse_commodity_page(test_html, url="https://www.trade-tariff.service.gov.uk/commodities/1234567890")

    # 验证北爱尔兰 URL 格式正确
    assert 'north_ireland_url' in result
    assert result['north_ireland_url'] == "https://www.trade-tariff.service.gov.uk/xi/commodities/1234567890"
    # 确保没有双斜杠
    assert '//commodities' not in result['north_ireland_url']
