# 📁 Scripts 目录说明

这个目录包含了 GitHub Actions 工作流和智能更新系统所需的所有脚本文件。

## 📂 目录结构

```
scripts/
├── actions/                    # GitHub Actions 专用脚本
│   ├── run_scraper.py         # 爬虫执行器（支持原版和优化版）
│   ├── generate_metadata.py   # 生成数据库元数据
│   └── validate_database.py   # 数据库验证脚本
│
├── scrapers/                   # 爬虫核心模块
│   ├── __init__.py            # 包初始化文件
│   └── tariff_scraper.py      # 关税数据爬虫（独立版本）
│
├── clients/                    # 客户端工具
│   └── smart_update_client.py # 智能更新客户端
│
├── monitoring/                 # 监控工具
│   ├── performance_monitor.py # 性能监控
│   └── simple_monitor.py      # 简易监控
│
├── tools/                      # 工具脚本
│   ├── cleanup_releases.py    # Release 清理工具
│   ├── create_latest_release.py # 创建最新 Release
│   └── scraper_config.json    # 配置文件
│
└── README.md                   # 本说明文件
```

## 🎯 职责划分

### GitHub Action 负责
- 📊 数据爬取：定时从英国海关网站爬取最新税率数据
- ✅ 数据验证：检查数据库完整性和质量
- 📝 元数据生成：生成版本、哈希等元信息
- 📦 发布更新：将数据库和元数据发布到 GitHub Release

### 客户端负责
- 🔍 检查更新：下载元数据判断是否需要更新
- ⬇️ 下载数据：从 GitHub Release 下载最新数据库
- 💾 本地更新：替换本地数据库文件
- 🔄 自动备份：更新前自动备份旧数据

## 🚀 核心脚本说明

### 1. actions/run_scraper.py
**用途**: 执行关税数据爬取，支持原版和优化版爬虫

```bash
# 环境变量控制
USE_OPTIMIZED=true
INPUT_UPDATE_UK=true
INPUT_UPDATE_NI=true
INPUT_BATCH_SIZE=100
INPUT_DELAY=0.2

python scripts/actions/run_scraper.py
```

**功能**:
- 支持原版和优化版爬虫自动切换
- 输出详细的统计信息
- 保存结果到 JSON 文件供后续步骤使用

### 2. actions/generate_metadata.py
**用途**: 生成数据库元数据文件，供客户端判断是否需要更新

```bash
python scripts/actions/generate_metadata.py tariffs.db data-123 update_results.json metadata.json
```

**功能**:
- 计算数据库文件哈希值
- 统计记录数量和数据质量
- 生成版本信息

### 3. actions/validate_database.py
**用途**: 验证数据库完整性和数据质量

```bash
python scripts/actions/validate_database.py tariffs.db
```

### 4. scrapers/tariff_scraper.py
**用途**: 爬虫核心模块（独立版本，可在 scripts/ 内独立使用）

```python
from scripts.scrapers.tariff_scraper import TariffScraper, BatchUpdateManager

# 使用爬虫
scraper = TariffScraper()
tariffs = await scraper.scrape_tariffs()

# 使用批量更新管理器
manager = BatchUpdateManager()
results = await manager.update_all_tariffs()
```

### 5. clients/smart_update_client.py
**用途**: 客户端智能更新工具

```bash
# 基本用法
python scripts/clients/smart_update_client.py \
  --metadata-url "https://github.com/owner/repo/releases/download/latest-data/metadata.json"

# 高级用法
python scripts/clients/smart_update_client.py \
  --metadata-url "https://github.com/owner/repo/releases/download/latest-data/metadata.json" \
  --db-path "tariffs.db" \
  --verbose \
  --dry-run
```

**功能**:
- 仅下载几 KB 的元数据进行判断
- 智能比较版本、哈希、时间戳
- 支持强制更新和模拟运行
- 自动备份和完整性验证

## 🔄 智能更新流程

### 服务端（GitHub Actions）

```yaml
# 工作流文件: .github/workflows/scrape-tariff.yml

# 1. 爬取数据
python scripts/actions/run_scraper.py

# 2. 验证数据库
python scripts/actions/validate_database.py tariffs.db

# 3. 生成元数据
python scripts/actions/generate_metadata.py tariffs.db "$VERSION" update_results.json

# 4. 发布到 Release（仅当有足够变更时）
# - metadata.json (几KB)
# - tariffs.db (完整数据库)
```

### 客户端使用

```python
from scripts.clients.smart_update_client import SmartUpdateChecker

checker = SmartUpdateChecker(
    metadata_url="https://github.com/owner/repo/releases/download/latest-data/metadata.json"
)
result = checker.check_and_update()

if result['status'] == 'updated':
    print("✅ 数据库已更新")
elif result['status'] == 'up_to_date':
    print("✨ 数据库已是最新版本")
```

## 📊 性能优化效果

| 指标 | 传统方式 | 智能更新方式 |
|------|----------|--------------|
| **检查速度** | 下载 7MB+ 数据库 | 下载几 KB 元数据 |
| **网络流量** | 每次检查 7MB+ | 仅需时 7MB+ |
| **响应时间** | 数十秒 | 毫秒级 |
| **用户体验** | 慢速等待 | 即时响应 |

## 🛠️ 故障排除

### 常见问题

1. **元数据下载失败**
   ```bash
   curl -I "https://github.com/owner/repo/releases/download/latest-data/metadata.json"
   ```

2. **爬虫执行失败**
   ```bash
   # 检查依赖
   pip install -r requirements.txt
   pip install backoff psutil prometheus-client structlog
   ```

3. **权限问题**
   ```bash
   chmod +x scripts/**/*.py
   ```

4. **GitHub CLI 认证**
   ```bash
   gh auth login
   ```

### 调试技巧

1. **启用详细输出**
   ```bash
   python scripts/clients/smart_update_client.py --verbose --metadata-url "..."
   ```

2. **模拟运行**
   ```bash
   python scripts/clients/smart_update_client.py --dry-run --metadata-url "..."
   ```

3. **检查系统资源**
   ```bash
   python scripts/monitoring/performance_monitor.py --check
   ```

---

**🚀 智能更新系统** - 高效、可靠、资源友好的数据更新解决方案
