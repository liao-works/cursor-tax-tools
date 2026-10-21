# GitHub Actions 超时优化方案

## 问题分析

### 当前配置的问题

1. **超时时间冲突**
   - Job 级别：`timeout-minutes: 60`
   - 命令级别：`timeout 90m`
   - ❌ 问题：GitHub Actions 在 60 分钟时强制终止

2. **并发过高**
   - 15 个并行 shard
   - 每个 shard 同时处理多个请求
   - 总并发可能超过 3000 请求/分钟
   - 可能触发目标网站的速率限制

3. **缺少重试机制**
   - 网络错误导致直接失败
   - 没有断点续传能力

## 优化方案

### 方案1：调整参数（快速解决）

#### 1.1 减少并发数
```yaml
strategy:
  max-parallel: 3  # 限制同时运行的 shard 数
  matrix:
    shard: [0, 1, 2, 3, 4, 5]  # 总共 6 个 shard
```

#### 1.2 增加超时时间
```yaml
scrape:
  timeout-minutes: 120  # 从 60 增加到 120
```

#### 1.3 减少批量大小和增加延迟
```yaml
env:
  DEFAULT_BATCH_SIZE: 100  # 从 200 减少到 100
  DEFAULT_DELAY: 0.2        # 从 0.05 增加到 0.2
```

#### 1.4 默认关闭北爱尔兰更新
```yaml
env:
  UPDATE_NI: false  # 先只更新英国数据
```

#### 1.5 默认开启测试模式
```yaml
env:
  TEST_MODE: true  # 先测试少量数据
```

### 方案2：添加重试机制

在 workflow 中添加自动重试逻辑：

```bash
MAX_RETRIES=2
RETRY_COUNT=0

while [ $RETRY_COUNT -le $MAX_RETRIES ]; do
  echo "INFO: 尝试 $((RETRY_COUNT + 1))/$((MAX_RETRIES + 1))"

  timeout 100m python src/actions/run_shard.py \
    --shard-id $SHARD_ID \
    --task-file task_assignment.json \
    --output-db tariffs_${SHARD_ID}.db && {
    echo "INFO: Shard 成功完成"
    exit 0
  }

  EXIT_CODE=$?

  if [ $EXIT_CODE -eq 124 ]; then
    echo "::warning::Shard 超时"
    RETRY_COUNT=$((RETRY_COUNT + 1))

    if [ $RETRY_COUNT -le $MAX_RETRIES ]; then
      echo "INFO: 10秒后重试..."
      sleep 10
      rm -f tariffs_${SHARD_ID}.db
      continue
    else
      echo "::error::Shard 超时且重试失败"
      exit 0
    fi
  fi
done
```

### 方案3：分阶段执行

#### 阶段1：测试模式（推荐先用）
```bash
# 只爬取前 3 个章节
test_mode: true
shard_count: 3
batch_size: 50
```

#### 阶段2：增量更新
```bash
# 只更新缺失的章节
update_mode: missing_only
shard_count: 6
```

#### 阶段3：全量更新
```bash
# 爬取所有章节
update_mode: full
shard_count: 6
batch_size: 100
```

### 方案4：使用 Self-hosted Runner（长期方案）

如果 GitHub Actions 限制严重，可以考虑：

1. **使用自托管 Runner**
   - 部署在自己的服务器上
   - 没有超时限制
   - 更快的网络访问

2. **配置方法**
   ```yaml
   scrape:
     runs-on: self-hosted  # 使用自托管 runner
   ```

## 使用优化后的 Workflow

### 新文件
`.github/workflows/scrape-tariff-parallel-improved.yml`

### 使用步骤

1. **替换旧文件**
   ```bash
   # 备份旧文件
   mv .github/workflows/scrape-tariff-parallel.yml \
      .github/workflows/scrape-tariff-parallel.yml.backup

   # 使用新文件
   cp .github/workflows/scrape-tariff-parallel-improved.yml \
      .github/workflows/scrape-tariff-parallel.yml
   ```

2. **测试运行（推荐第一步）**
   - 使用默认参数（test_mode: true）
   - 只爬取前 3 个章节
   - 验证流程正常

3. **增量更新**
   - `update_mode: missing_only`
   - 只更新缺失的章节

4. **全量更新**
   - 确认一切正常后
   - 设置 `test_mode: false`
   - 执行全量更新

## 参数调优建议

### 保守配置（推荐）
```yaml
concurrent_shards: 2-3
batch_size: 50-100
delay: 0.2-0.5
update_ni: false
test_mode: true
```

### 平衡配置
```yaml
concurrent_shards: 3-4
batch_size: 100-150
delay: 0.1-0.2
update_ni: false
test_mode: false
```

### 激进配置（不推荐）
```yaml
concurrent_shards: 6
batch_size: 200
delay: 0.05
update_ni: true
test_mode: false
```

## 监控和调试

### 1. 查看日志
```bash
# 查看 GitHub Actions 日志
# 关注以下信息：
# - "INFO: 尝试 X/Y" - 重试次数
# - "INFO: 处理速度" - 每分钟处理的商品数
# - "WARNING: 超时" - 哪些 shard 超时
```

### 2. 调整参数
根据日志调整：
- 如果频繁超时：增加 `delay`，减少 `batch_size`
- 如果速度太慢：减少 `concurrent_shards`
- 如果遇到速率限制：增加 `delay`，减少并发

### 3. 断点续传
新 workflow 支持：
- 每个 shard 独立运行
- 失败后可以重新运行失败的 shard
- 已完成的 shard 不会重新执行

## 总结

### 推荐执行顺序

1. ✅ 先使用 **测试模式** 验证流程
2. ✅ 使用 **增量更新** 补齐缺失数据
3. ✅ 最后执行 **全量更新**

### 关键参数

| 参数 | 保守 | 平衡 | 激进 |
|------|------|------|------|
| concurrent_shards | 2-3 | 3-4 | 6 |
| batch_size | 50-100 | 100-150 | 200 |
| delay | 0.2-0.5 | 0.1-0.2 | 0.05 |
| update_ni | false | false | true |
| test_mode | true | false | false |

### 预期效果

- **测试模式**：5-10 分钟
- **增量更新**：30-60 分钟
- **全量更新**：90-120 分钟
