"""
关税爬虫核心模块

此模块提供关税数据爬取和批量更新功能。

使用方式：
    from src.core.scraper import TariffScraper, BatchUpdateManager

    scraper = TariffScraper()
    tariffs = await scraper.scrape_tariffs()
"""

import asyncio
import sys
import os
from typing import List, Dict, Set
from bs4 import BeautifulSoup
import re
import logging

# 添加项目根目录到 Python 路径（确保可以导入 src 模块）
_project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from src.http.client import scrape_urls
from src.db.database import TariffDB

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# NI 未提供时的错误标识（与 fetch_ni_data 约定一致）
_NI_NOT_PROVIDED = "未提供北爱尔兰URL"


def should_delete_code(uk_data, uk_error, ni_data, ni_error):
    """判定商品编码是否应从数据库删除（已被 trade-tariff 移除/废止）。

    规则：UK 和 NI 都"无效"才删（任一地仍有有效数据则保留）。

    无效的判定：
    - 干净 HTTP 404：data 为 None 且 error 为 None
    - 200 但无有效 duty rate：data 是含 code 的 dict，但 rate 为空
      （trade-tariff 对废止编码常返回 200 + 无 duty rate 页面，而非 404；
       典型案例如 9403208000 重定向到 /subheadings/，parse 后 rate 空）

    保守保留（不算无效）：
    - 抓取错误：data 为 None 但 error 非 None（超时/网络异常，下次重试）
    - 空 dict：parse 异常或状态不确定

    注意：判据基于【重新抓取的解析结果】，绝不可用 DB 旧 rate——
    Phase 3 实证 DB 中 rate 为空的记录 6/7 是有效商品的历史抓取失败残留。

    Args:
        uk_data/ni_data: parse_commodity_page 的返回（dict 或 None）
        uk_error/ni_error: 抓取错误信息（None 表示无错误）

    Returns:
        bool: True 表示应删除
    """
    def _is_invalid(data, error):
        # NI 未提供（仅 NI 会出现此 error）
        if error == _NI_NOT_PROVIDED:
            return True
        # 干净 404（明确的不存在）
        if data is None and error is None:
            return True
        # 抓取错误 → 不确定，保守保留
        if data is None:
            return False
        # 200 解析结果：有 code 但无有效 duty rate = 已废止
        if isinstance(data, dict) and data.get('code'):
            return not (data.get('rate') or '').strip()
        # 空 dict（parse 异常/跳过）→ 保守保留
        return False

    return _is_invalid(uk_data, uk_error) and _is_invalid(ni_data, ni_error)


def classify_commodity(status, tariff):
    """对路径1（全量抓取）单条 commodity 抓取结果分类。

    路径1 只抓 UK 页面，无 NI 数据；NI 视为未提供，废弃识别用 UK-only 判定。

    Args:
        status: HTTP 状态码（200/404/0=异常）
        tariff: parse_commodity_page 的返回（dict/None），仅 status==200 且有内容时有效

    Returns:
        'delete' | 'save' | 'skip'
        - 'delete': 废弃（404，或 200 但无有效税率）→ 从 DB 删除
        - 'save': 有效（200 且有税率）→ 保存/更新
        - 'skip': 异常（status=0）或空 dict → 保守不动
    """
    if status == 404:
        return 'delete'
    if status == 200 and tariff:
        if should_delete_code(tariff, None, None, _NI_NOT_PROVIDED):
            return 'delete'
        return 'save'
    # status=0（异常）或 200 但 tariff 为空 dict/None → 保守跳过
    return 'skip'


class TariffScraper:
    def __init__(self):
        """初始化TariffScraper

        Args:
"""
        self.base_url = "https://www.trade-tariff.service.gov.uk"
        self.browse_url = f"{self.base_url}/browse"
        self.visited_urls: Set[str] = set()
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        self.timeout = 30  # 请求超时时间
        self.max_retries = 3  # 最大重试次数
        self.db = TariffDB()
        self.existing_codes = self.db.get_existing_codes()  # 获取已存在的编码
        logger.info(f"已存在 {len(self.existing_codes)} 条记录")

    async def scrape_with_retry(self, urls: List[str]) -> List[tuple]:
        """带重试的抓取 - 使用指数退避策略

        返回: List[tuple], 每个元素是 (status_code, content) 元组。
        重试耗尽时返回最后一次的真实结果（可能是真 404 或其他状态），
        不再统一伪装成 404——以避免把"抓取失败/超时"误判为"商品不存在"，
        进而在全量抓取路径误删有效商品。全部异常（从未拿到结果）时返回 (0, None)。
        """
        last_results = None
        for retry in range(self.max_retries):
            try:
                results = await scrape_urls(urls, headers=self.headers)
                last_results = results
                # 检查是否有成功的状态码
                if any(status == 200 for status, _ in results):
                    return results
            except Exception as e:
                logger.warning(f"第{retry + 1}次重试失败: {str(e)}")
                # 指数退避：1s, 2s, 4s, 8s, 最大10s
                backoff_time = min(2 ** retry, 10)
                await asyncio.sleep(backoff_time)
        # 重试耗尽：返回最后真实结果（真404/其他状态/0=异常），无结果则 0=异常
        if last_results is not None:
            return last_results
        return [(0, None) for _ in urls]

    def parse_section_links(self, html: str) -> List[str]:
        """解析主页面获取section链接"""
        soup = BeautifulSoup(html, 'html.parser')
        links = []

        # 查找section表格
        section_table = soup.find('table', class_='tariff-table')
        if not section_table:
            logger.error("未找到section表格")
            return links

        # 查找所有section链接
        for row in section_table.find_all('tr'):
            link = row.find('a')
            if link and link.get('href'):
                href = link.get('href')
                if href.startswith('/sections/'):
                    full_url = f"{self.base_url}{href}"
                    if full_url not in self.visited_urls:
                        links.append(full_url)
                        logger.debug(f"找到section链接: {full_url}")

        logger.info(f"共找到 {len(links)} 个section链接")
        return links

    def parse_chapter_links(self, html: str) -> List[str]:
        """解析section页面获取chapter链接"""
        soup = BeautifulSoup(html, 'html.parser')
        links = []

        # 查找chapter表格
        chapter_table = soup.find('table', class_='govuk-table')
        if not chapter_table:
            logger.debug("未找到chapter表格")
            return links

        # 查找所有chapter链接
        for row in chapter_table.find_all('tr', class_='govuk-table__row'):
            link = row.find('a')
            if link and link.get('href'):
                href = link.get('href')
                if href.startswith('/chapters/'):
                    full_url = f"{self.base_url}{href}"
                    if full_url not in self.visited_urls:
                        links.append(full_url)
                        logger.debug(f"找到chapter链接: {full_url}")

        logger.info(f"共找到 {len(links)} 个chapter链接")
        return links

    def parse_heading_links(self, html: str) -> List[str]:
        """解析chapter页面获取heading链接"""
        soup = BeautifulSoup(html, 'html.parser')
        links = []

        # 查找heading表格
        tables = soup.find_all('table', class_='govuk-table')
        for table in tables:
            for row in table.find_all('tr', class_='govuk-table__row'):
                link = row.find('a')
                if link and link.get('href'):
                    href = link.get('href')
                    if href.startswith('/headings/'):
                        full_url = f"{self.base_url}{href}"
                        if full_url not in self.visited_urls:
                            links.append(full_url)
                            logger.debug(f"找到heading链接: {full_url}")

        logger.info(f"共找到 {len(links)} 个heading链接")
        return links

    def parse_commodity_links(self, html: str) -> List[str]:
        """解析heading页面获取commodity链接"""
        soup = BeautifulSoup(html, 'html.parser')
        links = []

        # 查找所有commodity链接（只抓取英国commodity，过滤北爱尔兰）
        for link in soup.find_all('a', href=True):
            href = link.get('href')
            # 只抓取英国commodity，过滤掉北爱尔兰commodity（/xi/commodities/）
            if href and '/commodities/' in href and '/xi/commodities/' not in href:
                full_url = f"{self.base_url}{href}"
                if full_url not in self.visited_urls:
                    links.append(full_url)
                    logger.debug(f"找到commodity链接: {full_url}")

        logger.info(f"共找到 {len(links)} 个commodity链接")
        return links

    def parse_commodity_page(self, html: str, url: str = "") -> Dict:
        """解析commodity页面获取税率信息"""
        soup = BeautifulSoup(html, 'html.parser')
        result = {}

        try:
            # 查找商品编码：优先用 url 的 code（避免重定向页面 soup 链接 code 错位，
            # 如 9403208000 重定向到 subheading，soup 里是 9403208020 链接），
            # fallback 到 soup 提取（兼容无 url 场景）
            url_code_match = re.search(r'/commodities/(\d+)', url or '')
            soup_code_match = re.search(r'/commodities/(\d+)', str(soup))
            code_match = url_code_match or soup_code_match
            if code_match:
                code = code_match.group(1)
                # 如果编码已存在，直接返回空
                if code in self.existing_codes:
                    logger.debug(f"编码 {code} 已存在，跳过")
                    return {}

                result['code'] = code
                result['url'] = url or f"https://www.trade-tariff.service.gov.uk/commodities/{code}"
                # 自动生成北爱尔兰 URL
                result['north_ireland_url'] = f"https://www.trade-tariff.service.gov.uk/xi/commodities/{code}"

            # 查找商品描述（更新：使用正确的class名）
            desc_elem = soup.find('h1', class_='commodity-header')
            if desc_elem:
                result['description'] = desc_elem.text.strip()
                logger.debug(f"找到商品描述: {result['description']}")
            else:
                error_msg = f"未找到商品描述 for code: {result.get('code')}"
                logger.warning(error_msg)
                if result.get('code'):
                    self.db.add_scrape_error(result['code'], error_msg)
                result['description'] = ''

            # 查找税率
            found_rate = False
            duty_tables = soup.find_all('table', class_='small-table')
            for table in duty_tables:
                if found_rate:
                    break

                headers = table.find_all('th')
                duty_rate_idx = None

                # 查找"Duty rate"列的索引
                for i, th in enumerate(headers):
                    header_text = th.text.strip()
                    if "Duty rate" in header_text:
                        duty_rate_idx = i
                        break

                if duty_rate_idx is not None:
                    # 查找包含"All countries"、"United Kingdom"或"Other"的行
                    rows = table.find_all('tr')
                    for row in rows:
                        cells = row.find_all(['td', 'th'])
                        if cells and len(cells) > duty_rate_idx:
                            country_cell = cells[0].get_text(strip=True)

                            # 提取税率值的通用函数
                            def extract_rate(cell):
                                duty_rate_elem = cell.find('span', class_='duty-expression')
                                if duty_rate_elem:
                                    # 找到嵌套的span中的税率值
                                    rate_span = duty_rate_elem.find('span')
                                    return rate_span.get_text(strip=True) if rate_span else duty_rate_elem.get_text(strip=True)
                                else:
                                    # 备用方法：直接获取文本
                                    return cell.get_text(strip=True)

                            # 获取 measure type（第二列）
                            measure_type_cell = cells[1].get_text(strip=True) if len(cells) > 1 else ""

                            # 处理United Kingdom或All countries（一般税率）
                            if "All countries" in country_cell or "United Kingdom" in country_cell:
                                # 更宽松的匹配：允许 Third country duty, Non preferential duty, Other 等类型
                                measure_type_lower = measure_type_cell.lower()
                                # 定义有效关键词列表
                                valid_keywords = ["third country duty", "non preferential duty", "other"]
                                # 只要包含任一关键词就认为是有效行
                                if not any(keyword in measure_type_lower for keyword in valid_keywords):
                                    logger.debug(f"跳过非关税行: {country_cell}, measure type: {measure_type_cell}")
                                    continue

                                duty_rate = extract_rate(cells[duty_rate_idx])
                                result['rate'] = duty_rate
                                logger.debug(f"找到一般税率: {duty_rate} (measure type: {measure_type_cell})")
                                found_rate = True
                                # 继续查找Other税率
                                continue

                            # 处理Other地区
                            elif "Other" in country_cell:
                                # 更宽松的匹配：允许 Third country duty, Non preferential duty, Other 等类型
                                measure_type_lower = measure_type_cell.lower()
                                # 定义有效关键词列表
                                valid_keywords = ["third country duty", "non preferential duty", "other"]
                                # 只要包含任一关键词就认为是有效行
                                if not any(keyword in measure_type_lower for keyword in valid_keywords):
                                    logger.debug(f"跳过非关税行: {country_cell}, measure type: {measure_type_cell}")
                                    continue

                                other_rate = extract_rate(cells[duty_rate_idx])
                                result['other_rate'] = other_rate
                                logger.debug(f"找到Other税率: {other_rate} (measure type: {measure_type_cell})")

                    # 如果没有找到一般税率，尝试从Other中获取
                    if not found_rate and result.get('other_rate'):
                        result['rate'] = result['other_rate']
                        found_rate = True
                        logger.debug(f"使用Other税率作为一般税率: {result['other_rate']}")

            if 'rate' not in result and result.get('code'):
                error_msg = f"未找到税率 for code: {result.get('code')}"
                logger.warning(error_msg)
                self.db.add_scrape_error(result['code'], error_msg)
                result['rate'] = ''

            # 解析贸易救济措施（反倾销税、反补贴税）
            trade_remedies_data = self.parse_trade_remedies(soup)
            result['anti_dumping_rate'] = trade_remedies_data['anti_dumping_rate']
            result['countervailing_rate'] = trade_remedies_data['countervailing_rate']

            return result

        except Exception as e:
            error_msg = f"解析页面失败: {str(e)}"
            logger.error(error_msg)
            if result.get('code'):
                self.db.add_scrape_error(result['code'], error_msg)
            return {}

    def parse_trade_remedies(self, soup: BeautifulSoup) -> Dict[str, str]:
        """解析贸易救济措施（反倾销税、反补贴税）

        Returns:
            Dict: {
                'anti_dumping_rate': str,    # 反倾销税率
                'countervailing_rate': str   # 反补贴税率
            }
        """
        result = {
            'anti_dumping_rate': '',
            'countervailing_rate': ''
        }

        try:
            # 1. 查找 trade_remedies 部分
            trade_remedies_header = soup.find('h3', id='trade_remedies')
            if not trade_remedies_header:
                return result

            # 2. 查找下一个表格
            next_table = trade_remedies_header.find_next_sibling('table', class_='small-table')
            if not next_table:
                return result

            # 3. 解析表格列索引
            headers = next_table.find_all('th')
            measure_type_idx = None
            duty_rate_idx = None

            for i, th in enumerate(headers):
                header_text = th.text.strip()
                if "Measure type" in header_text:
                    measure_type_idx = i
                elif "Duty rate" in header_text:
                    duty_rate_idx = i

            if measure_type_idx is None or duty_rate_idx is None:
                return result

            # 4. 遍历表格行
            rows = next_table.find_all('tr')
            for row in rows:
                cells = row.find_all(['td', 'th'])
                if len(cells) <= max(measure_type_idx, duty_rate_idx):
                    continue

                # 检查是否为 "所有其他海外出口商"
                measure_type_cell = cells[measure_type_idx].get_text(strip=True)

                if "All other overseas exporters" in measure_type_cell and "residual amount" in measure_type_cell:
                    # 提取税率
                    duty_rate_cell = cells[duty_rate_idx]
                    duty_rate_elem = duty_rate_cell.find('span', class_='duty-expression')
                    if duty_rate_elem:
                        rate_span = duty_rate_elem.find('span')
                        rate = rate_span.get_text(strip=True) if rate_span else duty_rate_elem.get_text(strip=True)

                        # 根据完整的 measure type 判断是反倾销还是反补贴
                        full_measure_type = measure_type_cell.lower()
                        if "anti-dumping" in full_measure_type or "anti dumping" in full_measure_type:
                            result['anti_dumping_rate'] = rate
                            logger.debug(f"找到反倾销税率: {rate}")
                        elif "countervailing" in full_measure_type:
                            result['countervailing_rate'] = rate
                            logger.debug(f"找到反补贴税率: {rate}")

        except Exception as e:
            logger.warning(f"解析贸易救济措施失败: {str(e)}")

        return result

    async def initialize(self) -> bool:
        """初始化抓取器，返回是否成功"""
        try:
            logger.info("开始初始化抓取器...")
            tariffs = await self.scrape_tariffs()
            if tariffs:
                self.save_to_db(tariffs)
                logger.info("初始化完成")
                return True
            else:
                logger.error("初始化失败：未获取到数据")
                return False
        except Exception as e:
            logger.error(f"初始化失败：{str(e)}")
            return False

    async def scrape_tariffs(self) -> List[Dict]:
        """抓取关税数据"""
        try:
            # 1. 获取section列表
            logger.info(f"开始抓取主页面: {self.browse_url}")
            results = await self.scrape_with_retry([self.browse_url])
            status, content = results[0]
            if status != 200 or not content:
                logger.error("无法访问主页面")
                return []

            section_urls = self.parse_section_links(content)
            if not section_urls:
                logger.error("未找到任何section链接")
                return []

            # 2. 分批处理section
            batch_size = 10  # 每批处理5个section
            for i in range(0, len(section_urls), batch_size):
                batch_urls = section_urls[i:i + batch_size]
                logger.info(f"正在处理第 {i//batch_size + 1} 批section，共 {len(batch_urls)} 个")

                section_results = await self.scrape_with_retry(batch_urls)
                chapter_urls = []
                for status, content in section_results:
                    if status == 200 and content:
                        urls = self.parse_chapter_links(content)
                        chapter_urls.extend(urls)

                # 3. 分批处理chapter
                for j in range(0, len(chapter_urls), batch_size):
                    chapter_batch = chapter_urls[j:j + batch_size]
                    logger.info(f"正在处理第 {j//batch_size + 1} 批chapter，共 {len(chapter_batch)} 个")

                    chapter_results = await self.scrape_with_retry(chapter_batch)
                    heading_urls = []
                    for status, content in chapter_results:
                        if status == 200 and content:
                            urls = self.parse_heading_links(content)
                            heading_urls.extend(urls)

                    # 4. 分批处理heading
                    for k in range(0, len(heading_urls), batch_size):
                        heading_batch = heading_urls[k:k + batch_size]
                        logger.info(f"正在处理第 {k//batch_size + 1} 批heading，共 {len(heading_batch)} 个")

                        heading_results = await self.scrape_with_retry(heading_batch)
                        commodity_urls = []
                        for status, content in heading_results:
                            if status == 200 and content:
                                urls = self.parse_commodity_links(content)
                                commodity_urls.extend(urls)

                        # 5. 分批处理commodity并直接保存
                        for m in range(0, len(commodity_urls), batch_size):
                            commodity_batch = commodity_urls[m:m + batch_size]
                            logger.info(f"正在处理第 {m//batch_size + 1} 批commodity，共 {len(commodity_batch)} 个")

                            commodity_results = await self.scrape_with_retry(commodity_batch)
                            batch_tariffs = []
                            codes_to_delete = []
                            codes_to_clear_errors = []

                            for n, (status, content) in enumerate(commodity_results):
                                # 提取 code（删除/清理 error 都需要）
                                code_match = re.search(r'/commodities/(\d+)', commodity_batch[n])
                                code = code_match.group(1) if code_match else None
                                # 仅 200 且有内容时才解析，避免对 404/异常页解析
                                tariff = self.parse_commodity_page(content, url=commodity_batch[n]) \
                                    if (status == 200 and content) else None
                                action = classify_commodity(status, tariff)

                                if action == 'delete':
                                    # 废弃（404 或 200 无税率）→ 标记删除
                                    if code:
                                        codes_to_delete.append(code)
                                        # 仅更新错误记录模式下，改为清理 error 而非删除
                                        if filter_func:
                                            codes_to_clear_errors.append(code)
                                            logger.info(f"发现废弃状态（错误更新模式），标记清理error记录: {code}")
                                        else:
                                            logger.info(f"发现废弃状态（404或无税率），标记删除商品编码: {code}")
                                elif action == 'save':
                                    batch_tariffs.append(tariff)
                                # action == 'skip'（异常/空 dict）→ 保守不动

                            # 清理废弃记录的error数据（仅在错误更新模式下）
                            if codes_to_clear_errors:
                                for code in codes_to_clear_errors:
                                    self.db.clear_scrape_error(code)
                                logger.info(f"已清理 {len(codes_to_clear_errors)} 条废弃记录的error数据")

                            # 删除废弃的记录（404 或 200 无税率）
                            if codes_to_delete:
                                for code in codes_to_delete:
                                    self.db.delete_tariff(code)
                                logger.info(f"已删除 {len(codes_to_delete)} 条废弃记录")

                            # 直接保存这一批数据
                            if batch_tariffs:
                                self.save_to_db(batch_tariffs)
                                logger.info(f"已保存 {len(batch_tariffs)} 条commodity记录")

            total_count = self.get_db_count()
            logger.info(f"抓取完成，数据库共有 {total_count} 条记录")
            # 末尾重试 transient 失败（网络抖动等可恢复失败）
            await self.retry_transient_failures()
            return []

        except Exception as e:
            logger.error(f"抓取过程出错: {str(e)}")
            return []

    def save_to_db(self, tariffs: List[Dict]):
        """保存到数据库"""
        saved_count = 0
        for tariff in tariffs:
            try:
                if not tariff or 'code' not in tariff:
                    continue

                if tariff['code'] in self.existing_codes:
                    continue

                self.db.add_tariff(
                    code=tariff['code'],
                    description=tariff['description'],
                    rate=tariff['rate'],
                    url=tariff.get('url'),
                    other_rate=tariff.get('other_rate'),
                    anti_dumping_rate=tariff.get('anti_dumping_rate'),
                    countervailing_rate=tariff.get('countervailing_rate')
                )
                self.existing_codes.add(tariff['code'])  # 更新已存在编码集合
                saved_count += 1

                # 如果保存成功，清除可能存在的错误记录
                self.db.clear_scrape_error(tariff['code'])

            except Exception as e:
                logger.error(f"保存记录失败: {str(e)}")
                if tariff.get('code'):
                    self.db.add_scrape_error(tariff['code'], f"保存失败: {str(e)}")
                continue
        logger.info(f"成功保存 {saved_count} 条记录")

    def get_db_count(self) -> int:
        """获取数据库中的记录总数"""
        # 使用已导入的 TariffDB（无需重复导入）
        db = TariffDB()
        return db.get_record_count()

    async def retry_transient_failures(self, max_retries: int = 3, base_interval: int = 5) -> Dict:
        """重试 transient 失败的商品编码（抓取流程末尾调用）。

        筛选 failure_type=transient 且 retry_count < max_retries 的 code，重新抓取；
        成功则清理失败记录，失败则 retry_count++。重试间隔随次数指数退避
        （比 scrape_with_retry 的 1/2/4s 更长，给目标站点恢复时间）。
        """
        errors = self.db.get_scrape_errors()
        retryable = [e for e in errors
                     if e.get('failure_type') == 'transient'
                     and (e.get('retry_count') or 0) < max_retries]

        succeeded = 0
        failed = 0
        for err in retryable:
            code = err['code']
            retry_count = err.get('retry_count') or 0
            # 退避间隔（比 scrape_with_retry 更长：base*2^n，上限 60s）
            await asyncio.sleep(min(base_interval * (2 ** retry_count), 60))
            url = f"{self.base_url}/commodities/{code}"
            try:
                results = await self.scrape_with_retry([url])
                status, content = results[0]
                if status == 200 and content:
                    tariff = self.parse_commodity_page(content, url=url)
                    if tariff and tariff.get('rate'):
                        # 用 add_tariff（INSERT OR REPLACE）覆盖更新，
                        # 避免 save_to_db 因 existing_codes 跳过已存在的失败 code
                        self.db.add_tariff(
                            code=tariff['code'],
                            description=tariff.get('description', ''),
                            rate=tariff['rate'],
                            url=tariff.get('url'),
                            other_rate=tariff.get('other_rate'),
                        )
                        self.existing_codes.add(tariff['code'])
                        self.db.clear_scrape_error(code)
                        succeeded += 1
                        continue
                # 抓取失败或仍无有效税率 → 递增重试计数
                self.db.increment_retry_count(code)
                failed += 1
            except Exception as e:
                logger.warning(f"重试 {code} 异常: {str(e)}")
                self.db.increment_retry_count(code)
                failed += 1

        if retryable:
            logger.info(f"末尾重试: 共 {len(retryable)} 个, 成功 {succeeded}, 仍失败 {failed}")
        return {'retried': len(retryable), 'succeeded': succeeded, 'failed': failed}

    async def retry_network_errors(self, codes_with_status, max_retries: int = 3,
                                   intervals=(30, 60, 120)) -> List[Dict]:
        """重试网络错误 code（400/500），成功则补全，仍失败返回 failed_codes。

        CI 中抓取返回 400/500 的 code，在任务末尾用更长间隔重试 3 次
        （30/60/120s，比 scrape_with_retry 的 1/2/4s 更长）。仍失败的写入
        metadata.failed_codes，供客户端更新后补全。

        Args:
            codes_with_status: [(code, status), ...] 待重试 code 及首次失败状态码
            max_retries: 最大重试次数
            intervals: 每次重试间隔（秒）

        Returns:
            failed_codes: 仍失败列表，每个 {code, status, error_type, last_attempt, retries}
        """
        from datetime import datetime

        pending = list(codes_with_status)  # [(code, first_status)]
        for attempt in range(max_retries):
            if not pending:
                break
            await asyncio.sleep(intervals[attempt] if attempt < len(intervals) else intervals[-1])
            still_failed = []
            for code, first_status in pending:
                url = f"{self.base_url}/commodities/{code}"
                try:
                    results = await self.scrape_with_retry([url])
                    status, content = results[0]
                    if status == 200 and content:
                        tariff = self.parse_commodity_page(content, url=url)
                        if tariff and tariff.get('rate'):
                            self.db.add_tariff(
                                code=tariff['code'], description=tariff.get('description', ''),
                                rate=tariff['rate'], url=tariff.get('url'),
                                other_rate=tariff.get('other_rate'),
                            )
                            continue  # 补全成功
                except Exception as e:
                    logger.warning(f"重试 {code} 异常: {e}")
                still_failed.append((code, first_status))
            pending = still_failed

        def _etype(s):
            if s == 0:
                return 'network'
            if 400 <= s < 500:
                return 'client_error'
            if s >= 500:
                return 'server_error'
            return 'unknown'

        return [{
            'code': code,
            'status': status,
            'error_type': _etype(status),
            'last_attempt': datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ'),
            'retries': max_retries,
        } for code, status in pending]

    async def auto_update_single(self, code: str, uk_url: str, ni_url: str = None) -> Dict:
        """自动更新单个商品的税率信息（支持英国和北爱尔兰，独立更新）

        Args:
            code: 商品编码
            uk_url: 英国税率URL
            ni_url: 北爱尔兰税率URL（可选）

        Returns:
            包含更新结果的字典：{
                'overall_success': bool,  # 总体是否成功（至少一个地区成功）
                'uk_success': bool,       # 英国数据是否获取成功
                'ni_success': bool,       # 北爱尔兰数据是否获取成功
                'uk_updated': bool,       # 英国数据是否实际更新
                'ni_updated': bool,       # 北爱尔兰数据是否实际更新
                'message': str,           # 详细的状态描述
                'old_data': dict,         # 更新前的数据
                'new_data': dict          # 更新后的数据
            }
        """
        try:
            # 获取当前数据
            old_data = self.db.get_tariff(code)
            if not old_data:
                # 记录错误到数据库
                error_message = f'未找到商品编码 {code} 的记录'
                try:
                    self.db.add_scrape_error(code, error_message)
                except Exception as db_error:
                    logger.error(f"保存错误记录到数据库失败 {code}: {str(db_error)}")

                return {
                    'overall_success': False,
                    'uk_success': False,
                    'ni_success': False,
                    'uk_updated': False,
                    'ni_updated': False,
                    'message': error_message,
                    'old_data': None,
                    'new_data': None
                }

            # 创建解析器实例避免修改全局状态
            temp_parser = self._create_temp_parser()

            async def fetch_all_data():
                """并行抓取英国和北爱尔兰数据"""
                async def fetch_uk_data():
                    """抓取并解析英国数据"""
                    try:
                        results = await self.scrape_with_retry([uk_url])
                        status, content = results[0]
                        if status != 200 or not content:
                            if status == 404:
                                return None, None  # 404不记录错误
                            return None, f"英国网页内容为空（状态码: {status}）"
                        return temp_parser.parse_commodity_page(content, uk_url), None
                    except Exception as e:
                        return None, f"英国数据抓取失败: {str(e)}"

                async def fetch_ni_data():
                    """抓取并解析北爱尔兰数据"""
                    # 如果没有提供 ni_url，自动生成
                    current_ni_url = ni_url
                    if not current_ni_url:
                        current_ni_url = f"https://www.trade-tariff.service.gov.uk/xi/commodities/{code}"
                        logger.debug(f"自动生成北爱尔兰 URL: {current_ni_url}")

                    try:
                        results = await self.scrape_with_retry([current_ni_url])
                        status, content = results[0]
                        if status != 200 or not content:
                            if status == 404:
                                return None, None  # 404不记录错误
                            return None, f"北爱尔兰网页内容为空（状态码: {status}）"
                        return temp_parser.parse_commodity_page(content, current_ni_url), None
                    except Exception as e:
                        return None, f"北爱尔兰数据抓取失败: {str(e)}"

                # 并行执行抓取任务
                tasks = [fetch_uk_data()]
                if ni_url:
                    tasks.append(fetch_ni_data())

                results = await asyncio.gather(*tasks, return_exceptions=True)
                return results

            # 执行并行抓取
            fetch_results = await fetch_all_data()

            # 解析结果
            uk_result = fetch_results[0]
            ni_result = fetch_results[1] if len(fetch_results) > 1 else (None, "未提供北爱尔兰URL")

            uk_data_parsed, uk_error = uk_result if isinstance(uk_result, tuple) else (None, "英国数据解析异常")
            ni_data_parsed, ni_error = ni_result if isinstance(ni_result, tuple) else (None, "北爱尔兰数据解析异常")

            # 判定是否应删除：干净 404，或 200 但无有效 duty rate
            # （trade-tariff 对废止编码常返回 200 + 无税率页面，而非 404；
            #   典型如 9403208000 重定向到 /subheadings/，parse 后 rate 空）
            # 任一地仍有有效数据则保留；抓取错误保守保留（下次重试）
            if should_delete_code(uk_data_parsed, uk_error, ni_data_parsed, ni_error):
                logger.info(f"商品编码 {code} 已移除（404 或无有效税率），删除记录")
                # 先清理error记录（防止循环操作）
                self.db.clear_scrape_error(code)
                # 然后删除记录
                self.db.delete_tariff(code)
                return {
                    'overall_success': True,
                    'uk_success': False,
                    'ni_success': False,
                    'uk_updated': False,
                    'ni_updated': False,
                    'message': f'商品编码 {code} 已不存在，已删除记录',
                    'old_data': old_data,
                    'new_data': None,
                    'updated': False
                }

            uk_success = uk_data_parsed is not None
            ni_success = ni_data_parsed is not None

            # 独立处理每个地区的数据更新
            update_data = {}
            uk_updated = False
            ni_updated = False
            changed_parts = []
            status_messages = []

            # 处理英国数据
            if uk_success:
                new_uk_rate = uk_data_parsed.get('rate', '')
                new_description = uk_data_parsed.get('description', '')

                # 检查英国税率变化
                old_uk_rate = old_data.get('rate', '') or ''
                new_uk_rate_clean = new_uk_rate.strip().lower() if new_uk_rate else ''
                old_uk_rate_clean = old_uk_rate.strip().lower() if old_uk_rate else ''

                uk_rate_changed = new_uk_rate_clean != old_uk_rate_clean
                desc_changed = new_description and new_description != old_data.get('description', '')

                if uk_rate_changed:
                    update_data['rate'] = new_uk_rate
                    uk_updated = True
                    changed_parts.append('英国税率')

                if desc_changed:
                    update_data['description'] = new_description
                    uk_updated = True
                    changed_parts.append('描述')

                # 记录状态
                if uk_rate_changed or desc_changed:
                    status_messages.append("英国数据已更新")
                else:
                    status_messages.append("英国数据无变化")
            else:
                status_messages.append(f"英国数据获取失败: {uk_error}")

            # 处理北爱尔兰数据
            if ni_success:
                new_ni_rate = ni_data_parsed.get('rate', '')

                # 检查北爱尔兰税率变化
                old_ni_rate = old_data.get('north_ireland_rate', '') or ''
                new_ni_rate_clean = new_ni_rate.strip().lower() if new_ni_rate else ''
                old_ni_rate_clean = old_ni_rate.strip().lower() if old_ni_rate else ''

                ni_rate_changed = new_ni_rate_clean != old_ni_rate_clean

                if ni_rate_changed:
                    update_data['north_ireland_rate'] = new_ni_rate
                    ni_updated = True
                    changed_parts.append('北爱尔兰税率')
                    status_messages.append("北爱尔兰数据已更新")
                else:
                    status_messages.append("北爱尔兰数据无变化")
            elif ni_url:  # 提供了URL但获取失败
                status_messages.append(f"北爱尔兰数据获取失败: {ni_error}")
            else:
                status_messages.append("北爱尔兰数据：未提供URL")

            # 执行数据库更新（如果有任何变化）
            any_updated = uk_updated or ni_updated
            if any_updated:
                self.db.update_tariff(code=code, **update_data)
                updated_data = self.db.get_tariff(code)
            else:
                updated_data = old_data

            # 判断总体成功状态（至少一个地区成功获取数据就算成功）
            overall_success = uk_success or ni_success

            # 如果没有成功获取任何有效数据，记录错误
            if not overall_success:
                error_message = f"❌ 更新失败 | {' | '.join(status_messages)}"
                try:
                    self.db.add_scrape_error(code, error_message)
                except Exception as db_error:
                    logger.error(f"保存错误记录到数据库失败 {code}: {str(db_error)}")

            # 构建最终消息
            if any_updated:
                if len(changed_parts) > 0:
                    change_msg = '、'.join(changed_parts)
                    full_message = f'✅ 成功更新（{change_msg}）| {" | ".join(status_messages)}'
                else:
                    full_message = f'ℹ️ 数据无变化 | {" | ".join(status_messages)}'
            else:
                if overall_success:
                    full_message = f'ℹ️ 数据无变化 | {" | ".join(status_messages)}'
                else:
                    full_message = f'❌ 更新失败 | {" | ".join(status_messages)}'

            return {
                'overall_success': overall_success,
                'uk_success': uk_success,
                'ni_success': ni_success,
                'uk_updated': uk_updated,
                'ni_updated': ni_updated,
                'message': full_message,
                'old_data': old_data,
                'new_data': updated_data,
                'updated': any_updated  # 保持向后兼容
            }

        except Exception as e:
            logger.error(f"自动更新失败: {str(e)}")
            return {
                'overall_success': False,
                'uk_success': False,
                'ni_success': False,
                'uk_updated': False,
                'ni_updated': False,
                'message': f'自动更新失败: {str(e)}',
                'old_data': None,
                'new_data': None,
                'updated': False
            }

    def auto_update_single_sync(self, code: str, uk_url: str, ni_url: str = None) -> Dict:
        """同步版本的 auto_update_single 方法（用于向后兼容）"""
        try:
            # 尝试获取当前事件循环
            try:
                loop = asyncio.get_running_loop()
                # 如果已经在异步环境中，使用 asyncio.create_task
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor() as executor:
                    future = executor.submit(asyncio.run, self.auto_update_single(code, uk_url, ni_url))
                    return future.result()
            except RuntimeError:
                # 没有运行的事件循环，直接使用 asyncio.run
                return asyncio.run(self.auto_update_single(code, uk_url, ni_url))
        except Exception as e:
            logger.error(f"同步自动更新失败: {str(e)}")

            # 将错误记录保存到数据库
            try:
                self.db.add_scrape_error(code, f"同步自动更新失败: {str(e)}")
            except Exception as db_error:
                logger.error(f"保存错误记录到数据库失败 {code}: {str(db_error)}")

            return {
                'overall_success': False,
                'uk_success': False,
                'ni_success': False,
                'uk_updated': False,
                'ni_updated': False,
                'message': f'同步自动更新失败: {str(e)}',
                'old_data': None,
                'new_data': None,
                'updated': False
            }

    def _create_temp_parser(self):
        """创建临时的解析器实例，避免修改全局状态"""
        # 创建一个新的解析器实例，共享配置但使用独立的状态
        temp_parser = TariffScraper.__new__(TariffScraper)
        temp_parser.base_url = self.base_url
        temp_parser.browse_url = self.browse_url
        temp_parser.headers = self.headers
        temp_parser.timeout = self.timeout
        temp_parser.max_retries = self.max_retries
        temp_parser.db = self.db
        # 关键：使用空的existing_codes集合，允许解析已存在的记录
        temp_parser.existing_codes = set()
        temp_parser.visited_urls = set()
        return temp_parser

class BatchUpdateManager:
    """批量更新管理器 - 支持批量更新所有关税数据"""

    def __init__(self, progress_callback=None, status_callback=None):
        """初始化批量更新管理器

        Args:
            progress_callback: 进度回调函数 (completed, total, message)
            status_callback: 状态回调函数 (message)
"""
        self.scraper = TariffScraper()
        self.db = TariffDB()
        self.progress_callback = progress_callback
        self.status_callback = status_callback
# 控制状态
        self.is_paused = False
        self.is_cancelled = False
        self.is_running = False

        # 统计信息
        self.stats = {
            'total': 0,
            'completed': 0,
            'successful': 0,
            'failed': 0,
            'skipped': 0,
            'uk_updated': 0,
            'ni_updated': 0,
            'start_time': None,
            'errors': [],
            # ✅ 添加集合记录实际更新的商品编码（避免重复计数）
            'uk_updated_codes': set(),
            'ni_updated_codes': set(),
            'updated_codes': set()  # 所有更新的商品编码（去重）
        }

        logger.info("BatchUpdateManager 初始化完成")

    def _update_progress(self, completed, total, message=""):
        """更新进度"""
        if self.progress_callback:
            self.progress_callback(completed, total, message)

        # 更新统计信息
        self.stats['completed'] = completed
        self.stats['total'] = total

    def _update_status(self, message):
        """更新状态"""
        if self.status_callback:
            self.status_callback(message)
        logger.info(f"批量更新状态: {message}")

    def _check_control_state(self):
        """检查控制状态"""
        if self.is_cancelled:
            raise InterruptedError("用户取消了批量更新")

        while self.is_paused and not self.is_cancelled:
            import time
            time.sleep(0.5)  # 暂停时等待

        if self.is_cancelled:
            raise InterruptedError("用户取消了批量更新")

    async def _process_batch(self, tariff_batch, update_uk=True, update_ni=True):
        """处理一批关税数据更新

        Args:
            tariff_batch: 一批关税数据列表
            update_uk: 是否更新英国数据
            update_ni: 是否更新北爱尔兰数据

        Returns:
            处理结果统计
        """
        batch_results = []

        # 创建信号量控制并发数
        semaphore = asyncio.Semaphore(20)  # 最多20个并发请求（提升并发性能）

        async def process_single_tariff(tariff):
            async with semaphore:
                try:
                    self._check_control_state()

                    code = tariff['code']
                    uk_url = tariff.get('url', '')
                    ni_url = tariff.get('north_ireland_url', '')

                    # 如果需要更新北爱尔兰数据且 URL 为空，自动生成
                    if update_ni and not ni_url:
                        ni_url = f"https://www.trade-tariff.service.gov.uk/xi/commodities/{code}"
                        logger.debug(f"为商品 {code} 自动生成北爱尔兰 URL")

                    # 检查是否需要更新
                    if not update_uk:
                        uk_url = None
                    if not update_ni:
                        ni_url = None

                    if not uk_url and not ni_url:
                        return {'code': code, 'status': 'skipped', 'reason': '无可用URL'}

                    # 执行单个更新
                    result = await self.scraper.auto_update_single(code, uk_url, ni_url)

                    success = result.get('overall_success', False)

                    # 如果更新失败，将错误记录保存到数据库
                    if not success:
                        error_message = result.get('message', '未知错误')
                        try:
                            self.db.add_scrape_error(code, f"自动更新失败: {error_message}")
                        except Exception as db_error:
                            logger.error(f"保存错误记录到数据库失败 {code}: {str(db_error)}")

                    return {
                        'code': code,
                        'status': 'success' if success else 'failed',
                        'result': result
                    }

                except InterruptedError:
                    raise
                except Exception as e:
                    logger.error(f"处理商品 {tariff.get('code', 'unknown')} 失败: {str(e)}")

                    # 将错误记录保存到数据库
                    code = tariff.get('code', 'unknown')
                    if code != 'unknown':
                        try:
                            self.db.add_scrape_error(code, f"批量更新失败: {str(e)}")
                        except Exception as db_error:
                            logger.error(f"保存错误记录到数据库失败 {code}: {str(db_error)}")

                    return {
                        'code': code,
                        'status': 'failed',
                        'reason': str(e)
                    }

        # 并发处理批次
        try:
            batch_results = await asyncio.gather(
                *[process_single_tariff(tariff) for tariff in tariff_batch],
                return_exceptions=True
            )
        except Exception as e:
            logger.error(f"批次处理失败: {str(e)}")
            raise

        # 统计结果
        batch_stats = {
            'successful': 0,
            'failed': 0,
            'skipped': 0,
            'uk_updated': 0,
            'ni_updated': 0,
            'errors': [],
            # ✅ 添加集合记录实际更新的商品编码
            'uk_updated_codes': set(),
            'ni_updated_codes': set(),
            'updated_codes': set()
        }

        for result in batch_results:
            if isinstance(result, Exception):
                batch_stats['failed'] += 1
                batch_stats['errors'].append(str(result))
                continue

            if result['status'] == 'success':
                batch_stats['successful'] += 1
                update_result = result.get('result', {})
                code = result.get('code')

                # ✅ 记录更新的商品编码到集合（避免重复计数）
                if update_result.get('uk_updated', False):
                    batch_stats['uk_updated'] += 1
                    if code:
                        batch_stats['uk_updated_codes'].add(code)
                        batch_stats['updated_codes'].add(code)

                if update_result.get('ni_updated', False):
                    batch_stats['ni_updated'] += 1
                    if code:
                        batch_stats['ni_updated_codes'].add(code)
                        batch_stats['updated_codes'].add(code)
            elif result['status'] == 'skipped':
                batch_stats['skipped'] += 1
            else:
                batch_stats['failed'] += 1
                if 'reason' in result:
                    batch_stats['errors'].append(f"{result['code']}: {result['reason']}")

        return batch_stats

    async def update_all_tariffs(self,
                               update_uk=True,
                               update_ni=True,
                               batch_size=100,
                               delay_between_batches=0.2,
                               filter_func=None):
        """批量更新所有关税数据

        Args:
            update_uk: 是否更新英国税率数据
            update_ni: 是否更新北爱尔兰税率数据
            batch_size: 每批处理的记录数
            delay_between_batches: 批次之间的延迟时间（秒）
            filter_func: 过滤函数，用于筛选需要更新的记录

        Returns:
            更新结果统计
        """
        if self.is_running:
            raise RuntimeError("批量更新已在运行中")

        self.is_running = True
        self.is_paused = False
        self.is_cancelled = False

        # 重置统计信息
        self.stats = {
            'total': 0,
            'completed': 0,
            'successful': 0,
            'failed': 0,
            'skipped': 0,
            'uk_updated': 0,
            'ni_updated': 0,
            'start_time': None,
            'errors': [],
            # ✅ 添加集合记录实际更新的商品编码（避免重复计数）
            'uk_updated_codes': set(),
            'ni_updated_codes': set(),
            'updated_codes': set()
        }

        try:
            import time
            start_time = time.time()
            self.stats['start_time'] = start_time

            self._update_status("正在获取关税数据列表...")

            # 获取所有关税数据
            all_tariffs = self.db.get_all_tariffs()

            # 应用过滤器
            if filter_func:
                all_tariffs = [t for t in all_tariffs if filter_func(t)]
                self._update_status(f"筛选后需要更新 {len(all_tariffs)} 条记录")

            total_count = len(all_tariffs)
            self.stats['total'] = total_count

            if total_count == 0:
                self._update_status("没有需要更新的记录")
                return self.stats

            self._update_status(f"开始批量更新 {total_count} 条记录...")
            self._update_progress(0, total_count, "准备开始...")

            # 分批处理
            for i in range(0, total_count, batch_size):
                self._check_control_state()

                # 获取当前批次
                batch = all_tariffs[i:i + batch_size]
                batch_num = i // batch_size + 1
                total_batches = (total_count + batch_size - 1) // batch_size

                self._update_status(f"正在处理第 {batch_num}/{total_batches} 批 ({len(batch)} 条记录)...")

                # 处理批次
                batch_stats = await self._process_batch(batch, update_uk, update_ni)

                # 更新统计信息
                self.stats['successful'] += batch_stats['successful']
                self.stats['failed'] += batch_stats['failed']
                self.stats['skipped'] += batch_stats['skipped']
                self.stats['uk_updated'] += batch_stats['uk_updated']
                self.stats['ni_updated'] += batch_stats['ni_updated']
                self.stats['errors'].extend(batch_stats['errors'])

                # ✅ 合并商品编码集合（避免重复计数）
                self.stats['uk_updated_codes'].update(batch_stats.get('uk_updated_codes', set()))
                self.stats['ni_updated_codes'].update(batch_stats.get('ni_updated_codes', set()))
                self.stats['updated_codes'].update(batch_stats.get('updated_codes', set()))

                # 更新进度
                completed = min(i + batch_size, total_count)
                progress_percent = (completed / total_count) * 100

                elapsed_time = time.time() - start_time
                if completed > 0:
                    rate = completed / elapsed_time
                    remaining = total_count - completed
                    eta_seconds = remaining / rate if rate > 0 else 0
                    eta_minutes = eta_seconds / 60

                    progress_msg = f"处理速度: {rate:.1f}条/分钟, 预计剩余: {eta_minutes:.0f}分钟"
                else:
                    progress_msg = "正在计算处理速度..."

                self._update_progress(completed, total_count, progress_msg)

                # 批次间延迟
                if i + batch_size < total_count and delay_between_batches > 0:
                    self._update_status(f"批次间等待 {delay_between_batches} 秒...")
                    await asyncio.sleep(delay_between_batches)

            # 完成统计
            total_time = time.time() - start_time
            total_minutes = total_time / 60

            self._update_status(f"批量更新完成！总用时: {total_minutes:.1f}分钟")
            self._update_progress(total_count, total_count, f"更新完成 - 成功: {self.stats['successful']}, 失败: {self.stats['failed']}, 跳过: {self.stats['skipped']}")

            # 末尾重试 transient 失败（网络抖动等可恢复失败）
            await self.scraper.retry_transient_failures()
            return self.stats

        except InterruptedError:
            self._update_status("批量更新已被用户取消")
            raise
        except Exception as e:
            self._update_status(f"批量更新过程中发生错误: {str(e)}")
            logger.error(f"批量更新失败: {str(e)}")
            raise
        finally:
            self.is_running = False

    def pause(self):
        """暂停批量更新"""
        self.is_paused = True
        self._update_status("批量更新已暂停")

    def resume(self):
        """恢复批量更新"""
        self.is_paused = False
        self._update_status("批量更新已恢复")

    def cancel(self):
        """取消批量更新"""
        self.is_cancelled = True
        self.is_paused = False
        self._update_status("正在取消批量更新...")

    def clear_error_records(self, codes=None):
        """清理错误记录

        Args:
            codes: 要清理的商品编码列表，如果为None则清理所有错误记录
        """
        try:
            if codes is None:
                # 清理所有错误记录
                with self.db.conn:
                    self.db.conn.execute("DELETE FROM scrape_errors")
                logger.info("已清理所有错误记录")
            else:
                # 清理指定编码的错误记录
                if not codes:
                    return

                placeholders = ','.join(['?' for _ in codes])
                with self.db.conn:
                    self.db.conn.execute(
                        f"DELETE FROM scrape_errors WHERE code IN ({placeholders})",
                        codes
                    )
                logger.info(f"已清理 {len(codes)} 个错误记录")
        except Exception as e:
            logger.error(f"清理错误记录失败: {str(e)}")
            raise

    def get_error_records_count(self):
        """获取错误记录数量"""
        try:
            cur = self.db.conn.execute("SELECT COUNT(*) FROM scrape_errors")
            return cur.fetchone()[0]
        except Exception as e:
            logger.error(f"获取错误记录数量失败: {str(e)}")
            return 0

    def get_stats(self):
        """获取当前统计信息"""
        stats = self.stats.copy()

        # ✅ 将集合转换为列表（用于JSON序列化）并添加去重后的实际更新记录数
        if 'uk_updated_codes' in stats:
            stats['uk_updated_codes'] = list(stats['uk_updated_codes'])
        if 'ni_updated_codes' in stats:
            stats['ni_updated_codes'] = list(stats['ni_updated_codes'])
        if 'updated_codes' in stats:
            stats['updated_codes'] = list(stats['updated_codes'])
            # 添加去重后的实际更新记录数（这才是真实的修改记录数）
            stats['modified_records'] = len(stats['updated_codes'])

        # 计算处理速度
        if stats['start_time'] and stats['completed'] > 0:
            import time
            elapsed_time = time.time() - stats['start_time']
            stats['rate'] = stats['completed'] / elapsed_time
            stats['elapsed_time'] = elapsed_time

            # 估算剩余时间
            if stats['rate'] > 0 and stats['total'] > stats['completed']:
                remaining = stats['total'] - stats['completed']
                stats['eta'] = remaining / stats['rate']

        return stats

async def main():
    scraper = TariffScraper()
    tariffs = await scraper.scrape_tariffs()
    scraper.save_to_db(tariffs)

if __name__ == "__main__":
    asyncio.run(main())