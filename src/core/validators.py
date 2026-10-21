"""
数据验证器模块 - 确保关税数据准确性

此模块提供数据验证功能，用于检测和防止英国/北爱尔兰数据混淆问题。

使用方式：
    from src.core.validators import TariffValidator

    validator = TariffValidator()
    is_valid, message = validator.validate_url(url, expected_type='uk')
"""

import re
import logging
from typing import Tuple, List, Dict, Optional

logger = logging.getLogger(__name__)


class TariffValidator:
    """关税数据验证器"""

    # URL 模式定义
    UK_COMMODITY_PATTERN = re.compile(r'^https://www\.trade-tariff\.service\.gov\.uk/commodities/(\d+)$')
    NI_COMMODITY_PATTERN = re.compile(r'^https://www\.trade-tariff\.service\.gov\.uk/xi/commodities/(\d+)$')

    @classmethod
    def validate_url(cls, url: str, expected_type: str = 'uk') -> Tuple[bool, str]:
        """验证URL格式是否正确

        Args:
            url: 要验证的URL
            expected_type: 期望的URL类型 ('uk' 或 'ni')

        Returns:
            Tuple[bool, str]: (是否有效, 错误信息或'OK')
        """
        if not url:
            return False, "URL为空"

        if expected_type == 'uk':
            # 英国URL不应包含 /xi/commodities/
            if '/xi/commodities/' in url:
                return False, "英国URL错误包含北爱尔兰路径 (/xi/commodities/)"

            if not cls.UK_COMMODITY_PATTERN.match(url):
                # 允许部分匹配（可能有查询参数等）
                if '/commodities/' not in url:
                    return False, f"无效的英国商品URL格式: {url}"

            return True, "OK"

        elif expected_type == 'ni':
            # 北爱尔兰URL应包含 /xi/commodities/
            if '/xi/commodities/' not in url:
                return False, "北爱尔兰URL缺少 /xi/commodities/ 路径"

            if not cls.NI_COMMODITY_PATTERN.match(url):
                # 允许部分匹配
                if '/xi/commodities/' not in url:
                    return False, f"无效的北爱尔兰商品URL格式: {url}"

            return True, "OK"

        else:
            return False, f"未知的URL类型: {expected_type}"

    @classmethod
    def validate_tariff_record(cls, record: Dict) -> Tuple[bool, List[str]]:
        """验证单条关税记录

        Args:
            record: 关税记录字典

        Returns:
            Tuple[bool, List[str]]: (是否有效, 错误列表)
        """
        errors = []

        # 验证必填字段
        if not record.get('code'):
            errors.append("缺少商品编码")

        # 验证商品编码格式（应为纯数字）
        code = record.get('code', '')
        if code and not code.isdigit():
            errors.append(f"商品编码包含非数字字符: {code}")

        # 验证英国URL
        uk_url = record.get('url', '')
        if uk_url:
            is_valid, msg = cls.validate_url(uk_url, 'uk')
            if not is_valid:
                errors.append(f"英国URL验证失败: {msg}")

        # 验证北爱尔兰URL
        ni_url = record.get('north_ireland_url', '')
        if ni_url:
            is_valid, msg = cls.validate_url(ni_url, 'ni')
            if not is_valid:
                errors.append(f"北爱尔兰URL验证失败: {msg}")

        # 检查数据混淆：英国URL字段不应包含北爱尔兰路径
        if uk_url and '/xi/commodities/' in uk_url:
            errors.append("数据混淆检测: 英国URL字段包含北爱尔兰路径")

        return len(errors) == 0, errors

    @classmethod
    def validate_batch(cls, records: List[Dict]) -> Dict:
        """批量验证关税记录

        Args:
            records: 关税记录列表

        Returns:
            Dict: 验证结果统计
        """
        results = {
            'total': len(records),
            'valid': 0,
            'invalid': 0,
            'errors': [],
            'error_codes': []
        }

        for record in records:
            is_valid, errors = cls.validate_tariff_record(record)
            if is_valid:
                results['valid'] += 1
            else:
                results['invalid'] += 1
                code = record.get('code', 'unknown')
                results['error_codes'].append(code)
                for error in errors:
                    results['errors'].append(f"{code}: {error}")

        results['validation_rate'] = (
            results['valid'] / results['total'] * 100
            if results['total'] > 0 else 0
        )

        return results

    @classmethod
    def detect_data_mixing(cls, records: List[Dict]) -> List[Dict]:
        """检测数据混淆问题

        专门检测英国数据被错误保存为北爱尔兰数据的情况

        Args:
            records: 关税记录列表

        Returns:
            List[Dict]: 有问题的记录列表
        """
        problematic_records = []

        for record in records:
            issues = []

            # 检查1: 英国URL包含北爱尔兰路径
            uk_url = record.get('url', '')
            if uk_url and '/xi/commodities/' in uk_url:
                issues.append("英国URL字段包含北爱尔兰路径")

            # 检查2: 北爱尔兰URL不包含/xi/路径
            ni_url = record.get('north_ireland_url', '')
            if ni_url and '/xi/' not in ni_url:
                issues.append("北爱尔兰URL字段不包含/xi/路径")

            if issues:
                problematic_records.append({
                    'code': record.get('code'),
                    'url': uk_url,
                    'north_ireland_url': ni_url,
                    'issues': issues
                })

        return problematic_records


def validate_database(db_path: str) -> Dict:
    """验证整个数据库的数据质量

    Args:
        db_path: 数据库文件路径

    Returns:
        Dict: 验证结果
    """
    import sqlite3
    import os

    if not os.path.exists(db_path):
        return {'success': False, 'error': '数据库文件不存在'}

    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # 获取所有记录
        cursor.execute("""
            SELECT code, url, north_ireland_url, rate, north_ireland_rate
            FROM tariffs
        """)

        records = []
        for row in cursor.fetchall():
            records.append({
                'code': row[0],
                'url': row[1],
                'north_ireland_url': row[2],
                'rate': row[3],
                'north_ireland_rate': row[4]
            })

        conn.close()

        # 执行验证
        validator = TariffValidator()
        batch_results = validator.validate_batch(records)
        mixing_issues = validator.detect_data_mixing(records)

        return {
            'success': True,
            'total_records': len(records),
            'validation_results': batch_results,
            'data_mixing_issues': mixing_issues,
            'has_mixing_issues': len(mixing_issues) > 0
        }

    except Exception as e:
        logger.error(f"数据库验证失败: {str(e)}")
        return {'success': False, 'error': str(e)}


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("用法: python validators.py <数据库路径>")
        sys.exit(1)

    db_path = sys.argv[1]
    results = validate_database(db_path)

    if not results['success']:
        print(f"❌ 验证失败: {results.get('error')}")
        sys.exit(1)

    print(f"\n📊 数据库验证报告")
    print(f"{'='*50}")
    print(f"总记录数: {results['total_records']}")

    validation = results['validation_results']
    print(f"\n✅ 有效记录: {validation['valid']}")
    print(f"❌ 无效记录: {validation['invalid']}")
    print(f"📈 验证通过率: {validation['validation_rate']:.1f}%")

    mixing = results['data_mixing_issues']
    if mixing:
        print(f"\n⚠️ 发现 {len(mixing)} 条数据混淆问题:")
        for issue in mixing[:10]:  # 只显示前10条
            print(f"  - {issue['code']}: {', '.join(issue['issues'])}")
        if len(mixing) > 10:
            print(f"  ... 还有 {len(mixing) - 10} 条")
    else:
        print(f"\n✅ 未发现数据混淆问题")

    sys.exit(0 if validation['invalid'] == 0 and not mixing else 1)
