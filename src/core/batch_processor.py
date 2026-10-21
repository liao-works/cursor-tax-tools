import pandas as pd
import logging
from datetime import datetime
import os
import sys
from typing import List, Dict, Optional
import queue

# 添加项目根目录到 Python 路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from src.api.tariff_api import TariffAPI

logger = logging.getLogger(__name__)


def get_writable_output_path(output_dir: str = "output") -> str:
    """获取可写的输出目录路径

    Args:
        output_dir: 输出目录名称

    Returns:
        str: 可写的输出目录绝对路径
    """
    # 如果是绝对路径，直接返回
    if os.path.isabs(output_dir):
        return output_dir

    # 获取用户可写目录（与数据库使用相同的目录结构）
    if os.name == 'nt':  # Windows
        user_dir = os.path.expanduser("~")
        app_dir = os.path.join(user_dir, "uk-tax-tools")
    else:  # macOS/Linux
        user_dir = os.path.expanduser("~")
        app_dir = os.path.join(user_dir, ".uk-tax-tools")

    # 构建输出目录路径
    writable_path = os.path.join(app_dir, output_dir)

    # 确保目录存在
    os.makedirs(writable_path, exist_ok=True)

    logger.info(f"📁 输出目录: {writable_path}")

    return writable_path


class BatchProcessor:
    def __init__(self, output_dir: str = "output"):
        self.api = TariffAPI()
        # 确保使用可写的输出目录
        self.output_dir = get_writable_output_path(output_dir)
        self.progress = 0
        self.total = 0
        self.status = "idle"
        self.log_queue = queue.Queue()
        self.current_file = None

    def process_file(self, file_path: str) -> Optional[str]:
        """处理Excel文件

        Args:
            file_path: Excel文件路径

        Returns:
            输出文件路径，处理失败返回None
        """
        try:
            # 读取Excel文件，将code列作为文本处理
            df = pd.read_excel(file_path, dtype={'code': str})
            if 'code' not in df.columns:
                self.log_queue.put("错误：Excel文件必须包含'code'列")
                return None

            self.total = len(df)
            self.progress = 0
            self.status = "processing"
            self.current_file = os.path.basename(file_path)

            # 处理每一行
            results = []
            for index, row in df.iterrows():
                # 获取code并且去掉空格
                code = str(row['code']).strip()

                # 使用模糊搜索找到最佳匹配
                matches = self.api.fuzzy_search(code, limit=1)
                if matches:
                    best_match = matches[0]
                    results.append({
                        'code': code,  # 使用原始code
                        'rate': best_match['rate'],
                        'ni_rate': best_match['north_ireland_rate'],
                        '相似度': f"{best_match['similarity']*100:.1f}%"
                    })
                else:
                    results.append({
                        'code': code,  # 使用原始code
                        'rate': '',
                        'ni_rate': '',
                        '相似度': "0.0%"
                    })

                self.progress = (index + 1) / self.total
                self.log_queue.put(f"处理进度: {self.progress*100:.1f}% - 正在处理: {code}")

            # 创建结果DataFrame
            result_df = pd.DataFrame(results)

            # 生成输出文件名
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            output_file = os.path.join(
                self.output_dir,
                f"processed_{timestamp}_{os.path.basename(file_path)}"
            )

            # 保存结果，确保code列作为文本保存
            with pd.ExcelWriter(output_file, engine='openpyxl') as writer:
                result_df.to_excel(writer, index=False)
                # 设置code列为文本格式
                worksheet = writer.sheets['Sheet1']
                for cell in worksheet['A']:
                    cell.number_format = '@'

            self.status = "completed"
            self.log_queue.put(f"处理完成，结果已保存到: {output_file}")

            return output_file

        except Exception as e:
            self.status = "error"
            error_msg = f"处理文件失败: {str(e)}"
            logger.error(error_msg)
            self.log_queue.put(error_msg)
            return None

    def get_history_files(self) -> List[Dict]:
        """获取历史处理文件列表"""
        try:
            files = []
            for file in os.listdir(self.output_dir):
                if file.startswith("processed_"):
                    file_path = os.path.join(self.output_dir, file)
                    files.append({
                        'filename': file,
                        'path': file_path,
                        'time': datetime.fromtimestamp(
                            os.path.getctime(file_path)
                        ).strftime('%Y-%m-%d %H:%M:%S'),
                        'size': f"{os.path.getsize(file_path)/1024:.1f}KB"
                    })
            return sorted(files, key=lambda x: x['time'], reverse=True)
        except Exception as e:
            logger.error(f"获取历史文件失败: {str(e)}")
            return []

    def get_progress(self) -> Dict:
        """获取当前处理进度"""
        return {
            'progress': self.progress,
            'status': self.status,
            'current_file': self.current_file
        }

    def get_logs(self) -> List[str]:
        """获取所有待处理的日志"""
        logs = []
        while not self.log_queue.empty():
            logs.append(self.log_queue.get())
        return logs