import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from typing import Dict
import logging
from tariff_api import TariffAPI
from tariff_db import TariffDB
from src.clients.smart_update_client import SmartUpdateChecker
from src.clients.data_update_notifier import (
    DEFAULT_METADATA_URL, configured_metadata_url, release_notification_key,
    check_delay_ms, RETRY_INTERVAL_MS,
)
import queue
import threading
import asyncio
import os
import sys
from datetime import datetime
from src.gui.batch_frame import BatchProcessFrame

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)


def resource_path(relative_path: str) -> str:
    """获取资源文件的绝对路径

    在开发环境返回相对路径，在 PyInstaller 打包后返回正确的资源路径

    Args:
        relative_path: 相对路径

    Returns:
        str: 资源文件的绝对路径
    """
    try:
        # PyInstaller 创建的临时文件夹路径
        base_path = sys._MEIPASS
    except AttributeError:
        # 开发环境，使用当前目录
        base_path = os.path.abspath(".")

    return os.path.join(base_path, relative_path)


import json


# Persistent settings live in the user profile, never in a read-only install
# directory. Existing portable installations are migrated on first save.
_USER_SETTINGS_DIR = (
    os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), "TariffTools")
    if sys.platform == "win32"
    else os.path.join(os.path.expanduser("~"), ".tarifftools")
)
CONFIG_FILE = os.path.join(_USER_SETTINGS_DIR, "gui_config.json")
LEGACY_CONFIG_FILE = "gui_config.json"


def load_config() -> dict:
    """Read persisted desktop settings; migrate only the retired metadata URL."""
    defaults = {"db_path": "tariffs.db", "metadata_url": DEFAULT_METADATA_URL}
    for path in (CONFIG_FILE, LEGACY_CONFIG_FILE):
        try:
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as stream:
                    saved = json.load(stream)
                if not isinstance(saved, dict):
                    raise ValueError("GUI 配置内容必须是对象")
                config = dict(defaults)
                config.update(saved)
                config["metadata_url"] = configured_metadata_url(config.get("metadata_url"))
                return config
        except (OSError, ValueError, TypeError) as exc:
            logger.warning("配置文件 %s 读取失败：%s", path, exc)
    return defaults


def save_config(config: dict) -> bool:
    """Atomically preserve last-notified release across application restarts."""
    try:
        os.makedirs(_USER_SETTINGS_DIR, exist_ok=True)
        tmp_path = CONFIG_FILE + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as stream:
            json.dump(config, stream, ensure_ascii=False, indent=2)
        os.replace(tmp_path, CONFIG_FILE)
        return True
    except (OSError, TypeError, ValueError) as exc:
        logger.warning("保存桌面配置失败：%s", exc)
        return False


class UpdateDialog:
    """数据更新对话框"""

    def __init__(self, parent, tariff_data: dict, on_save_callback=None):
        self.parent = parent
        self.tariff_data = tariff_data
        self.on_save_callback = on_save_callback
        self.result = None

        # 创建对话框窗口
        self.dialog = tk.Toplevel(parent)
        self.dialog.title("更新关税数据")
        self.dialog.geometry("500x400")
        self.dialog.resizable(False, False)

        # 设置模态
        self.dialog.transient(parent)
        self.dialog.grab_set()

        # 居中显示
        self.dialog.geometry("+%d+%d" % (
            parent.winfo_rootx() + 50,
            parent.winfo_rooty() + 50
        ))

        self.setup_ui()

    def setup_ui(self):
        """设置对话框UI"""
        # 主框架
        main_frame = ttk.Frame(self.dialog, padding="20")
        main_frame.pack(fill=tk.BOTH, expand=True)

        # 商品编码（只读）
        ttk.Label(main_frame, text="商品编码:").grid(row=0, column=0, sticky=tk.W, pady=5)
        code_label = ttk.Label(main_frame, text=self.tariff_data['code'], font=('TkDefaultFont', 9, 'bold'))
        code_label.grid(row=0, column=1, sticky=tk.W, padx=10, pady=5)

        # 商品描述
        ttk.Label(main_frame, text="商品描述:").grid(row=1, column=0, sticky=tk.NW, pady=5)
        self.description_text = tk.Text(main_frame, width=40, height=4)
        self.description_text.grid(row=1, column=1, sticky=tk.W, padx=10, pady=5)
        self.description_text.insert('1.0', self.tariff_data.get('description', ''))

        # 英国税率
        ttk.Label(main_frame, text="英国税率:").grid(row=2, column=0, sticky=tk.W, pady=5)
        self.rate_var = tk.StringVar(value=self.tariff_data.get('rate', ''))
        rate_entry = ttk.Entry(main_frame, textvariable=self.rate_var, width=30)
        rate_entry.grid(row=2, column=1, sticky=tk.W, padx=10, pady=5)

        # 英国税率网址
        ttk.Label(main_frame, text="英国税率网址:").grid(row=3, column=0, sticky=tk.W, pady=5)
        self.url_var = tk.StringVar(value=self.tariff_data.get('url', ''))
        url_entry = ttk.Entry(main_frame, textvariable=self.url_var, width=30)
        url_entry.grid(row=3, column=1, sticky=tk.W, padx=10, pady=5)

        # 北爱尔兰税率
        ttk.Label(main_frame, text="北爱尔兰税率:").grid(row=4, column=0, sticky=tk.W, pady=5)
        self.ni_rate_var = tk.StringVar(value=self.tariff_data.get('north_ireland_rate', ''))
        ni_rate_entry = ttk.Entry(main_frame, textvariable=self.ni_rate_var, width=30)
        ni_rate_entry.grid(row=4, column=1, sticky=tk.W, padx=10, pady=5)

        # 北爱尔兰税率网址
        ttk.Label(main_frame, text="北爱尔兰税率网址:").grid(row=5, column=0, sticky=tk.W, pady=5)
        self.ni_url_var = tk.StringVar(value=self.tariff_data.get('north_ireland_url', ''))
        ni_url_entry = ttk.Entry(main_frame, textvariable=self.ni_url_var, width=30)
        ni_url_entry.grid(row=5, column=1, sticky=tk.W, padx=10, pady=5)

        # 按钮框架
        button_frame = ttk.Frame(main_frame)
        button_frame.grid(row=6, column=0, columnspan=2, pady=20)

        # 保存按钮
        save_btn = ttk.Button(button_frame, text="保存", command=self.save)
        save_btn.pack(side=tk.LEFT, padx=5)

        # 取消按钮
        cancel_btn = ttk.Button(button_frame, text="取消", command=self.cancel)
        cancel_btn.pack(side=tk.LEFT, padx=5)

    def save(self):
        """保存更新"""
        try:
            # 获取输入值
            description = self.description_text.get('1.0', tk.END).strip()
            rate = self.rate_var.get().strip()
            url = self.url_var.get().strip()
            ni_rate = self.ni_rate_var.get().strip()
            ni_url = self.ni_url_var.get().strip()

            # 构建结果
            self.result = {
                'code': self.tariff_data['code'],
                'description': description if description else None,
                'rate': rate if rate else None,
                'url': url if url else None,
                'north_ireland_rate': ni_rate if ni_rate else None,
                'north_ireland_url': ni_url if ni_url else None
            }

            # 关闭对话框
            self.dialog.destroy()

            # 触发回调
            if self.on_save_callback:
                self.on_save_callback(self.result)

        except Exception as e:
            logger.error(f"保存更新失败: {str(e)}")
            messagebox.showerror("错误", f"保存失败: {str(e)}")

    def cancel(self):
        """取消更新"""
        self.dialog.destroy()


class TariffGUI:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("关税查询工具")

        # ✅ 加载配置文件
        self.config = load_config()
        
        # ✅ 添加远程更新操作锁
        self.remote_update_lock = threading.Lock()
        self.remote_update_in_progress = False

        # ✅ 添加线程池管理（避免无限制创建线程）
        from concurrent.futures import ThreadPoolExecutor
        self.thread_pool = ThreadPoolExecutor(max_workers=3, thread_name_prefix="TariffGUI")

        self.setup_ui()
        self.setup_api()
        self.setup_queue()

        # ✅ 绑定窗口关闭事件
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)
        self._closing = False
        self._data_update_check_timer = None
        self._data_notification_window = None

        # Startup and once per 24h while this desktop application is running.
        # The network check runs in the existing background thread pool.
        self.root.after(500, self._load_initial_local_info)
        self._schedule_next_data_update_check(2000)

    def setup_ui(self):
        """设置UI界面"""
        # 创建标签页
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        # 单个查询标签页
        self.single_frame = ttk.Frame(self.notebook)
        self.notebook.add(self.single_frame, text="单个查询")
        self.setup_single_search()

        # 批量查询标签页
        self.batch_frame = BatchProcessFrame(self.notebook)
        self.notebook.add(self.batch_frame, text="批量查询")

        # 远程数据更新标签页
        self.remote_update_frame = ttk.Frame(self.notebook)
        self.notebook.add(self.remote_update_frame, text="远程数据更新")
        self.setup_remote_update()

    def setup_single_search(self):
        """设置单个查询界面"""
        # 搜索框架
        search_frame = ttk.LabelFrame(self.single_frame, text="搜索")
        search_frame.pack(fill=tk.X, padx=5, pady=5)

        # 搜索输入
        self.search_var = tk.StringVar()
        search_entry = ttk.Entry(
            search_frame,
            textvariable=self.search_var,
            width=40
        )
        search_entry.pack(side=tk.LEFT, padx=5, pady=5)
        search_entry.bind('<Return>', lambda e: self.search())

        # 模糊匹配选项
        self.fuzzy_var = tk.BooleanVar(value=True)
        fuzzy_check = ttk.Checkbutton(
            search_frame,
            text="模糊匹配",
            variable=self.fuzzy_var,
            command=self.on_fuzzy_changed
        )
        fuzzy_check.pack(side=tk.LEFT, padx=5, pady=5)

        # 搜索按钮
        self.search_btn = ttk.Button(
            search_frame,
            text="搜索",
            command=self.search
        )
        self.search_btn.pack(side=tk.LEFT, padx=5, pady=5)

        # 结果显示
        result_frame = ttk.LabelFrame(self.single_frame, text="搜索结果")
        result_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        # 创建表格（注意：表格不显示description，但数据中包含它）
        columns = ('编码', '税率', '网址', '北爱尔兰税率', '北爱尔兰网址', '相似度')
        self.result_tree = ttk.Treeview(
            result_frame,
            columns=columns,
            show='headings',
            height=10
        )

        # 设置列
        column_widths = {
            '编码': 120,
            '税率': 100,
            '网址': 150,
            '北爱尔兰税率': 100,
            '北爱尔兰网址': 150,
            '相似度': 80
        }

        for col in columns:
            self.result_tree.heading(col, text=col)
            self.result_tree.column(col, width=column_widths[col])

        # 添加滚动条
        scrollbar = ttk.Scrollbar(
            result_frame,
            orient=tk.VERTICAL,
            command=self.result_tree.yview
        )
        self.result_tree.configure(yscrollcommand=scrollbar.set)

        # 布局
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.result_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # 绑定双击事件
        self.result_tree.bind('<Double-1>', self.on_result_double_click)

        # 创建右键菜单
        self.context_menu = tk.Menu(self.root, tearoff=0)
        self.context_menu.add_command(label="复制英国税率", command=self.copy_uk_rate)
        self.context_menu.add_command(label="打开英国税率网址", command=self.open_uk_url)
        self.context_menu.add_separator()
        self.context_menu.add_command(label="复制北爱尔兰税率", command=self.copy_ni_rate)
        self.context_menu.add_command(label="打开北爱尔兰税率网址", command=self.open_ni_url)
        self.context_menu.add_separator()
        self.context_menu.add_command(label="更新数据", command=self.update_data)
        self.context_menu.add_command(label="自动更新", command=self.auto_update)

        # 绑定右键事件（同时支持 Windows 和 macOS）
        self.result_tree.bind('<Button-2>', self.show_context_menu)  # macOS 右键
        self.result_tree.bind('<Button-3>', self.show_context_menu)  # Windows 右键
        if self.root.tk.call('tk', 'windowingsystem') == 'aqua':  # macOS
            self.result_tree.bind('<Control-1>', self.show_context_menu)  # macOS Control+左键

        # 状态栏
        self.status_var = tk.StringVar(value="就绪")
        status_bar = ttk.Label(
            self.single_frame,
            textvariable=self.status_var,
            relief=tk.SUNKEN,
            anchor=tk.W
        )
        status_bar.pack(fill=tk.X, padx=5, pady=2)

    def setup_api(self):
        """设置API - 使用配置文件中的数据库路径"""
        db_path = self.config.get("db_path", "tariffs.db")
        logger.info(f"🔧 使用数据库路径: {db_path}")
        self.api = TariffAPI(db_path=db_path)
        self.db = TariffDB(db_path=db_path)

    def setup_queue(self):
        """设置消息队列和更新任务"""
        self.queue = queue.Queue()
        self.root.after(100, self.process_queue)

    def process_queue(self):
        """处理消息队列"""
        try:
            while True:
                func, args, kwargs = self.queue.get_nowait()
                func(*args, **kwargs)
                self.queue.task_done()
        except queue.Empty:
            pass
        finally:
            self.root.after(100, self.process_queue)

    def search(self):
        """执行搜索"""
        query = self.search_var.get().strip()
        if not query:
            return

        # 禁用搜索按钮
        self.search_btn.configure(state='disabled')
        self.status_var.set("搜索中...")

        # 清空现有结果
        for item in self.result_tree.get_children():
            self.result_tree.delete(item)

        # 在后台线程中执行搜索
        thread = threading.Thread(
            target=self._search,
            args=(query,),
            daemon=True
        )
        thread.start()

    def _search(self, query: str):
        """在后台线程中执行搜索"""
        try:
            if self.fuzzy_var.get():
                results = self.api.fuzzy_search(query)
                if results:
                    # 在主线程中更新UI
                    self.queue.put((self._update_results, (results,), {}))
                else:
                    self.queue.put((
                        self.status_var.set,
                        ("未找到匹配结果",),
                        {}
                    ))
            else:
                result = self.api.exact_search(query)
                if result:
                    # 在主线程中更新UI
                    self.queue.put((self._update_results, ([result],), {}))
                else:
                    self.queue.put((
                        self.status_var.set,
                        ("未找到匹配结果",),
                        {}
                    ))
        except Exception as e:
            logger.error(f"搜索失败: {str(e)}")
            self.queue.put((
                self.status_var.set,
                (f"搜索失败: {str(e)}",),
                {}
            ))
        finally:
            # 恢复搜索按钮
            self.queue.put((
                self.search_btn.configure,
                (),
                {'state': 'normal'}
            ))

    def _update_results(self, results: list):
        """更新搜索结果显示"""
        # 清空现有结果
        for item in self.result_tree.get_children():
            self.result_tree.delete(item)

        # 添加新结果
        for result in results:
            similarity = result.get('similarity', 1.0)
            self.result_tree.insert(
                '',
                'end',
                values=(
                    result['code'],
                    result.get('rate', ''),
                    result.get('url', ''),
                    result.get('north_ireland_rate', ''),
                    result.get('north_ireland_url', ''),
                    f"{similarity*100:.1f}%"
                )
            )

        self.status_var.set(f"找到 {len(results)} 条结果")

    def on_result_double_click(self, event):
        """处理结果双击事件"""
        # 获取点击的项目和列
        item = self.result_tree.selection()[0]
        column = self.result_tree.identify_column(event.x)
        col_num = int(column.replace('#', ''))

        # 获取值
        values = self.result_tree.item(item)['values']

        # 根据列号确定URL
        url = None
        if col_num == 3:  # 英国网址列
            url = values[2]  # 因为values是从0开始索引
        elif col_num == 5:  # 北爱尔兰网址列
            url = values[4]

        # 如果点击的是URL列且URL不为空，则打开浏览器
        if url and url.strip():
            import webbrowser
            webbrowser.open(url)

    def on_fuzzy_changed(self):
        """处理模糊匹配状态改变"""
        is_fuzzy = self.fuzzy_var.get()
        logger.debug(f"模糊匹配状态改变: {is_fuzzy}")

    def show_context_menu(self, event):
        """显示右键菜单"""
        # 获取点击的项目
        item = self.result_tree.identify_row(event.y)
        if item:
            # 选中该项
            self.result_tree.selection_set(item)
            # 显示菜单
            try:
                self.context_menu.tk_popup(event.x_root, event.y_root)
            finally:
                self.context_menu.grab_release()  # 释放菜单的事件捕获

    def copy_to_clipboard(self, text):
        """复制文本到剪贴板"""
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.status_var.set("已复制到剪贴板")

    def copy_uk_rate(self):
        """复制英国税率"""
        item = self.result_tree.selection()[0]
        values = self.result_tree.item(item)['values']
        rate = values[1]  # 英国税率在第2列
        if rate:
            self.copy_to_clipboard(rate)

    def copy_ni_rate(self):
        """复制北爱尔兰税率"""
        item = self.result_tree.selection()[0]
        values = self.result_tree.item(item)['values']
        rate = values[3]  # 北爱尔兰税率在第4列
        if rate:
            self.copy_to_clipboard(rate)

    def open_uk_url(self):
        """打开英国税率网址"""
        item = self.result_tree.selection()[0]
        values = self.result_tree.item(item)['values']
        url = values[2]  # 英国网址在第3列
        if url and url.strip():
            import webbrowser
            webbrowser.open(url)

    def open_ni_url(self):
        """打开北爱尔兰税率网址"""
        item = self.result_tree.selection()[0]
        values = self.result_tree.item(item)['values']
        url = values[4]  # 北爱尔兰网址在第5列
        if url and url.strip():
            import webbrowser
            webbrowser.open(url)

    def update_data(self):
        """更新选中行的数据"""
        try:
            # 获取选中的项目
            selected_items = self.result_tree.selection()
            if not selected_items:
                messagebox.showwarning("警告", "请先选择要更新的数据")
                return

            item = selected_items[0]
            values = self.result_tree.item(item)['values']

            # 构建tariff数据
            tariff_data = {
                'code': values[0],
                'description': '',  # 表格中不显示description，从数据库获取
                'rate': values[1] if values[1] else None,
                'url': values[2] if values[2] else None,
                'north_ireland_rate': values[3] if values[3] else None,
                'north_ireland_url': values[4] if values[4] else None
            }

            # 从数据库获取完整数据（包括description）
            full_data = self.db.get_tariff(tariff_data['code'])
            if full_data:
                tariff_data.update(full_data)

            # 显示更新对话框
            dialog = UpdateDialog(self.root, tariff_data, on_save_callback=self._handle_update)

        except IndexError:
            messagebox.showwarning("警告", "请先选择要更新的数据")
        except Exception as e:
            logger.error(f"打开更新对话框失败: {str(e)}")
            messagebox.showerror("错误", f"打开更新对话框失败: {str(e)}")

    def _handle_update(self, updated_data):
        """处理更新结果（在对话框关闭后调用）"""
        def do_update():
            try:
                # 执行数据库更新
                self.db.update_tariff(
                    code=updated_data['code'],
                    description=updated_data['description'],
                    rate=updated_data['rate'],
                    url=updated_data['url'],
                    north_ireland_rate=updated_data['north_ireland_rate'],
                    north_ireland_url=updated_data['north_ireland_url']
                )

                # 更新UI
                self.queue.put((self._refresh_results, (), {}))

                # 显示成功消息
                self.queue.put((
                    lambda: self.status_var.set(f"已更新商品编码 {updated_data['code']} 的数据"),
                    (),
                    {}
                ))

            except Exception as e:
                logger.error(f"更新数据失败: {str(e)}")
                self.queue.put((
                    lambda: messagebox.showerror("错误", f"更新失败: {str(e)}"),
                    (),
                    {}
                ))

        # 在后台线程中执行更新
        thread = threading.Thread(target=do_update, daemon=True)
        thread.start()

    def _refresh_results(self):
        """刷新当前搜索结果"""
        # 保持当前搜索查询
        current_query = self.search_var.get().strip()
        if current_query:
            # 重新执行搜索
            self._search(current_query)
        else:
            # 如果没有搜索查询，清空结果
            for item in self.result_tree.get_children():
                self.result_tree.delete(item)

    def auto_update(self):
        """自动更新选中行的数据"""
        try:
            # 获取选中的项目
            selected_items = self.result_tree.selection()
            if not selected_items:
                messagebox.showwarning("警告", "请先选择要自动更新的数据")
                return

            item = selected_items[0]
            values = self.result_tree.item(item)['values']

            # 获取商品编码和税率网址
            code = values[0]
            uk_url = values[2]  # 英国网址在第3列
            ni_url = values[4] if len(values) > 4 and values[4] else None  # 北爱尔兰网址在第5列

            # 验证URL有效性
            if not uk_url or not uk_url.strip():
                messagebox.showwarning("警告", "该记录没有英国税率网址，无法进行自动更新")
                return

            # 构建确认消息
            confirm_msg = f"将自动抓取商品编码 {code} 的最新税率数据并与当前数据对比：\n\n"
            confirm_msg += f"• 英国税率网址: {uk_url[:60]}{'...' if len(uk_url) > 60 else ''}\n"

            if ni_url and ni_url.strip():
                confirm_msg += f"• 北爱尔兰税率网址: {ni_url[:60]}{'...' if len(ni_url) > 60 else ''}\n"
            else:
                confirm_msg += f"• 北爱尔兰税率网址: 未提供\n"

            confirm_msg += f"\n如有变化则自动更新。确认继续吗？"

            # 确认对话框
            if not messagebox.askyesno("确认自动更新", confirm_msg):
                return

            # 在后台线程中执行自动更新
            thread = threading.Thread(
                target=self._do_auto_update,
                args=(code, uk_url, ni_url),
                daemon=True
            )
            thread.start()

            # 显示详细状态
            regions = ["英国"]
            if ni_url and ni_url.strip():
                regions.append("北爱尔兰")
            self.status_var.set(f"正在自动更新{', '.join(regions)}税率数据...")

        except IndexError:
            messagebox.showwarning("警告", "请先选择要自动更新的数据")
        except Exception as e:
            logger.error(f"自动更新失败: {str(e)}")
            messagebox.showerror("错误", f"自动更新失败: {str(e)}")

    def _do_auto_update(self, code: str, uk_url: str, ni_url: str = None):
        """在后台线程中执行自动更新"""
        try:
            # 执行自动更新
            result = self.api.auto_update(code, uk_url, ni_url)

            # 在主线程中处理结果
            self.queue.put((self._handle_auto_update_result, (result, code), {}))

        except Exception as e:
            logger.error(f"后台自动更新失败: {str(e)}")
            self.queue.put((
                lambda: (self.status_var.set("自动更新失败"),
                         messagebox.showerror("错误", f"自动更新失败: {str(e)}")),
                (),
                {}
            ))

    def _handle_auto_update_result(self, result: Dict, code: str):
        """处理自动更新结果（支持独立地区更新）"""
        try:
            # 获取新的状态字段（兼容新旧格式）
            overall_success = result.get('overall_success', result.get('success', False))
            uk_success = result.get('uk_success', False)
            ni_success = result.get('ni_success', False)
            uk_updated = result.get('uk_updated', False)
            ni_updated = result.get('ni_updated', False)
            any_updated = result.get('updated', uk_updated or ni_updated)

            if overall_success:
                if any_updated:
                    # 实际更新了数据
                    self.status_var.set(f"自动更新成功: {result['message']}")

                    # 刷新搜索结果
                    self._refresh_results()

                    # 显示详细信息对话框
                    self._show_detailed_update_dialog(code, result, uk_success, ni_success)

                else:
                    # 没有变化，不需要更新
                    self.status_var.set("自动更新完成: " + result['message'])

                    # 根据成功状态选择不同的消息框类型
                    if uk_success and ni_success:
                        messagebox.showinfo("自动更新完成", result['message'])
                    elif uk_success or ni_success:
                        messagebox.showinfo("部分更新完成", result['message'])
                    else:
                        messagebox.showwarning("更新无变化", result['message'])
            else:
                # 完全失败（两个地区都失败）
                self.status_var.set("自动更新失败")

                # 构建详细错误信息
                error_msg = result['message']

                # 根据失败情况显示不同类型的对话框
                if not uk_success and not ni_success:
                    messagebox.showerror("自动更新失败", error_msg)
                else:
                    # 部分失败，但仍显示为警告而不是错误
                    messagebox.showwarning("部分更新失败", error_msg)

        except Exception as e:
            logger.error(f"处理自动更新结果失败: {str(e)}")
            self.status_var.set("处理自动更新结果时出错")
            messagebox.showerror("错误", f"处理自动更新结果失败: {str(e)}")

    def _show_detailed_update_dialog(self, code: str, result: Dict, uk_success: bool, ni_success: bool):
        """显示详细的更新结果对话框"""
        try:
            old_data = result['old_data']
            new_data = result['new_data']

            # 创建详细信息对话框
            dialog = tk.Toplevel(self.root)
            dialog.title("自动更新详情")
            dialog.geometry("600x500")
            dialog.resizable(True, True)

            # 设置模态
            dialog.transient(self.root)
            dialog.grab_set()

            # 主框架
            main_frame = ttk.Frame(dialog, padding="20")
            main_frame.pack(fill=tk.BOTH, expand=True)

            # 商品编码
            code_frame = ttk.Frame(main_frame)
            code_frame.pack(fill=tk.X, pady=(0, 10))
            ttk.Label(code_frame, text="商品编码:", font=('TkDefaultFont', 10, 'bold')).pack(side=tk.LEFT)
            ttk.Label(code_frame, text=code, font=('TkDefaultFont', 10)).pack(side=tk.LEFT, padx=(5, 0))

            # 状态指示器
            status_frame = ttk.Frame(main_frame)
            status_frame.pack(fill=tk.X, pady=(0, 15))

            ttk.Label(status_frame, text="更新状态:", font=('TkDefaultFont', 10, 'bold')).pack(side=tk.LEFT)

            # 获取更新状态
            uk_updated = result.get('uk_updated', False)
            ni_updated = result.get('ni_updated', False)

            # 英国状态
            if uk_success:
                if uk_updated:
                    uk_status = "✅ 已更新"
                    uk_color = "green"
                else:
                    uk_status = "✅ 无变化"
                    uk_color = "blue"
            else:
                uk_status = "❌ 失败"
                uk_color = "red"

            uk_label = ttk.Label(status_frame, text=f"英国 {uk_status}", foreground=uk_color)
            uk_label.pack(side=tk.LEFT, padx=(10, 20))

            # 北爱尔兰状态
            if ni_success:
                if ni_updated:
                    ni_status = "✅ 已更新"
                    ni_color = "green"
                else:
                    ni_status = "✅ 无变化"
                    ni_color = "blue"
            else:
                ni_status = "❌ 失败"
                ni_color = "red"

            ni_label = ttk.Label(status_frame, text=f"北爱尔兰 {ni_status}", foreground=ni_color)
            ni_label.pack(side=tk.LEFT, padx=(0, 10))

            # 总体状态
            overall_success = result.get('overall_success', result.get('success', uk_success or ni_success))
            if overall_success:
                if uk_updated or ni_updated:
                    overall_status = "✅ 部分更新成功"
                    overall_color = "green"
                else:
                    overall_status = "ℹ️ 数据无变化"
                    overall_color = "blue"
            else:
                overall_status = "❌ 更新失败"
                overall_color = "red"

            overall_label = ttk.Label(status_frame, text=f"总体 {overall_status}", foreground=overall_color, font=('TkDefaultFont', 9, 'bold'))
            overall_label.pack(side=tk.LEFT, padx=(20, 0))

            # 创建笔记本用于分组显示
            notebook = ttk.Notebook(main_frame)
            notebook.pack(fill=tk.BOTH, expand=True, pady=(10, 0))

            # 数据对比标签页
            comparison_frame = ttk.Frame(notebook, padding="10")
            notebook.add(comparison_frame, text="数据对比")

            # 创建对比表格
            columns = ('字段', '更新前', '更新后', '状态')
            comparison_tree = ttk.Treeview(comparison_frame, columns=columns, show='headings', height=8)

            # 设置列标题和宽度
            column_widths = {'字段': 120, '更新前': 150, '更新后': 150, '状态': 100}
            for col in columns:
                comparison_tree.heading(col, text=col)
                comparison_tree.column(col, width=column_widths[col])

            # 添加滚动条
            comp_scrollbar = ttk.Scrollbar(comparison_frame, orient=tk.VERTICAL, command=comparison_tree.yview)
            comparison_tree.configure(yscrollcommand=comp_scrollbar.set)

            # 布局对比表格
            comp_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
            comparison_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

            # 填充对比数据
            self._fill_comparison_data(comparison_tree, old_data, new_data)

            # 原始数据标签页
            raw_frame = ttk.Frame(notebook, padding="10")
            notebook.add(raw_frame, text="原始数据")

            # 创建文本框显示原始数据
            raw_text = tk.Text(raw_frame, wrap=tk.WORD, height=15, font=('Consolas', 9))
            raw_scrollbar = ttk.Scrollbar(raw_frame, orient=tk.VERTICAL, command=raw_text.yview)
            raw_text.configure(yscrollcommand=raw_scrollbar.set)

            # 布局原始数据
            raw_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
            raw_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

            # 填充原始数据
            raw_content = self._format_raw_data(old_data, new_data, uk_success, ni_success)
            raw_text.insert('1.0', raw_content)
            raw_text.configure(state='disabled')

            # 按钮框架
            button_frame = ttk.Frame(main_frame)
            button_frame.pack(fill=tk.X, pady=(15, 0))

            # 关闭按钮
            close_btn = ttk.Button(button_frame, text="关闭", command=dialog.destroy)
            close_btn.pack(side=tk.RIGHT)

            # 居中显示对话框
            dialog.geometry("+%d+%d" % (
                self.root.winfo_rootx() + 100,
                self.root.winfo_rooty() + 50
            ))

        except Exception as e:
            logger.error(f"显示详细更新对话框失败: {str(e)}")
            # 如果详细对话框失败，回退到简单对话框
            messagebox.showinfo("自动更新成功", result['message'])

    def _fill_comparison_data(self, tree, old_data: Dict, new_data: Dict):
        """填充数据对比表格"""
        try:
            # 定义要对比的字段
            fields = [
                ('描述', 'description'),
                ('英国税率', 'rate'),
                ('北爱尔兰税率', 'north_ireland_rate'),
                ('英国网址', 'url'),
                ('北爱尔兰网址', 'north_ireland_url')
            ]

            for display_name, field_name in fields:
                old_value = old_data.get(field_name, '') or '(无)'
                new_value = new_data.get(field_name, '') or '(无)'

                # 确定状态
                if old_value == new_value:
                    status = "无变化"
                else:
                    status = "已更新"

                # 插入数据
                tree.insert('', 'end', values=(display_name, old_value[:50] + '...' if len(old_value) > 50 else old_value,
                                             new_value[:50] + '...' if len(new_value) > 50 else new_value, status))

        except Exception as e:
            logger.error(f"填充对比数据失败: {str(e)}")

    def _format_raw_data(self, old_data: Dict, new_data: Dict, uk_success: bool, ni_success: bool) -> str:
        """格式化原始数据用于显示"""
        try:
            content = []
            content.append("=" * 50)
            content.append("自动更新结果详情")
            content.append("=" * 50)
            content.append(f"更新时间: {self._get_current_time()}")
            content.append(f"英国数据获取: {'成功' if uk_success else '失败'}")
            content.append(f"北爱尔兰数据获取: {'成功' if ni_success else '失败'}")
            content.append("")

            content.append("-" * 30)
            content.append("更新前数据:")
            content.append("-" * 30)
            for key, value in old_data.items():
                content.append(f"{key}: {value}")

            content.append("")
            content.append("-" * 30)
            content.append("更新后数据:")
            content.append("-" * 30)
            for key, value in new_data.items():
                content.append(f"{key}: {value}")

            return "\n".join(content)

        except Exception as e:
            logger.error(f"格式化原始数据失败: {str(e)}")
            return f"格式化数据失败: {str(e)}"

    def setup_remote_update(self):
        """设置远程数据更新界面"""
        # 配置区域
        config_frame = ttk.LabelFrame(self.remote_update_frame, text="更新配置")
        config_frame.pack(fill='x', padx=10, pady=10)

        # 元数据URL
        ttk.Label(config_frame, text="元数据URL:").grid(row=0, column=0, sticky='w', padx=5, pady=5)
        self.metadata_url_var = tk.StringVar(value=self.config.get("metadata_url", DEFAULT_METADATA_URL))
        ttk.Entry(config_frame, textvariable=self.metadata_url_var, width=70).grid(row=0, column=1, padx=5, pady=5)

        # 数据库路径
        ttk.Label(config_frame, text="数据库路径:").grid(row=1, column=0, sticky='w', padx=5, pady=5)
        self.db_path_var = tk.StringVar(value=self.config.get("db_path", "tariffs.db"))
        db_entry = ttk.Entry(config_frame, textvariable=self.db_path_var, width=70)
        db_entry.grid(row=1, column=1, padx=5, pady=5)

        # 浏览和应用按钮
        button_frame = ttk.Frame(config_frame)
        button_frame.grid(row=1, column=2, padx=5, pady=5)
        ttk.Button(button_frame, text="浏览", command=self.browse_remote_database).pack(side='left', padx=2)
        ttk.Button(button_frame, text="应用", command=self.reload_database).pack(side='left', padx=2)

        # 状态显示区域
        status_frame = ttk.LabelFrame(self.remote_update_frame, text="当前状态")
        status_frame.pack(fill='x', padx=10, pady=10)

        # 本地信息
        local_frame = ttk.Frame(status_frame)
        local_frame.pack(fill='x', padx=5, pady=5)
        ttk.Label(local_frame, text="本地版本:").pack(side='left')
        self.local_version_label = ttk.Label(local_frame, text="未知", foreground="blue")
        self.local_version_label.pack(side='left', padx=(10, 20))

        ttk.Label(local_frame, text="本地记录数:").pack(side='left')
        self.local_records_label = ttk.Label(local_frame, text="0", foreground="blue")
        self.local_records_label.pack(side='left', padx=10)

        # 远程信息
        remote_frame = ttk.Frame(status_frame)
        remote_frame.pack(fill='x', padx=5, pady=5)
        ttk.Label(remote_frame, text="远程版本:").pack(side='left')
        self.remote_version_label = ttk.Label(remote_frame, text="未检查", foreground="green")
        self.remote_version_label.pack(side='left', padx=(10, 20))

        ttk.Label(remote_frame, text="远程记录数:").pack(side='left')
        self.remote_records_label = ttk.Label(remote_frame, text="未检查", foreground="green")
        self.remote_records_label.pack(side='left', padx=10)

        # 更新状态
        update_frame = ttk.Frame(status_frame)
        update_frame.pack(fill='x', padx=5, pady=5)
        ttk.Label(update_frame, text="更新状态:").pack(side='left')
        self.update_status_label = ttk.Label(update_frame, text="未检查", foreground="gray")
        self.update_status_label.pack(side='left', padx=10)

        # Persistent in-app notification, visible until user installs newer data.
        self.data_update_banner_var = tk.StringVar(value="")
        self.data_update_banner = ttk.Frame(self.remote_update_frame)
        ttk.Label(self.data_update_banner, textvariable=self.data_update_banner_var,
                  foreground="#b45309").pack(side="left", padx=(10, 8))
        ttk.Button(self.data_update_banner, text="查看更新",
                   command=lambda: self.notebook.select(self.remote_update_frame)).pack(side="left")
        # Do not show an empty banner when no new release is available.

        # 操作按钮区域
        action_frame = ttk.LabelFrame(self.remote_update_frame, text="操作")
        action_frame.pack(fill='x', padx=10, pady=10)

        button_frame = ttk.Frame(action_frame)
        button_frame.pack(pady=10)

        # 检查更新按钮
        self.check_remote_update_btn = ttk.Button(
            button_frame,
            text="🔍 检查更新",
            command=self.check_remote_update,
            width=15
        )
        self.check_remote_update_btn.pack(side='left', padx=5)

        # 强制更新按钮
        self.force_remote_update_btn = ttk.Button(
            button_frame,
            text="🔄 强制更新",
            command=self.force_remote_update,
            width=15
        )
        self.force_remote_update_btn.pack(side='left', padx=5)

        # 刷新状态按钮
        self.refresh_status_btn = ttk.Button(
            button_frame,
            text="♻️ 刷新状态",
            command=self.refresh_remote_status,
            width=15
        )
        self.refresh_status_btn.pack(side='left', padx=5)

        # 日志显示区域
        log_frame = ttk.LabelFrame(self.remote_update_frame, text="更新日志")
        log_frame.pack(fill='both', expand=True, padx=10, pady=10)

        # 创建日志文本框
        self.remote_log_text = tk.Text(log_frame, height=15, width=100)
        self.remote_log_scrollbar = ttk.Scrollbar(log_frame, command=self.remote_log_text.yview)
        self.remote_log_text.config(yscrollcommand=self.remote_log_scrollbar.set)

        self.remote_log_text.pack(side='left', fill='both', expand=True, padx=5, pady=5)
        self.remote_log_scrollbar.pack(side='right', fill='y', pady=5)

        # 进度条容器框架（方便统一显示/隐藏）
        self.progress_frame = ttk.Frame(self.remote_update_frame)
        self.progress_frame.pack(fill='x', padx=10, pady=5)

        # 进度条（使用determinate模式显示实际下载进度）
        self.remote_progress_var = tk.DoubleVar()
        self.remote_progress_bar = ttk.Progressbar(
            self.progress_frame,
            variable=self.remote_progress_var,
            mode='determinate',
            maximum=100,
            length=400
        )
        self.remote_progress_bar.pack(fill='x')

        # 进度标签（显示百分比文字）
        self.remote_progress_label = ttk.Label(
            self.progress_frame,
            text="0%",
            foreground="blue"
        )
        self.remote_progress_label.pack(pady=2)

        # 初始化时隐藏进度条
        self.progress_frame.pack_forget()

        # 初始化远程更新检查器
        self.smart_update_checker = None

    def browse_remote_database(self):
        """浏览数据库文件"""
        filename = filedialog.askopenfilename(
            title="选择数据库文件",
            filetypes=[("SQLite数据库", "*.db"), ("所有文件", "*.*")]
        )
        if filename:
            self.db_path_var.set(filename)

    def refresh_remote_status(self):
        """刷新远程状态"""
        # ✅ 检查是否有操作正在进行
        if self.remote_update_in_progress:
            messagebox.showwarning("提示", "远程更新操作正在进行中，请稍候")
            return

        def refresh_task():
            with self.remote_update_lock:
                self.remote_update_in_progress = True
                try:
                    db_path = self.db_path_var.get()
                    if not os.path.exists(db_path):
                        # ✅ 使用队列更新UI
                        self.queue.put((self.local_version_label.config, (), {'text': "数据库文件不存在"}))
                        self.queue.put((self.local_records_label.config, (), {'text': "0"}))
                        self.queue.put((self.update_status_label.config, (), {'text': "需要创建", 'foreground': "orange"}))
                        self.queue.put((self.add_remote_log, ("❌ 数据库文件不存在",), {}))
                        return

                    # 初始化更新检查器
                    metadata_url = self.metadata_url_var.get()
                    self.smart_update_checker = SmartUpdateChecker(metadata_url, db_path)

                    # 获取本地信息
                    local_metadata = self.smart_update_checker.load_local_metadata()
                    local_db_info = self.smart_update_checker.get_local_db_info()

                    # ✅ 使用队列更新UI
                    if local_metadata:
                        self.queue.put((self.local_version_label.config, (), {'text': local_metadata.get('version', '未知')}))
                    else:
                        self.queue.put((self.local_version_label.config, (), {'text': "无元数据"}))

                    self.queue.put((self.local_records_label.config, (), {'text': str(local_db_info.get('record_count', 0))}))
                    self.queue.put((self.update_status_label.config, (), {'text': "已检查", 'foreground': "green"}))
                    self.queue.put((self.add_remote_log, ("✅ 本地状态已刷新",), {}))

                except Exception as e:
                    # ✅ 使用队列更新UI
                    self.queue.put((self.add_remote_log, (f"❌ 刷新状态失败: {str(e)}",), {}))
                    self.queue.put((self.update_status_label.config, (), {'text': "检查失败", 'foreground': "red"}))
                finally:
                    self.remote_update_in_progress = False

        # ✅ 使用线程池提交任务（避免无限制创建线程）
        self.thread_pool.submit(refresh_task)

    def check_remote_update(self, automatic: bool = False):
        """Read-only release check (manual or scheduled background operation)."""
        if self.remote_update_in_progress:
            if automatic:
                self._schedule_next_data_update_check(RETRY_INTERVAL_MS)
            else:
                messagebox.showwarning("提示", "远程更新操作正在进行中，请稍候")
            return

        # Tk variables belong to the UI thread, never read them from a worker.
        db_path = self.db_path_var.get()
        metadata_url = configured_metadata_url(self.metadata_url_var.get())

        def check_task():
            succeeded = False
            with self.remote_update_lock:
                self.remote_update_in_progress = True
                try:
                    # ✅ 使用队列更新UI
                    self.queue.put((self.set_remote_ui_state, (False,), {}))
                    self.queue.put((self.remote_progress_bar.start, (), {}))
                    self.queue.put((self.add_remote_log, ("🔍 开始检查远程更新...",), {}))

                    # 初始化更新检查器
                    self.smart_update_checker = SmartUpdateChecker(metadata_url, db_path)

                    # 下载远程元数据
                    remote_metadata = self.smart_update_checker.download_metadata()
                    if not remote_metadata:
                        # ✅ 使用队列更新UI
                        self.queue.put((self.add_remote_log, ("❌ 无法下载远程元数据",), {}))
                        self.queue.put((self.remote_version_label.config, (), {'text': "获取失败", 'foreground': "red"}))
                        return

                    # 更新远程版本信息
                    remote_version = remote_metadata.get('version', '未知')
                    remote_records = remote_metadata.get('record_count', 0)
                    # ✅ 使用队列更新UI
                    self.queue.put((self.remote_version_label.config, (), {'text': remote_version, 'foreground': "green"}))
                    self.queue.put((self.remote_records_label.config, (), {'text': str(remote_records), 'foreground': "green"}))

                    # 获取本地信息
                    local_metadata = self.smart_update_checker.load_local_metadata()
                    local_db_info = self.smart_update_checker.get_local_db_info()

                    # 更新本地版本信息
                    # ✅ 使用队列更新UI
                    if local_metadata:
                        local_version = local_metadata.get('version', '未知')
                        self.queue.put((self.local_version_label.config, (), {'text': local_version}))
                    else:
                        self.queue.put((self.local_version_label.config, (), {'text': "无元数据"}))

                    self.queue.put((self.local_records_label.config, (), {'text': str(local_db_info.get('record_count', 0))}))

                    # 检查是否需要更新
                    update_needed, reason, details = self.smart_update_checker.check_update_needed(
                        remote_metadata, local_db_info, local_metadata
                    )

                    succeeded = True
                    # ✅ 使用队列更新UI
                    if update_needed:
                        self.queue.put((self.update_status_label.config, (), {'text': f"需要更新: {reason}", 'foreground': "orange"}))
                        self.queue.put((self.add_remote_log, (f"🔄 需要更新: {reason}",), {}))

                        priority = details.get('priority', 'medium')
                        if priority == 'high':
                            self.queue.put((self.add_remote_log, ("🔥 高优先级更新建议",), {}))
                        elif priority == 'medium':
                            self.queue.put((self.add_remote_log, ("⚠️ 中优先级更新建议",), {}))
                        else:
                            self.queue.put((self.add_remote_log, ("💡 低优先级更新建议",), {}))

                        if automatic:
                            # Nonblocking notification; do not automatically install data.
                            self.queue.put((self._announce_new_data_release, (remote_metadata, reason), {}))
                        else:
                            self.queue.put((self._show_update_confirmation_dialog, (reason, remote_metadata, details), {}))
                    else:
                        self.queue.put((self.update_status_label.config, (), {'text': "已是最新", 'foreground': "green"}))
                        self.queue.put((self.add_remote_log, ("✅ 数据库已是最新版本",), {}))

                except Exception as e:
                    # ✅ 使用队列更新UI
                    self.queue.put((self.add_remote_log, (f"❌ 检查更新失败: {str(e)}",), {}))
                    self.queue.put((self.update_status_label.config, (), {'text': "检查失败", 'foreground': "red"}))
                finally:
                    # ✅ 使用队列更新UI
                    self.queue.put((self.remote_progress_bar.stop, (), {}))
                    self.queue.put((self.set_remote_ui_state, (True,), {}))
                    self.remote_update_in_progress = False
                    if automatic:
                        self.queue.put((self._finish_daily_data_update_check, (succeeded,), {}))

        self.thread_pool.submit(check_task)

    def _schedule_next_data_update_check(self, delay_ms: int):
        """Schedule on Tk's event loop; never block UI with network traffic."""
        if getattr(self, "_closing", False):
            return
        previous = getattr(self, "_data_update_check_timer", None)
        if previous is not None:
            self.root.after_cancel(previous)
        self._data_update_check_timer = self.root.after(
            delay_ms, self._run_daily_data_update_check
        )

    def _run_daily_data_update_check(self):
        """Check on app startup and at least once per day while it stays open."""
        self._data_update_check_timer = None
        if not self._closing:
            self.check_remote_update(automatic=True)

    def _finish_daily_data_update_check(self, succeeded: bool):
        """Reschedule even if metadata service was temporarily inaccessible."""
        if not self._closing:
            self._schedule_next_data_update_check(check_delay_ms(succeeded=succeeded))

    def _close_data_release_notification(self):
        window = self._data_notification_window
        self._data_notification_window = None
        if window is not None and window.winfo_exists():
            window.destroy()

    def _announce_new_data_release(self, remote_metadata: dict, reason: str):
        """Persistent banner + once-per-release non-modal desktop popup."""
        if self._closing:
            return
        key = release_notification_key(remote_metadata)
        version = str(remote_metadata["version"])
        self.data_update_banner_var.set(f"发现已发布的数据更新：{version}")
        self.data_update_banner.pack(fill="x", padx=10, pady=(0, 4))
        self.notebook.tab(self.remote_update_frame, text="远程数据更新 ●")
        if (self.config.get("last_data_update_notification") == key
                or getattr(self, "_last_notified_release", None) == key):
            return
        self._last_notified_release = key
        self.config["last_data_update_notification"] = key
        self.config["metadata_url"] = configured_metadata_url(self.metadata_url_var.get())
        self.config["db_path"] = self.db_path_var.get()
        save_config(self.config)  # Save immediately: no repeated alerts after restarting.

        self._close_data_release_notification()
        window = tk.Toplevel(self.root)
        self._data_notification_window = window
        window.title("关税数据更新通知")
        window.geometry("460x200")
        window.resizable(False, False)
        window.transient(self.root)
        window.protocol("WM_DELETE_WINDOW", self._close_data_release_notification)
        body = ttk.Frame(window, padding=18)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text="有新的关税数据库可用",
                  font=("TkDefaultFont", 12, "bold")).pack(anchor="w", pady=(0, 10))
        ttk.Label(body,
                  text=f"数据版本：{version}\n{reason}\n不会自动覆盖本地数据，请选择是否更新。",
                  wraplength=415, justify="left").pack(anchor="w", fill="x")
        actions = ttk.Frame(body)
        actions.pack(anchor="e", side="bottom", pady=(12, 0))
        ttk.Button(actions, text="稍后更新",
                   command=self._close_data_release_notification).pack(side="left", padx=5)
        def install():
            self._close_data_release_notification()
            self.notebook.select(self.remote_update_frame)
            self.force_remote_update()
        ttk.Button(actions, text="立即更新", command=install).pack(side="left", padx=5)
        window.lift()
        window.bell()

    def force_remote_update(self):
        """强制更新"""
        # ✅ 检查是否有操作正在进行
        if self.remote_update_in_progress:
            messagebox.showwarning("提示", "远程更新操作正在进行中，请稍候")
            return

        def update_task():
            with self.remote_update_lock:
                self.remote_update_in_progress = True
                try:
                    # ✅ 使用队列更新UI
                    self.queue.put((self.set_remote_ui_state, (False,), {}))
                    # 显示进度条并重置为0
                    self.queue.put((self.show_progress_bar, (), {}))
                    self.queue.put((self.remote_progress_var.set, (0,), {}))
                    self.queue.put((self.remote_progress_label.config, (), {'text': "0%"}))
                    self.queue.put((self.add_remote_log, ("🔄 开始强制更新...",), {}))

                    # 初始化更新检查器
                    db_path = self.db_path_var.get()
                    metadata_url = self.metadata_url_var.get()
                    self.smart_update_checker = SmartUpdateChecker(metadata_url, db_path)

                    # 定义进度回调函数
                    def progress_callback(downloaded_bytes, total_bytes, percentage):
                        """下载进度回调,在主线程中更新进度条"""
                        try:
                            # 使用队列安全地更新UI
                            self.queue.put((self.remote_progress_var.set, (percentage,), {}))

                            # 更新进度标签文字
                            self.queue.put((
                                self.remote_progress_label.config,
                                (),
                                {'text': f"{percentage:.1f}%"}
                            ))

                            # 每10%记录一次日志
                            if int(percentage) % 10 == 0 and int(percentage) != 0:
                                mb_downloaded = downloaded_bytes / 1024 / 1024
                                mb_total = total_bytes / 1024 / 1024
                                self.queue.put((
                                    self.add_remote_log,
                                    (f"📥 下载进度: {percentage:.1f}% ({mb_downloaded:.1f}MB / {mb_total:.1f}MB)",),
                                    {}
                                ))
                        except Exception as e:
                            logger.warning(f"进度回调更新UI失败: {str(e)}")

                    # 执行强制更新（传递进度回调）
                    result = self.smart_update_checker.check_and_update(force_update=True, progress_callback=progress_callback)

                    # ✅ 使用队列更新UI
                    update_success = False
                    if result['status'] == 'success':
                        # 更新成功，设置进度条为100%
                        self.queue.put((self.remote_progress_var.set, (100,), {}))
                        self.queue.put((self.remote_progress_label.config, (), {'text': "100%"}))
                        self.queue.put((self.add_remote_log, (f"✅ 更新成功: {result['message']}",), {}))
                        self.queue.put((self.update_status_label.config, (), {'text': "更新成功", 'foreground': "green"}))
                        update_success = True

                        # 延迟1秒后隐藏进度条（让用户看到100%）
                        import time
                        time.sleep(1)
                        self.queue.put((self.hide_progress_bar, (), {}))
                    else:
                        # 更新失败，隐藏进度条
                        self.queue.put((self.hide_progress_bar, (), {}))
                        self.queue.put((self.add_remote_log, (f"❌ 更新失败: {result['message']}",), {}))
                        self.queue.put((self.update_status_label.config, (), {'text': "更新失败", 'foreground': "red"}))

                except Exception as e:
                    # ✅ 使用队列更新UI
                    # 异常时隐藏进度条
                    self.queue.put((self.hide_progress_bar, (), {}))
                    self.queue.put((self.add_remote_log, (f"❌ 强制更新异常: {str(e)}",), {}))
                    self.queue.put((self.update_status_label.config, (), {'text': "更新异常", 'foreground': "red"}))
                    update_success = False
                finally:
                    # ✅ 使用队列更新UI
                    self.queue.put((self.set_remote_ui_state, (True,), {}))
                    self.remote_update_in_progress = False

                    # ✅ 如果更新成功，在锁释放后刷新本地状态信息
                    if update_success:
                        self.queue.put((self._refresh_local_info_after_update, (), {}))

        # ✅ 使用线程池提交任务（避免无限制创建线程）
        self.thread_pool.submit(update_task)

    def set_remote_ui_state(self, enabled):
        """设置远程更新UI状态"""
        state = 'normal' if enabled else 'disabled'
        self.check_remote_update_btn.config(state=state)
        self.force_remote_update_btn.config(state=state)
        self.refresh_status_btn.config(state=state)

    def show_progress_bar(self):
        """显示进度条"""
        self.progress_frame.pack(fill='x', padx=10, pady=5)

    def hide_progress_bar(self):
        """隐藏进度条"""
        self.progress_frame.pack_forget()

    def add_remote_log(self, message):
        """添加远程更新日志"""
        timestamp = self._get_current_time()
        self.remote_log_text.insert('end', f"[{timestamp}] {message}\n")
        self.remote_log_text.see('end')

    def _get_current_time(self) -> str:
        """获取当前时间字符串"""
        from datetime import datetime
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def _show_update_confirmation_dialog(self, reason: str, remote_metadata: dict, details: dict):
        """显示更新确认对话框"""
        try:
            # 构建详细的更新信息
            priority = details.get('priority', 'medium')
            priority_text = {
                'high': '🔥 高优先级',
                'medium': '⚠️ 中优先级',
                'low': '💡 低优先级'
            }.get(priority, '⚠️ 中优先级')

            # 获取版本信息
            remote_version = remote_metadata.get('version', '未知')
            remote_records = remote_metadata.get('record_count', 0)
            remote_size = remote_metadata.get('file_size', 0)
            size_mb = remote_size / 1024 / 1024 if remote_size > 0 else 0

            # 获取变更摘要
            changes = remote_metadata.get('changes_summary', {})
            total_updates = changes.get('total_updates', 0)
            uk_updated = changes.get('uk_updated', 0)
            ni_updated = changes.get('ni_updated', 0)

            # 构建确认消息
            message = f"检测到数据库更新！\n\n"
            message += f"更新原因：{reason}\n"
            message += f"优先级：{priority_text}\n\n"
            message += f"远程版本信息：\n"
            message += f"  • 版本号：{remote_version}\n"
            message += f"  • 记录数：{remote_records:,} 条\n"
            message += f"  • 文件大小：{size_mb:.1f} MB\n\n"

            if total_updates > 0:
                message += f"变更摘要：\n"
                message += f"  • 总更新数：{total_updates} 条\n"
                message += f"  • 英国税率更新：{uk_updated} 条\n"
                message += f"  • 北爱尔兰税率更新：{ni_updated} 条\n\n"

            message += f"是否立即下载并更新数据库？"

            # 显示确认对话框
            if messagebox.askyesno("发现数据库更新", message, icon='question'):
                # 用户确认更新，调用强制更新方法
                self.add_remote_log("✅ 用户确认更新，开始下载...")
                self.force_remote_update()
            else:
                # 用户取消更新
                self.add_remote_log("ℹ️ 用户取消更新")
                self.update_status_label.config(text="用户取消更新", foreground="gray")

        except Exception as e:
            logger.error(f"显示更新确认对话框失败: {str(e)}")
            # 如果对话框显示失败，记录错误但不中断流程
            self.add_remote_log(f"⚠️ 显示更新对话框失败: {str(e)}")

    def _refresh_local_info_after_update(self):
        """更新成功后刷新本地版本和记录数信息"""
        try:
            if not self.smart_update_checker:
                return

            # 🔧 关键修复：重新初始化API和DB对象，确保连接到新的数据库文件
            # 这是必需的，因为数据库文件被替换后，旧的连接仍然指向旧文件句柄
            logger.info("🔄 重新初始化数据库连接...")
            self.setup_api()
            self.add_remote_log("🔄 已重新初始化数据库连接")

            # 重新加载本地元数据和数据库信息
            local_metadata = self.smart_update_checker.load_local_metadata()
            local_db_info = self.smart_update_checker.get_local_db_info()

            # 更新本地版本信息
            if local_metadata:
                local_version = local_metadata.get('version', '未知')
                self.local_version_label.config(text=local_version)
                self.add_remote_log(f"📊 本地版本已更新: {local_version}")
            else:
                self.local_version_label.config(text="无元数据")

            # 更新本地记录数
            record_count = local_db_info.get('record_count', 0)
            self.local_records_label.config(text=str(record_count))
            self.add_remote_log(f"📊 本地记录数已更新: {record_count:,} 条")
            # Clear pending release state only after the database update succeeded.
            self.data_update_banner.pack_forget()
            self.notebook.tab(self.remote_update_frame, text="远程数据更新")
            self._close_data_release_notification()

        except Exception as e:
            logger.error(f"刷新本地信息失败: {str(e)}")
            self.add_remote_log(f"⚠️ 刷新本地信息失败: {str(e)}")

    def _load_initial_local_info(self):
        """GUI启动时自动加载本地数据库信息"""
        try:
            db_path = self.db_path_var.get()
            metadata_url = self.metadata_url_var.get()

            # 检查数据库文件是否存在
            if not os.path.exists(db_path):
                self.add_remote_log("ℹ️ 本地数据库文件不存在，请检查更新")
                self.local_version_label.config(text="未安装")
                self.local_records_label.config(text="0")
                self.update_status_label.config(text="未安装数据库", foreground="orange")
                return

            # 初始化更新检查器
            self.smart_update_checker = SmartUpdateChecker(metadata_url, db_path)

            # 加载本地元数据和数据库信息
            local_metadata = self.smart_update_checker.load_local_metadata()
            local_db_info = self.smart_update_checker.get_local_db_info()

            # 更新本地版本信息
            if local_metadata:
                local_version = local_metadata.get('version', '未知')
                self.local_version_label.config(text=local_version)
                self.add_remote_log(f"✅ 已加载本地版本: {local_version}")
            else:
                self.local_version_label.config(text="无元数据")
                self.add_remote_log("⚠️ 未找到本地元数据文件")

            # 更新本地记录数
            record_count = local_db_info.get('record_count', 0)
            self.local_records_label.config(text=str(record_count))
            self.add_remote_log(f"✅ 已加载本地记录数: {record_count:,} 条")

            # 更新状态
            if record_count > 0:
                self.update_status_label.config(text="已就绪", foreground="green")
            else:
                self.update_status_label.config(text="数据库为空", foreground="orange")

        except Exception as e:
            logger.error(f"加载初始本地信息失败: {str(e)}")
            self.add_remote_log(f"⚠️ 加载本地信息失败: {str(e)}")
            self.local_version_label.config(text="加载失败")
            self.local_records_label.config(text="0")
            self.update_status_label.config(text="加载失败", foreground="red")

    def reload_database(self):
        """重新加载数据库连接"""
        try:
            # 关闭旧连接
            if hasattr(self, 'db'):
                self.db.close()

            # 获取新的数据库路径
            new_db_path = self.db_path_var.get()
            logger.info(f"🔄 重新加载数据库: {new_db_path}")

            # 重新初始化数据库连接
            self.db = TariffDB(db_path=new_db_path)
            self.api = TariffAPI(db_path=new_db_path)

            # 更新配置
            self.config["db_path"] = new_db_path

            logger.info("✅ 数据库重新加载成功")
            return True
        except Exception as e:
            logger.error(f"❌ 重新加载数据库失败: {e}")
            messagebox.showerror("错误", f"重新加载数据库失败: {str(e)}")
            return False

    def on_closing(self):
        """窗口关闭时的清理工作"""
        # ✅ 检查是否有正在进行的操作
        if self.remote_update_in_progress:
            if not messagebox.askyesno(
                "确认关闭",
                "远程更新操作正在进行中，强制关闭可能导致数据损坏。\n确定要关闭吗？"
            ):
                return

        self._closing = True
        if self._data_update_check_timer is not None:
            self.root.after_cancel(self._data_update_check_timer)
            self._data_update_check_timer = None

        # ✅ 保存配置文件
        self.config["db_path"] = self.db_path_var.get()
        self.config["metadata_url"] = configured_metadata_url(self.metadata_url_var.get())
        save_config(self.config)

        # ✅ 关闭线程池（等待当前任务完成，但不接受新任务）
        if hasattr(self, 'thread_pool'):
            logger.info("正在关闭线程池...")
            self.thread_pool.shutdown(wait=False)

        # ✅ 关闭数据库连接
        if hasattr(self, 'db'):
            try:
                self.db.close()
                logger.info("数据库连接已关闭")
            except Exception as e:
                logger.error(f"关闭数据库连接失败: {str(e)}")

        # ✅ 销毁窗口
        logger.info("关闭GUI窗口")
        self.root.destroy()

    def run(self):
        """运行GUI"""
        self.root.mainloop()

if __name__ == "__main__":
    gui = TariffGUI()
    gui.run()