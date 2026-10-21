import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import threading
import queue
import logging
import sys
import os

# 添加项目根目录到 Python 路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from src.core.batch_processor import BatchProcessor
import webbrowser

logger = logging.getLogger(__name__)

class BatchProcessFrame(ttk.Frame):
    def __init__(self, master):
        super().__init__(master)
        self.processor = BatchProcessor()
        self.update_queue = queue.Queue()
        self.create_widgets()
        self.layout_widgets()
        self.setup_periodic_updates()

    def create_widgets(self):
        # 文件选择部分
        self.file_frame = ttk.LabelFrame(self, text="文件选择")
        self.file_path_var = tk.StringVar()
        self.file_entry = ttk.Entry(
            self.file_frame,
            textvariable=self.file_path_var,
            width=50
        )
        self.browse_btn = ttk.Button(
            self.file_frame,
            text="浏览",
            command=self.browse_file
        )
        self.process_btn = ttk.Button(
            self.file_frame,
            text="开始处理",
            command=self.start_processing
        )
        # 添加下载模板按钮
        self.template_btn = ttk.Button(
            self.file_frame,
            text="下载模板",
            command=self.download_template
        )

        # 进度显示部分
        self.progress_frame = ttk.LabelFrame(self, text="处理进度")
        self.progress_var = tk.DoubleVar()
        self.progress_bar = ttk.Progressbar(
            self.progress_frame,
            variable=self.progress_var,
            maximum=100
        )
        self.status_var = tk.StringVar(value="就绪")
        self.status_label = ttk.Label(
            self.progress_frame,
            textvariable=self.status_var
        )

        # 日志显示部分
        self.log_frame = ttk.LabelFrame(self, text="处理日志")
        self.log_text = tk.Text(
            self.log_frame,
            height=10,
            width=60,
            wrap=tk.WORD
        )
        self.log_scroll = ttk.Scrollbar(
            self.log_frame,
            command=self.log_text.yview
        )
        self.log_text.configure(yscrollcommand=self.log_scroll.set)

        # 历史记录部分
        self.history_frame = ttk.LabelFrame(self, text="历史记录")
        columns = ('文件名', '处理时间', '大小')
        self.history_tree = ttk.Treeview(
            self.history_frame,
            columns=columns,
            show='headings',
            height=5
        )
        for col in columns:
            self.history_tree.heading(col, text=col)
            self.history_tree.column(col, width=120)

        self.history_scroll = ttk.Scrollbar(
            self.history_frame,
            command=self.history_tree.yview
        )
        self.history_tree.configure(yscrollcommand=self.history_scroll.set)

        # 添加按钮框架
        self.history_btn_frame = ttk.Frame(self.history_frame)

        # 下载按钮
        self.download_btn = ttk.Button(
            self.history_btn_frame,
            text="下载选中文件",
            command=self.download_selected_file
        )

        # 刷新按钮
        self.refresh_btn = ttk.Button(
            self.history_btn_frame,
            text="刷新",
            command=self.refresh_history
        )

    def layout_widgets(self):
        # 文件选择布局
        self.file_frame.pack(fill=tk.X, padx=5, pady=5)
        self.file_entry.pack(side=tk.LEFT, padx=5, pady=5)
        self.browse_btn.pack(side=tk.LEFT, padx=5, pady=5)
        self.process_btn.pack(side=tk.LEFT, padx=5, pady=5)
        self.template_btn.pack(side=tk.LEFT, padx=5, pady=5)

        # 进度显示布局
        self.progress_frame.pack(fill=tk.X, padx=5, pady=5)
        self.progress_bar.pack(fill=tk.X, padx=5, pady=5)
        self.status_label.pack(padx=5, pady=5)

        # 日志显示布局
        self.log_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        self.log_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # 历史记录布局
        self.history_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        self.history_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.history_tree.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        # 按钮布局
        self.history_btn_frame.pack(side=tk.BOTTOM, fill=tk.X, padx=5, pady=5)
        self.download_btn.pack(side=tk.LEFT, padx=5)
        self.refresh_btn.pack(side=tk.LEFT, padx=5)

    def browse_file(self):
        """选择Excel文件"""
        file_path = filedialog.askopenfilename(
            filetypes=[("Excel files", "*.xlsx *.xls")]
        )
        if file_path:
            self.file_path_var.set(file_path)

    def start_processing(self):
        """开始处理文件"""
        file_path = self.file_path_var.get()
        if not file_path:
            messagebox.showerror("错误", "请先选择要处理的文件")
            return

        if not os.path.exists(file_path):
            messagebox.showerror("错误", "文件不存在")
            return

        # 禁用按钮
        self.process_btn.configure(state='disabled')
        self.browse_btn.configure(state='disabled')

        # 清空日志
        self.log_text.delete(1.0, tk.END)

        # 启动处理线程
        thread = threading.Thread(
            target=self._process_file,
            args=(file_path,),
            daemon=True
        )
        thread.start()

    def _process_file(self, file_path: str):
        """在后台线程中处理文件"""
        try:
            output_file = self.processor.process_file(file_path)
            if output_file:
                self.update_queue.put(("success", f"处理完成: {output_file}"))
                self.refresh_history()
            else:
                self.update_queue.put(("error", "处理失败"))
        except Exception as e:
            self.update_queue.put(("error", f"处理出错: {str(e)}"))
        finally:
            self.update_queue.put(("enable_buttons", None))

    def setup_periodic_updates(self):
        """设置定期更新UI的任务"""
        self.update_ui()
        self.after(100, self.setup_periodic_updates)

    def update_ui(self):
        """更新UI显示"""
        # 更新进度
        progress_info = self.processor.get_progress()
        self.progress_var.set(progress_info['progress'] * 100)
        self.status_var.set(
            f"状态: {progress_info['status']} - "
            f"文件: {progress_info['current_file'] or '无'}"
        )

        # 更新日志
        logs = self.processor.get_logs()
        for log in logs:
            self.log_text.insert(tk.END, log + "\n")
            self.log_text.see(tk.END)

        # 处理更新队列中的消息
        while not self.update_queue.empty():
            msg_type, msg = self.update_queue.get()
            if msg_type == "success":
                messagebox.showinfo("成功", msg)
            elif msg_type == "error":
                messagebox.showerror("错误", msg)
            elif msg_type == "enable_buttons":
                self.process_btn.configure(state='normal')
                self.browse_btn.configure(state='normal')

    def refresh_history(self):
        """刷新历史记录"""
        # 清空现有记录
        for item in self.history_tree.get_children():
            self.history_tree.delete(item)

        # 添加新记录
        for file_info in self.processor.get_history_files():
            self.history_tree.insert(
                '',
                'end',
                values=(
                    file_info['filename'],
                    file_info['time'],
                    file_info['size']
                ),
                tags=(file_info['path'],)
            )

    def download_selected_file(self):
        """下载选中的文件"""
        selection = self.history_tree.selection()
        if not selection:
            messagebox.showwarning("警告", "请先选择要下载的文件")
            return

        try:
            item = selection[0]
            file_path = self.history_tree.item(item)['tags'][0]
            if os.path.exists(file_path):
                # 打开文件保存对话框
                save_path = filedialog.asksaveasfilename(
                    defaultextension=".xlsx",
                    filetypes=[("Excel files", "*.xlsx")],
                    initialfile=os.path.basename(file_path)
                )
                if save_path:
                    import shutil
                    shutil.copy2(file_path, save_path)
                    messagebox.showinfo("成功", f"文件已保存到: {save_path}")
            else:
                messagebox.showerror("错误", "文件不存在")
        except Exception as e:
            messagebox.showerror("错误", f"下载文件失败: {str(e)}")
            logger.error(f"下载文件失败: {str(e)}")

    def download_template(self):
        """下载Excel模板文件"""
        template_path = "templates/batch_rate_template.xlsx"
        if not os.path.exists(template_path):
            messagebox.showerror("错误", "模板文件不存在")
            return

        try:
            # 打开文件保存对话框
            save_path = filedialog.asksaveasfilename(
                defaultextension=".xlsx",
                filetypes=[("Excel files", "*.xlsx")],
                initialfile="batch_rate_template.xlsx"
            )
            if save_path:
                import shutil
                shutil.copy2(template_path, save_path)
                messagebox.showinfo("成功", f"模板已保存到: {save_path}")
        except Exception as e:
            messagebox.showerror("错误", f"下载模板失败: {str(e)}")
            logger.error(f"下载模板失败: {str(e)}")