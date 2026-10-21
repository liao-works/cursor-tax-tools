#!/usr/bin/env python3
"""测试单个商品的爬取功能"""

import sys
import requests
from scraper import TariffScraper
import logging

# 设置日志级别
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

def test_single_commodity(url: str):
    """测试单个商品页面的解析"""
    print(f"\n{'='*60}")
    print(f"测试 URL: {url}")
    print(f"{'='*60}\n")

    # 初始化爬虫
    scraper = TariffScraper()

    # 获取页面内容
    try:
        print(f"📥 正在获取页面...")
        response = requests.get(
            url,
            headers=scraper.headers,
            timeout=scraper.timeout
        )
        response.raise_for_status()
        print(f"✅ 页面获取成功 (状态码: {response.status_code})\n")

        # 解析页面
        print(f"🔍 正在解析页面...")
        result = scraper.parse_commodity_page(response.text, url)

        if result:
            print(f"\n{'='*60}")
            print(f"✅ 解析成功！")
            print(f"{'='*60}")
            print(f"📦 商品编码: {result.get('code', 'N/A')}")
            print(f"📝 商品描述: {result.get('description', 'N/A')}")
            print(f"💰 一般税率: {result.get('rate', 'N/A')}")
            print(f"🌍 Other税率: {result.get('other_rate', 'N/A')}")
            print(f"🔗 URL: {result.get('url', 'N/A')}")
            print(f"{'='*60}\n")
        else:
            print(f"❌ 解析失败：未返回有效数据")
            print(f"可能原因：")
            print(f"  - 商品编码已存在于数据库中")
            print(f"  - 页面结构发生变化")
            print(f"  - Measure type 不匹配")

    except requests.exceptions.RequestException as e:
        print(f"❌ 请求失败: {e}")
    except Exception as e:
        print(f"❌ 发生错误: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    # 测试 URL
    test_url = "https://www.trade-tariff.service.gov.uk/commodities/0206801000"

    if len(sys.argv) > 1:
        test_url = sys.argv[1]

    test_single_commodity(test_url)
