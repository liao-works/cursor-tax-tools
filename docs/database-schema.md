# 数据库架构文档

## tariffs 表结构

| 字段名 | 类型 | 说明 | 是否必填 |
|--------|------|------|----------|
| code | TEXT | 商品编码（主键） | ✅ |
| description | TEXT | 商品描述 | ✅ |
| rate | TEXT | 英国税率 | ✅ |
| url | TEXT | 英国税率URL | ✅ (自动生成) |
| north_ireland_rate | TEXT | 北爱尔兰税率 | ❌ |
| north_ireland_url | TEXT | 北爱尔兰税率URL | ❌ (自动生成) |
| other_rate | TEXT | 其他税率 | ❌ |

## URL 格式规范

### 英国 URL
```
https://www.trade-tariff.service.gov.uk/commodities/{code}
```

### 北爱尔兰 URL
```
https://www.trade-tariff.service.gov.uk/xi/commodities/{code}
```

⚠️ **注意**: 北爱尔兰 URL 路径为 `/xi/commodities/`，不要与英国 URL 混淆。

## 数据完整性规则

1. **code**: 主键，唯一标识一个商品
2. **url**: 如果未提供，自动根据 code 生成
3. **north_ireland_url**: 如果未提供，自动根据 code 生成
4. **rate**: 英国税率，必填
5. **north_ireland_rate**: 北爱尔兰税率，可选

## 数据更新流程

### 新增记录
使用 `add_tariff()` 方法，会自动生成 URL：

```python
db.add_tariff(
    code="1234567890",
    description="示例商品",
    rate="5.00%"
)
# 自动生成:
# - url: https://www.trade-tariff.service.gov.uk/commodities/1234567890
# - north_ireland_url: https://www.trade-tariff.service.gov.uk/xi/commodities/1234567890
```

### 更新记录
使用 `update_tariff()` 方法，支持部分字段更新：

```python
db.update_tariff(
    code="1234567890",
    north_ireland_rate="8.00%"  # 只更新税率
)
```

### 删除记录
使用 `delete_tariff()` 方法：

```python
db.delete_tariff(code="1234567890")
```

## 常见问题

### Q: 为什么有些记录缺失 north_ireland_url?
A: 这是历史遗留问题。在旧版本中，`add_tariff()` 方法不支持北爱尔兰 URL。使用迁移脚本修复：
```bash
python scripts/migrations/fix_missing_ni_urls.py
```

### Q: 如何验证数据完整性?
A: 运行以下 SQL 查询：
```sql
-- 检查缺失 UK URL 的记录
SELECT COUNT(*) FROM tariffs WHERE url IS NULL OR url = '';

-- 检查缺失 NI URL 的记录
SELECT COUNT(*) FROM tariffs WHERE north_ireland_url IS NULL OR north_ireland_url = '';
```

### Q: 北爱尔兰 URL 的路径是什么？
A: 北爱尔兰 URL 使用 `/xi/commodities/` 路径，而不是 `/commodities/`。具体格式为：
```
https://www.trade-tariff.service.gov.uk/xi/commodities/{code}
```

### Q: 如何确保 URL 生成正确？
A: 系统会在以下情况自动生成正确的 URL：
1. 当 `add_tariff()` 调用时未提供 URL 参数
2. 使用 `north_ireland_url` 字段时，确保路径正确
3. 避免双斜杠错误（已修复）

### Q: 数据库索引有什么作用？
A: 当前数据库创建了 `idx_code` 索引，用于加速基于商品编码的查询。该索引在商品编码查询时显著提高性能。

### Q: 如何备份和恢复数据库？
A: 数据库文件是 SQLite 格式，可以直接复制备份：
```bash
# 备份
cp tariffs.db tariffs.db.backup

# 恢复
cp tariffs.db.backup tariffs.db
```