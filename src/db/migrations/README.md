# 数据迁移脚本

## 概述

此目录包含用于数据库架构升级和数据修复的迁移脚本。

## 可用的迁移脚本

### fix_missing_ni_urls.py

修复缺失的北爱尔兰 URL。

**问题**: 历史数据中部分记录缺失 `north_ireland_url` 字段。

**影响**: 414 条记录需要修复。

## 使用方法

### 演练模式（推荐先运行）

在执行实际修复前，先运行演练模式查看将要修改的记录：

```bash
python scripts/migrations/fix_missing_ni_urls.py --dry-run
```

这将显示：
- 缺失北爱尔兰 URL 的记录数量
- 前 10 条将被修复的记录预览

### 执行修复

确认无误后，执行实际修复（不加 `--dry-run`）：

```bash
python scripts/migrations/fix_missing_ni_urls.py
```

这将：
- 更新所有缺失 `north_ireland_url` 的记录
- 根据商品编码自动生成正确的北爱尔兰 URL
- 验证修复结果

### 指定数据库路径

默认使用当前目录的 `tariffs.db`，如需指定其他数据库：

```bash
python scripts/migrations/fix_missing_ni_urls.py --db-path /path/to/your/database.db
```

## URL 格式规范

### 英国 URL
```
https://www.trade-tariff.service.gov.uk/commodities/{code}
```

### 北爱尔兰 URL
```
https://www.trade-tariff.service.gov.uk/xi/commodities/{code}
```

注意北爱尔兰 URL 包含 `/xi/` 路径。

## 验证修复结果

使用 SQL 查询验证：

```bash
sqlite3 tariffs.db "SELECT COUNT(*) FROM tariffs WHERE north_ireland_url IS NULL OR north_ireland_url = ''"
```

期望结果: `0`

## 安全注意事项

1. **备份**: 执行迁移前建议备份数据库
2. **演练**: 优先使用 `--dry-run` 预览修改
3. **验证**: 迁移后验证数据完整性
4. **锁定**: 迁移期间数据库可能被锁定，避免并发操作

## 故障排查

### 错误: 数据库文件不存在

确保数据库文件路径正确：

```bash
python scripts/migrations/fix_missing_ni_urls.py --db-path tariffs.db
```

### 错误: 数据库被锁定

确保没有其他程序正在使用数据库文件。

## 开发

### 添加新的迁移脚本

1. 在此目录创建新的 Python 脚本
2. 实现迁移逻辑
3. 添加 `--dry-run` 演练模式支持
4. 编写单元测试（参考 `tests/test_migration_fix_ni_urls.py`）
5. 更新此 README 文档

### 测试迁移脚本

```bash
pytest tests/test_migration_fix_ni_urls.py -v
```
