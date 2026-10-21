"""
HTTP 客户端模块 - 异步批量 URL 抓取

此模块提供高性能的异步 HTTP 请求功能，用于关税数据爬取。

使用方式：
    from src.http.client import scrape_urls

    results = await scrape_urls(urls, headers={'User-Agent': '...'})
"""

import aiohttp
import asyncio
import ssl
from typing import List, Optional, Dict
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def scrape_urls(
    urls: List[str],
    headers: Dict = None,
    max_concurrent: int = 25  # 从 15 增加到 25 以提升性能
) -> List[tuple]:
    """异步抓取多个URL的内容

    Args:
        urls: 要抓取的URL列表
        headers: 请求头
        max_concurrent: 最大并发数（默认 25）

    Returns:
        List[tuple]: 每个元素是 (status_code, content) 元组
                      status_code: HTTP状态码
                      content: 页面内容（如果状态码不是200，则为None）
    """
    if headers is None:
        headers = {}

    # 创建 SSL 上下文
    ssl_context = ssl.create_default_context()
    ssl_context.check_hostname = False
    ssl_context.verify_mode = ssl.CERT_NONE

    async def fetch_url(session: aiohttp.ClientSession, url: str) -> tuple:
        try:
            async with session.get(url, headers=headers, ssl=False) as response:
                status = response.status
                if status == 200:
                    content = await response.text()
                    return (status, content)
                else:
                    logger.warning(f"抓取 {url}: 状态码 {status}")
                    return (status, None)
        except Exception as e:
            logger.error(f"抓取失败 {url}: {str(e)}")
            return (0, None)

    semaphore = asyncio.Semaphore(max_concurrent)

    async def bounded_fetch(session: aiohttp.ClientSession, url: str) -> Optional[str]:
        async with semaphore:
            return await fetch_url(session, url)

    # 使用连接池优化性能（增加连接池大小）
    connector = aiohttp.TCPConnector(
        limit=80,           # 总连接池大小（从 50 增加）
        limit_per_host=30,  # 每个主机的连接数（从 20 增加）
        ttl_dns_cache=600,  # DNS 缓存 10 分钟（从 5 分钟增加）
        use_dns_cache=True,
        ssl=ssl_context,
    )
    timeout = aiohttp.ClientTimeout(total=60, connect=20)

    async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
        tasks = [bounded_fetch(session, url) for url in urls]
        return await asyncio.gather(*tasks)


# 如果直接运行此文件，执行测试
if __name__ == "__main__":
    async def test():
        test_urls = [
            "https://www.trade-tariff.service.gov.uk/browse",
            "https://www.trade-tariff.service.gov.uk/chapters/85",
            "https://www.trade-tariff.service.gov.uk/chapters/84",
        ]
        results = await scrape_urls(test_urls)
        for url, result in zip(test_urls, results):
            status, content = result
            if status == 200:
                logger.info(f"成功抓取 {url}: {len(content)} 字节")
            else:
                logger.error(f"抓取失败 {url}: 状态码 {status}")

    asyncio.run(test())
