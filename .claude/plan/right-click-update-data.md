# 右键菜单数据更新功能 - 执行计划

## 任务概述
在关税查询工具的单查询结果右键菜单中增加"更新数据"和"自动更新"选项，允许用户手动编辑或自动抓取更新数据。

## 技术方案
**采用方案1：弹窗编辑模式**
- 在现有右键菜单中添加"更新数据"选项
- 弹出对话框显示可编辑字段
- 使用现有TariffDB.update_tariff()方法保存
- 符合项目现有的交互模式

**采用方案2：智能自动更新**
- 在右键菜单中添加"自动更新"选项
- 自动抓取URL内容，解析税率信息
- 智能对比当前数据，只在有变化时更新
- 主要对比英国税率和商品描述

**架构设计原则**
- tariff_api.py：纯数据库API层（不包含爬虫逻辑）
- scraper.py：负责所有爬虫和数据抓取逻辑
- tariff_gui.py：用户界面和交互逻辑
- 分层明确，职责分离，易于维护和扩展

## 详细步骤

### 步骤1：更新右键菜单 (tariff_gui.py:230-238)
- 在现有右键菜单中添加"更新数据"选项
- 添加"自动更新"选项
- 位置：复制税率选项之后，使用分隔线分隔
- 绑定处理函数：self.update_data(), self.auto_update()

### 步骤2：创建更新对话框 (tariff_gui.py:14-127)
- 创建UpdateDialog类，继承tk.Toplevel
- 字段布局：
  - 商品编码 (只读Label)
  - 商品描述 (Text或Entry)
  - 英国税率 (Entry)
  - 英国税率网址 (Entry)
  - 北爱尔兰税率 (Entry)
  - 北爱尔兰税率网址 (Entry)
- 按钮：保存、取消

### 步骤3：集成数据库操作 (tariff_db.py:160-196)
- 新增TariffDB.update_tariff()方法
- 支持部分字段更新（动态SQL构建）
- 添加错误处理和日志记录

### 步骤4：实现自动更新功能
**4.1 在scraper.py中添加自动更新方法 (scraper.py:333-432)**
- 新增TariffScraper.auto_update_single()方法
- 复用现有的抓取和解析逻辑
- 使用现有的scrape_with_retry()方法
- 使用现有的parse_commodity_page()方法
- 智能对比税率和描述是否变化
- 只在有实际变化时才更新数据库

**4.2 修改tariff_api.py保持纯API层 (tariff_api.py:147-165)**
- 移除爬虫相关的import（asyncio, scrape_urls, BeautifulSoup）
- 删除_parse_commodity_page方法
- 修改auto_update方法，调用TariffScraper.auto_update_single
- 保持tariff_api.py作为纯数据库API层

### 步骤5：完善交互体验 (tariff_gui.py:524-624)
- 获取选中行的完整数据
- 预填充编辑表单
- 更新后刷新表格显示
- 添加成功/失败状态提示
- 自动更新需要确认对话框
- 显示变化前后的对比信息

## 涉及文件
- **tariff_gui.py** (主要修改)：用户界面和交互逻辑
- **tariff_db.py** (新增update_tariff方法)：数据库操作层
- **tariff_api.py** (修改auto_update)：数据库API层（保持纯API，无爬虫逻辑）
- **scraper.py** (新增auto_update_single方法)：爬虫和数据抓取层

## 预期结果
用户可以通过右键菜单轻松编辑任何查询结果的数据：
1. **更新数据**：手动编辑商品描述、税率、网址等信息
2. **自动更新**：一键自动抓取最新税率并智能对比更新

## 使用方法

### 手动更新
1. 搜索商品编码
2. 右键点击结果行
3. 选择"更新数据"
4. 在弹出对话框中编辑字段
5. 点击"保存"

### 自动更新
1. 搜索商品编码
2. 右键点击结果行
3. 选择"自动更新"
4. 确认操作
5. 程序自动抓取、对比并更新（如有变化）

## 开始执行时间
2025-11-02

## 架构修正记录

### 问题识别
初版实现中，我将爬虫逻辑直接添加到了tariff_api.py中，违反了分层架构原则。tariff_api.py应该是纯数据库API层，不应该包含网络抓取和HTML解析等爬虫逻辑。

### 修正措施
1. **在scraper.py中新增auto_update_single()方法**：
   - 复用现有的抓取和解析逻辑
   - 使用TariffScraper的scrape_with_retry()和parse_commodity_page()方法
   - 实现智能对比和部分更新逻辑

2. **修改tariff_api.py**：
   - 移除所有爬虫相关的import
   - 删除重复的_parse_commodity_page方法
   - 修改auto_update()方法，仅作为调用层，委托给TariffScraper

3. **保持分层架构**：
   - tariff_api.py：纯数据库API层
   - scraper.py：爬虫和数据抓取层
   - tariff_gui.py：用户界面层

### 修正结果
- ✅ 架构更清晰，职责分离明确
- ✅ 避免代码重复，复用现有逻辑
- ✅ 易于维护和扩展
- ✅ 符合单一职责原则

## 完成状态
✅ 右键菜单更新完成
✅ 更新对话框实现完成
✅ 数据库更新方法完成
✅ 自动更新功能完成
✅ 架构修正完成（将爬虫逻辑移至scraper.py）
✅ 所有测试通过
