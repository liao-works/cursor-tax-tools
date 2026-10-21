#!/usr/bin/env python3
"""检查页面的 Measure Type 详情"""

import requests
from bs4 import BeautifulSoup
import sys

url = "https://www.trade-tariff.service.gov.uk/commodities/0206801000"

print(f"正在检查页面: {url}\n")

# 获取页面
headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
}
response = requests.get(url, headers=headers, timeout=30)
soup = BeautifulSoup(response.text, 'html.parser')

# 查找所有税率表格
duty_tables = soup.find_all('table', class_='small-table')

print(f"找到 {len(duty_tables)} 个税率表格\n")
print("=" * 80)

for table_idx, table in enumerate(duty_tables, 1):
    print(f"\n📊 表格 {table_idx}:")
    print("-" * 80)

    # 获取表头
    headers = table.find_all('th')
    header_names = [th.get_text(strip=True) for th in headers]
    print(f"列名: {header_names}\n")

    # 查找 "Duty rate" 列索引
    duty_rate_idx = None
    for i, th in enumerate(headers):
        if "Duty rate" in th.get_text(strip=True):
            duty_rate_idx = i
            break

    if duty_rate_idx is None:
        print("❌ 未找到 'Duty rate' 列")
        continue

    # 遍历所有行
    rows = table.find_all('tr')
    print(f"{'Country':<30} | {'Measure Type':<40} | {'Duty Rate'}")
    print("-" * 100)

    for row in rows:
        cells = row.find_all(['td', 'th'])
        if cells and len(cells) > duty_rate_idx:
            country_cell = cells[0].get_text(strip=True)
            measure_type_cell = cells[1].get_text(strip=True) if len(cells) > 1 else ""

            # 提取税率
            duty_rate_elem = cells[duty_rate_idx].find('span', class_='duty-expression')
            if duty_rate_elem:
                rate_span = duty_rate_elem.find('span')
                duty_rate = rate_span.get_text(strip=True) if rate_span else duty_rate_elem.get_text(strip=True)
            else:
                duty_rate = cells[duty_rate_idx].get_text(strip=True)

            # 检查是否匹配我们的条件
            measure_type_lower = measure_type_cell.lower()
            valid_keywords = ["third country duty", "non preferential duty", "other"]
            is_valid = any(keyword in measure_type_lower for keyword in valid_keywords)

            status = "✅" if is_valid else "❌"
            print(f"{status} {country_cell:<28} | {measure_type_cell:<38} | {duty_rate}")

    print("=" * 80)
