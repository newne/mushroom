# 日志查看指南

## 日志架构

蘑菇解决方案使用多层日志系统：

```
┌─────────────────────────────────────────────────────────┐
│                    docker logs                          │
│  (容器标准输出 - 包含启动脚本和调度器输出)                │
└─────────────────────────────────────────────────────────┘
                          │
                          ├─ 启动脚本日志 (run.sh)
                          ├─ 调度器日志 (实时输出)
                          └─ 进程监控日志
                          
┌─────────────────────────────────────────────────────────┐
│              /app/Logs/ (容器内日志文件)                 │
│  (详细的业务日志 - 按级别和时间分割)                      │
└─────────────────────────────────────────────────────────┘
                          │
                          ├─ mushroom_solution-info.log
                          ├─ mushroom_solution-error.log
                          ├─ mushroom_solution-warning.log
                          ├─ mushroom_solution-debug.log
                          ├─ timer.log (定时任务输出)
                          ├─ streamlit.log
                          └─ fastapi.log
```

## 快速查看命令

### 1. 查看容器日志（推荐首选）

```bash
# 查看最近100行日志（包含调度器输出）
docker logs --tail 100 mushroom_solution

# 实时跟踪日志
docker logs -f mushroom_solution

# 查看最近5分钟的日志
docker logs --since 5m mushroom_solution

# 查看特定时间段的日志
docker logs --since "2026-01-14T10:00:00" --until "2026-01-14T11:00:00" mushroom_solution

# 只看错误输出
docker logs mushroom_solution 2>&1 | grep -i error

# 搜索特定关键词
docker logs mushroom_solution 2>&1 | grep "调度器"
```

### 2. 查看容器内业务日志

```bash
# 查看INFO级别日志
docker exec mushroom_solution tail -f /app/Logs/mushroom_solution-info.log

# 查看ERROR级别日志
docker exec mushroom_solution tail -f /app/Logs/mushroom_solution-error.log

# 查看WARNING级别日志
docker exec mushroom_solution tail -f /app/Logs/mushroom_solution-warning.log

# 查看DEBUG级别日志（详细调试信息）
docker exec mushroom_solution tail -f /app/Logs/mushroom_solution-debug.log

# 查看定时任务日志
docker exec mushroom_solution tail -f /app/Logs/timer.log

# 查看Streamlit日志
docker exec mushroom_solution tail -f /app/Logs/streamlit.log

# 查看FastAPI日志
docker exec mushroom_solution tail -f /app/Logs/fastapi.log
```

### 3. 查看历史日志文件

```bash
# 列出所有日志文件
docker exec mushroom_solution ls -lh /app/Logs/

# 查看归档的日志文件
docker exec mushroom_solution ls -lh /app/Logs/*.log.*

# 查看特定日期的日志
docker exec mushroom_solution cat /app/Logs/mushroom_solution-info.2026-01-14_10-00-00_123456.log
```

## 日志级别说明

| 级别 | 文件 | 内容 | 用途 |
|------|------|------|------|
| DEBUG | mushroom_solution-debug.log | 详细的调试信息 | 开发调试 |
| INFO | mushroom_solution-info.log | 一般信息 | 日常监控 |
| WARNING | mushroom_solution-warning.log | 警告信息 | 潜在问题 |
| ERROR | mushroom_solution-error.log | 错误信息 | 故障排查 |
| CRITICAL | mushroom_solution-critical.log | 严重错误 | 紧急处理 |

## 常见日志查看场景

### 场景1：检查服务是否正常启动

```bash
# 查看启动日志
docker logs --tail 50 mushroom_solution

# 应该看到：
# [SCHEDULER] 调度器初始化成功，进入主循环
# [2026-01-14 10:10:56] 所有服务已成功启动
```

### 场景2：调度器任务执行情况

```bash
# 实时监控调度器日志
docker logs -f mushroom_solution | grep "\[SCHEDULER\]\|\[TASK\]"

# 或查看业务日志
docker exec mushroom_solution tail -f /app/Logs/mushroom_solution-info.log | grep "TASK"
```

### 场景3：排查数据库连接问题

```bash
# 搜索连接相关日志
docker logs mushroom_solution 2>&1 | grep -i "connect\|timeout\|database"

# 查看错误日志
docker exec mushroom_solution tail -100 /app/Logs/mushroom_solution-error.log
```

### 场景4：查看定时任务执行记录

```bash
# 查看每日环境统计任务
docker logs mushroom_solution 2>&1 | grep "daily_env_stats"

# 查看设定点监控任务
docker logs mushroom_solution 2>&1 | grep "setpoint_monitoring"

# 查看CLIP推理任务
docker logs mushroom_solution 2>&1 | grep "CLIP_TASK"
```

### 场景5：监控系统性能

```bash
# 查看任务执行时间
docker logs mushroom_solution 2>&1 | grep "耗时"

# 查看处理统计
docker logs mushroom_solution 2>&1 | grep "处理.*库房\|检测到.*变更"
```

## 日志导出

### 导出到本地文件

```bash
# 导出容器日志
docker logs mushroom_solution > mushroom_$(date +%Y%m%d_%H%M%S).log 2>&1

# 导出业务日志
docker exec mushroom_solution cat /app/Logs/mushroom_solution-info.log > info_$(date +%Y%m%d_%H%M%S).log

# 导出错误日志
docker exec mushroom_solution cat /app/Logs/mushroom_solution-error.log > error_$(date +%Y%m%d_%H%M%S).log

# 打包所有日志
docker exec mushroom_solution tar -czf /tmp/logs_$(date +%Y%m%d_%H%M%S).tar.gz /app/Logs/
docker cp mushroom_solution:/tmp/logs_*.tar.gz .
```

### 从容器复制日志文件

```bash
# 复制单个日志文件
docker cp mushroom_solution:/app/Logs/mushroom_solution-info.log ./

# 复制整个日志目录
docker cp mushroom_solution:/app/Logs/ ./logs_backup/
```

## 日志分析技巧

### 1. 统计错误数量

```bash
# 统计错误日志行数
docker exec mushroom_solution wc -l /app/Logs/mushroom_solution-error.log

# 统计特定错误
docker logs mushroom_solution 2>&1 | grep -c "Timeout"
```

### 2. 查找特定时间段的日志

```bash
# 查找10:00-11:00的日志
docker logs mushroom_solution 2>&1 | grep "2026-01-14 10:"

# 查找最近1小时的错误
docker logs --since 1h mushroom_solution 2>&1 | grep -i error
```

### 3. 分析任务执行频率

```bash
# 统计设定点监控任务执行次数
docker logs mushroom_solution 2>&1 | grep -c "开始执行设定点变更监控"

# 查看任务执行时间分布
docker logs mushroom_solution 2>&1 | grep "开始执行" | awk '{print $1, $2}'
```

### 4. 监控重试情况

```bash
# 查看重试日志
docker logs mushroom_solution 2>&1 | grep "重试\|retry"

# 统计重试次数
docker logs mushroom_solution 2>&1 | grep -c "检测到连接错误"
```

## 日志清理

### 查看日志大小

```bash
# 查看容器日志大小
docker inspect mushroom_solution --format='{{.LogPath}}' | xargs ls -lh

# 查看业务日志大小
docker exec mushroom_solution du -sh /app/Logs/
docker exec mushroom_solution du -h /app/Logs/*.log
```

### 清理旧日志

```bash
# 清理7天前的归档日志
docker exec mushroom_solution find /app/Logs -name "*.log.*" -mtime +7 -delete

# 清理容器日志（需要重启容器）
docker compose -f mushroom_solution.yml restart mushroom_solution
```

## 日志监控告警

### 设置日志监控脚本

```bash
#!/bin/bash
# log_monitor.sh - 监控错误日志并告警

ERROR_THRESHOLD=10
ERROR_COUNT=$(docker logs --since 1h mushroom_solution 2>&1 | grep -c "ERROR")

if [ $ERROR_COUNT -gt $ERROR_THRESHOLD ]; then
    echo "警告: 最近1小时内发现 $ERROR_COUNT 个错误"
    # 发送告警通知
    # curl -X POST "your-alert-webhook" -d "errors=$ERROR_COUNT"
fi
```

### 定期检查脚本

```bash
#!/bin/bash
# health_check.sh - 定期健康检查

# 检查调度器是否正常
if ! docker logs --since 5m mushroom_solution 2>&1 | grep -q "调度器"; then
    echo "警告: 调度器可能已停止"
fi

# 检查数据库连接
if docker logs --since 5m mushroom_solution 2>&1 | grep -q "Timeout connecting"; then
    echo "警告: 数据库连接超时"
fi
```

## 进入容器查看日志

```bash
# 进入容器
docker exec -it mushroom_solution bash

# 在容器内查看日志
cd /app/Logs
ls -lh
tail -f mushroom_solution-info.log

# 使用 less 查看大文件
less mushroom_solution-info.log

# 搜索日志内容
grep "关键词" mushroom_solution-info.log

# 退出容器
exit
```

## 日志格式说明

### 容器日志格式

```
[2026-01-14 10:10:51] 定时任务已启动，PID=55
[SCHEDULER] === 优化版调度器启动 ===
[TASK] 开始执行设定点变更监控
```

### 业务日志格式（Loguru）

```
2026-01-14 10:10:51.123 | INFO     | scheduling.optimized_scheduler:run:407 - [SCHEDULER] === 优化版调度器启动 ===
│                       │          │                                         │
│                       │          │                                         └─ 日志消息
│                       │          └─ 模块:函数:行号
│                       └─ 日志级别
└─ 时间戳
```

## 故障排查清单

当遇到问题时，按以下顺序查看日志：

1. ✅ **容器日志** - 查看整体运行状态
   ```bash
   docker logs --tail 100 mushroom_solution
   ```

2. ✅ **错误日志** - 查找具体错误
   ```bash
   docker exec mushroom_solution tail -50 /app/Logs/mushroom_solution-error.log
   ```

3. ✅ **INFO日志** - 了解执行流程
   ```bash
   docker exec mushroom_solution tail -100 /app/Logs/mushroom_solution-info.log
   ```

4. ✅ **定时任务日志** - 检查任务执行
   ```bash
   docker exec mushroom_solution tail -50 /app/Logs/timer.log
   ```

5. ✅ **数据库日志** - 排查数据库问题
   ```bash
   docker logs postgres_db
   ```

---

**提示**: 建议将常用的日志查看命令保存为别名或脚本，方便日常使用！
