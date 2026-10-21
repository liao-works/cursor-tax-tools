"""测试5609000000自动更新功能"""
import sys
import os
sys.path.insert(0, os.getcwd())

from tariff_api import TariffAPI

# 创建API实例
api = TariffAPI('tariffs.db')

# 获取当前数据
print("=" * 60)
print("当前数据库中的数据:")
print("=" * 60)
current = api.exact_search('5609000000')
print(f"英国税率: {current.get('rate')}")
print(f"英国URL: {current.get('url')}")
print(f"北爱税率: {current.get('north_ireland_rate')}")
print(f"北爱URL: {current.get('north_ireland_url')}")

# 测试自动更新
print("\n" + "=" * 60)
print("测试自动更新（使用修复后的URL）:")
print("=" * 60)

uk_url = current.get('url')  # 使用修复后的英国URL
ni_url = current.get('north_ireland_url')

result = api.auto_update('5609000000', uk_url, ni_url)

print(f"\n整体成功: {result.get('overall_success')}")
print(f"消息: {result.get('message')}")
print(f"是否有更新: {result.get('updated')}")
print(f"英国成功: {result.get('uk_success')}")
print(f"英国更新: {result.get('uk_updated')}")
print(f"北爱成功: {result.get('ni_success')}")
print(f"北爱更新: {result.get('ni_updated')}")

if result.get('new_data'):
    print("\n新数据:")
    print(f"  英国税率: {result['new_data'].get('rate')}")
    print(f"  北爱税率: {result['new_data'].get('north_ireland_rate')}")

if result.get('old_data'):
    print("\n旧数据:")
    print(f"  英国税率: {result['old_data'].get('rate')}")
    print(f"  北爱税率: {result['old_data'].get('north_ireland_rate')}")
