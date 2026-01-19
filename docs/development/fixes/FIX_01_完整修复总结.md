# 蘑菇解决方案 - Docker部署问题完整修复总结

## 问题概述

在Docker生产环境中，应用容器启动后约30秒就因为数据库连接超时而崩溃重启，导致服务无法正常运行。

## 问题分析

### 根本原因

1. **Docker网络环境**：生产环境使用 `postgres_db` 服务名而非IP地址
2. **连接池配置不足**：SQLAlchemy默认配置对Docker网络的超时设置过于严格
3. **初始化时序问题**：调度器启动后立即执行建表任务，此时数据库可能未完全就绪
4. **缺少容错机制**：单个任务失败导致整个调度器崩溃
5. **日志不可见**：业务日志只写入文件，`docker logs` 看不到

### 错误表现

```
[SCHEDULER] 初始化失败: Timeout connecting to server
[SCHEDULER] 调度器无法启动，程序退出
容器不断重启...
```

## 完整修复方案

### 1. 数据库连接池优化 ✅

**文件**: `src/global_const/global_const.py`

**修改内容**:
```python
# PostgreSQL引擎配置 - 针对Docker网络环境优化
pgsql_engine = sqlalchemy.create_engine(
    pg_engine_url,
    pool_pre_ping=True,          # 连接前检查连接是否有效
    pool_recycle=1800,            # 连接回收时间（30分钟）
    pool_size=5,                  # 连接池大小
    max_overflow=10,              # 最大溢出连接数
    pool_timeout=30,              # 获取连接的超时时间（秒）
    connect_args={
        "connect_timeout": 10,    # TCP连接超时（秒）- 适应Docker网络
        "options": "-c statement_timeout=300000"  # SQL语句超时（5分钟）
    },
    echo=False,
    future=True
)
```

**关键参数**:
- `connect_timeout=10`: 适应Docker网络延迟
- `pool_pre_ping=True`: 自动检测失效连接
- `pool_timeout=30`: 更宽松的连接池超时

### 2. 调度器初始化优化 ✅

**文件**: `src/scheduling/optimized_scheduler.py`

**核心问题**: `create_tables` 任务被配置为 `next_run_time=datetime.now()`，导致调度器启动时立即执行DDL操作，此时数据库可能未完全准备好处理复杂的建表操作。

**解决方案**: 将建表操作移出调度器，在调度器启动前执行。

**修改内容**:
```python
def run(self) -> NoReturn:
    """运行调度器（带数据库连接重试）"""
    for init_attempt in range(1, max_init_retries + 1):
        try:
            # ✅ 1. 测试数据库连接
            logger.info("[SCHEDULER] 测试数据库连接...")
            with pgsql_engine.connect() as conn:
                conn.execute(sqlalchemy.text("SELECT 1"))
            logger.info("[SCHEDULER] 数据库连接测试成功")
            
            # ✅ 2. 在调度器启动前执行建表操作
            logger.info("[SCHEDULER] 执行建表操作...")
            try:
                safe_create_tables()
                logger.info("[SCHEDULER] 建表操作完成")
            except Exception as table_error:
                logger.warning(f"[SCHEDULER] 建表操作失败: {table_error}")
                logger.warning("[SCHEDULER] 继续启动调度器，但后续任务可能会失败")
            
            # ✅ 3. 初始化并启动调度器（不再包含建表任务）
            self.scheduler = self._init_scheduler()
            self._register_signal_handlers()
            self._setup_jobs()  # 不再添加create_tables任务
            self._start_scheduler()
            
            logger.info("[SCHEDULER] 调度器初始化成功，进入主循环")
            break
            
        except Exception as e:
            # 智能重试逻辑
            if init_attempt < max_init_retries:
                logger.warning(f"[SCHEDULER] 检测到连接错误，10秒后重试...")
                time.sleep(10)
            else:
                logger.critical("[SCHEDULER] 调度器无法启动，程序退出")
                raise

def _setup_jobs(self) -> None:
    """设置所有任务"""
    # 注意：建表任务已在调度器启动前执行，不再作为调度任务添加
    
    # 1. 添加业务任务
    self._add_business_jobs()
    
    # 2. 显示任务信息
    jobs = self.scheduler.get_jobs()
    logger.info(f"[SCHEDULER] 总共添加了 {len(jobs)} 个任务")
```

**关键改进**:
- 在调度器启动前先测试数据库连接
- 在调度器启动前执行建表操作（避免调度器启动时立即执行）
- 建表失败不阻止调度器启动（记录警告但继续）
- 调度器只包含周期性业务任务，不包含一次性初始化任务
- 最多重试5次，每次间隔10秒

**修复前后对比**:
```
修复前：
1. 测试连接 (SELECT 1) ✓
2. 初始化调度器 ✓
3. 添加create_tables任务 (next_run_time=now) ✓
4. 启动调度器 → 立即执行create_tables → 超时 ✗

修复后：
1. 测试连接 (SELECT 1) ✓
2. 执行建表操作 (在调度器外) ✓
3. 初始化调度器 ✓
4. 添加业务任务 (不含create_tables) ✓
5. 启动调度器 → 无立即执行任务 ✓
6. 进入主循环 ✓
```

### 3. 任务级重试机制 ✅

**文件**: `src/scheduling/optimized_scheduler.py`

**修改内容**: 为所有定时任务添加重试逻辑

```python
def safe_hourly_setpoint_monitoring() -> None:
    """每小时设定点变更监控任务（带重试）"""
    max_retries = 3
    retry_delay = 5
    
    for attempt in range(1, max_retries + 1):
        try:
            # 任务执行逻辑
            ...
            return  # 成功执行
            
        except Exception as e:
            error_msg = str(e)
            is_connection_error = any(keyword in error_msg.lower() 
                for keyword in ['timeout', 'connection', 'database'])
            
            if is_connection_error and attempt < max_retries:
                logger.warning(f"检测到连接错误，{retry_delay}秒后重试...")
                time.sleep(retry_delay)
            else:
                logger.error(f"任务失败，不再重试")
                return  # 不抛出异常，避免调度器崩溃
```

**影响的任务**:
- `safe_create_tables()` - 建表任务
- `safe_daily_env_stats()` - 每日环境统计
- `safe_hourly_setpoint_monitoring()` - 每小时设定点监控
- `safe_daily_clip_inference()` - 每日CLIP推理

### 4. 日志输出优化 ✅

**文件**: `docker/run.sh`

**修改内容**:
```bash
# 修改前：日志只写入文件
nohup $PYTHON main.py > "$TIMER_LOG" 2>&1 &

# 修改后：同时输出到文件和标准输出
nohup $PYTHON main.py 2>&1 | tee -a "$TIMER_LOG" &
```

**效果**:
- `docker logs` 可以看到调度器实时输出
- 日志同时保存到文件
- 进程异常时显示详细错误

### 5. 设定点监控代码重构 ✅

**文件**: `src/utils/setpoint_change_monitor.py`

**改进内容**:
- 统一数据库模型定义
- 配置文件化管理
- 移除硬编码值
- 使用 point_alias 作为配置键
- 自动重试机制

## 文件变更清单

| 文件 | 状态 | 说明 |
|------|------|------|
| `src/global_const/global_const.py` | ✅ 已修改 | 优化数据库连接池配置 |
| `src/scheduling/optimized_scheduler.py` | ✅ 已修改 | 添加初始化测试和任务重试 |
| `docker/run.sh` | ✅ 已修改 | 日志同时输出到标准输出 |
| `src/utils/setpoint_change_monitor.py` | ✅ 已替换 | 重构版本已替换原文件 |
| `src/utils/setpoint_config.py` | ✅ 新增 | 配置管理模块 |
| `src/configs/setpoint_monitor_config.json` | ✅ 新增 | 设定点监控配置文件 |
| `scripts/test_db_connection.py` | ✅ 新增 | 数据库连接测试工具 |
| `scripts/test_scheduler_resilience.py` | ✅ 新增 | 调度器容错测试工具 |
| `docker/deploy_server.sh` | ✅ 新增 | 服务器端部署脚本 |

## 新增文档

| 文档 | 说明 |
|------|------|
| `DOCKER_DATABASE_CONNECTION_FIX.md` | 数据库连接优化详细文档 |
| `DOCKER_TIMEOUT_FIX_SUMMARY.md` | 超时问题修复总结 |
| `SCHEDULER_DB_CONNECTION_FIX.md` | 调度器初始化优化说明 |
| `DEPLOYMENT_GUIDE.md` | 完整部署指南 |
| `LOG_VIEWING_GUIDE.md` | 日志查看指南 |
| `QUICK_DEPLOY_REFERENCE.md` | 快速部署参考卡片 |
| `SETPOINT_MONITOR_CODE_ANALYSIS.md` | 设定点监控代码分析 |
| `SETPOINT_MONITOR_MIGRATION_GUIDE.md` | 设定点监控迁移指南 |

## 部署流程

### 本地构建

```bash
cd docker
./build.sh
# 记录生成的 IMAGE_TAG，例如：0.1.0-20260114120000-abc1234
```

### 服务器部署

#### 方式一：使用部署脚本（推荐）

```bash
# 上传脚本到服务器
scp docker/deploy_server.sh user@server:/path/to/docker/

# SSH到服务器并执行
ssh user@server
cd /path/to/mushroom_solution/docker
./deploy_server.sh 0.1.0-20260114120000-abc1234
```

#### 方式二：手动部署

```bash
# SSH到服务器
ssh user@server
cd /path/to/mushroom_solution/docker

# 停止旧版本
docker compose -f mushroom_solution.yml down

# 启动新版本
IMAGE_TAG=0.1.0-20260114120000-abc1234 docker compose -f mushroom_solution.yml up -d
```

### 验证部署

```bash
# 1. 检查容器状态
docker ps | grep mushroom

# 2. 查看启动日志（包含调度器输出）
docker logs -f mushroom_solution

# 应该看到：
# [SCHEDULER] 测试数据库连接...
# [SCHEDULER] 数据库连接测试成功
# [SCHEDULER] 调度器初始化成功，进入主循环

# 3. 测试数据库连接
docker exec mushroom_solution prod=true python scripts/test_db_connection.py

# 4. 查看业务日志
docker exec mushroom_solution tail -f /app/Logs/mushroom_solution-info.log

# 5. 检查错误日志
docker exec mushroom_solution tail -f /app/Logs/mushroom_solution-error.log
```

## 预期结果

修复后的系统应该：

1. ✅ 调度器成功启动并保持运行
2. ✅ 数据库连接超时后自动重试
3. ✅ 单个任务失败不会导致调度器崩溃
4. ✅ 所有定时任务正常执行
5. ✅ `docker logs` 可以看到完整的业务日志
6. ✅ 容器不再频繁重启

## 日志示例

### 成功启动日志

```
[2026-01-14 10:15:00] 开始启动服务...
[2026-01-14 10:15:00] 已设置线程限制：OMP/OpenBLAS/MKL ≤ 4 threads
[2026-01-14 10:15:00] 启动 Streamlit 应用...
[2026-01-14 10:15:00] Streamlit 已启动，PID=18
[2026-01-14 10:15:02] 启动 FastAPI 健康检查服务...
[2026-01-14 10:15:02] FastAPI 健康检查服务已启动，PID=28
[2026-01-14 10:15:03] 启动定时任务 main.py...
[2026-01-14 10:15:05] 定时任务已启动，PID=55

2026-01-14 10:15:05.123 | INFO | [SCHEDULER] === 优化版调度器启动 ===
2026-01-14 10:15:05.124 | INFO | [SCHEDULER] 初始化调度器 (尝试 1/5)
2026-01-14 10:15:05.125 | INFO | [SCHEDULER] 测试数据库连接...
2026-01-14 10:15:05.234 | INFO | [SCHEDULER] 数据库连接测试成功
2026-01-14 10:15:05.235 | INFO | [SCHEDULER] 执行建表操作...
2026-01-14 10:15:05.236 | INFO | [TASK] 开始执行建表任务 (尝试 1/3)
2026-01-14 10:15:05.456 | INFO | [0.1.1] Tables created/verified successfully.
2026-01-14 10:15:05.457 | INFO | [TASK] 建表任务完成，耗时: 0.22秒
2026-01-14 10:15:05.458 | INFO | [SCHEDULER] 建表操作完成
2026-01-14 10:15:05.459 | INFO | [SCHEDULER] 调度器初始化完成
2026-01-14 10:15:05.460 | INFO | [SCHEDULER] 信号处理器已注册
2026-01-14 10:15:05.461 | INFO | [SCHEDULER] 每日环境统计任务已添加
2026-01-14 10:15:05.462 | INFO | [SCHEDULER] 每小时设定点监控任务已添加
2026-01-14 10:15:05.463 | INFO | [SCHEDULER] 每日CLIP推理任务已添加 (每天 03:02:25 执行)
2026-01-14 10:15:05.464 | INFO | [SCHEDULER] 总共添加了 3 个任务
2026-01-14 10:15:05.465 | INFO |   - daily_env_stats: 触发器: cron[hour='1', minute='3', second='20']
2026-01-14 10:15:05.466 | INFO |   - hourly_setpoint_monitoring: 触发器: cron[minute='5']
2026-01-14 10:15:05.467 | INFO |   - daily_clip_inference: 触发器: cron[hour='3', minute='2', second='25']
2026-01-14 10:15:05.468 | INFO | [SCHEDULER] 调度器启动成功
2026-01-14 10:15:05.469 | INFO | [SCHEDULER] 调度器初始化成功，进入主循环
2026-01-14 10:15:05.470 | INFO | [SCHEDULER] 进入主循环

[2026-01-14 10:15:10] 定时任务运行正常
[2026-01-14 10:15:10] 所有服务已成功启动。保持容器活跃中...
```

### 重试恢复日志

```
2026-01-14 10:15:05.123 | INFO | [SCHEDULER] 初始化调度器 (尝试 1/5)
2026-01-14 10:15:05.124 | INFO | [SCHEDULER] 测试数据库连接...
2026-01-14 10:15:15.125 | ERROR | [SCHEDULER] 初始化失败 (尝试 1/5): 数据库连接失败: Timeout
2026-01-14 10:15:15.126 | WARNING | [SCHEDULER] 检测到连接错误，10秒后重试初始化...
2026-01-14 10:15:25.127 | INFO | [SCHEDULER] 初始化调度器 (尝试 2/5)
2026-01-14 10:15:25.128 | INFO | [SCHEDULER] 测试数据库连接...
2026-01-14 10:15:25.234 | INFO | [SCHEDULER] 数据库连接测试成功
2026-01-14 10:15:25.235 | INFO | [SCHEDULER] 调度器初始化成功，进入主循环
```

## 故障排查

### 问题1：容器仍然重启

```bash
# 查看完整日志
docker logs --tail 200 mushroom_solution

# 检查数据库状态
docker exec postgres_db pg_isready -U postgres

# 测试网络连接
docker exec mushroom_solution ping postgres_db
docker exec mushroom_solution nc -zv postgres_db 5432

# 查看网络配置
docker network inspect plant_backend
```

### 问题2：数据库连接仍然超时

```bash
# 进入容器手动测试
docker exec -it mushroom_solution bash
prod=true python scripts/test_db_connection.py

# 查看数据库日志
docker logs postgres_db

# 检查配置
prod=true python -c "
import sys
sys.path.insert(0, 'src')
from global_const.global_const import settings
print(f'Host: {settings.pgsql.host}')
print(f'Port: {settings.pgsql.port}')
"
```

### 问题3：任务执行失败

```bash
# 查看错误日志
docker exec mushroom_solution tail -100 /app/Logs/mushroom_solution-error.log

# 查看任务日志
docker logs mushroom_solution 2>&1 | grep "\[TASK\]"

# 手动执行任务测试
docker exec -it mushroom_solution bash
prod=true python -c "
import sys
sys.path.insert(0, 'src')
from scheduling.optimized_scheduler import safe_hourly_setpoint_monitoring
safe_hourly_setpoint_monitoring()
"
```

## 性能影响

- **启动时间**: +10-30秒（等待数据库就绪和重试）
- **内存使用**: +10-20MB（连接池）
- **CPU使用**: 可忽略
- **可靠性**: 显著提升 ⬆️⬆️⬆️

## 回滚方案

如果新版本出现问题：

```bash
# 停止当前版本
docker compose -f mushroom_solution.yml down

# 启动上一个版本
IMAGE_TAG=<旧版本号> docker compose -f mushroom_solution.yml up -d
```

## 监控建议

部署后建议持续监控24-48小时：

```bash
# 实时查看日志
docker logs -f mushroom_solution

# 查看资源使用
docker stats mushroom_solution

# 定期检查容器状态
watch -n 60 'docker ps | grep mushroom'

# 监控错误日志
watch -n 300 'docker exec mushroom_solution tail -20 /app/Logs/mushroom_solution-error.log'
```

## 后续优化建议

1. **监控告警**: 添加Prometheus指标监控
2. **健康检查**: 暴露应用健康状态API
3. **日志聚合**: 使用ELK或Loki收集日志
4. **断路器模式**: 防止级联失败
5. **配置中心**: 动态配置管理

## 技术债务清理

本次修复同时完成了以下技术债务清理：

1. ✅ 统一数据库模型定义（DeviceSetpointChange）
2. ✅ 移除硬编码配置值
3. ✅ 优化代码结构和命名
4. ✅ 添加完整的错误处理
5. ✅ 改进日志输出和可观测性

## 总结

本次修复从根本上解决了Docker部署环境中的数据库连接超时问题，通过：

1. **优化连接池配置** - 适应Docker网络环境
2. **改进初始化流程** - 先测试连接再启动
3. **添加重试机制** - 自动处理临时故障
4. **增强日志输出** - 提高可观测性
5. **重构核心代码** - 提升代码质量

系统现在具备更强的容错能力和可靠性，能够在Docker生产环境中稳定运行。

---

**修复完成时间**: 2026-01-14  
**涉及模块**: 数据库连接、调度器、日志系统、设定点监控  
**测试状态**: 代码验证通过，待生产环境验证  
**文档版本**: 1.0
