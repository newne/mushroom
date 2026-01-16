# Redis 连接超时问题修复

## 问题发现

部署后发现调度器在 `_start_scheduler()` 阶段超时：

```
[SCHEDULER] 建表操作完成 ✓
[SCHEDULER] 调度器初始化完成 ✓
[SCHEDULER] 信号处理器已注册 ✓
[SCHEDULER] 总共添加了 3 个任务 ✓
[SCHEDULER] 初始化失败: Timeout connecting to server ✗
```

## 根本原因

**调度器配置使用了 Redis 作为任务存储，但 Docker Compose 中没有 Redis 服务！**

### 问题分析

1. **调度器配置**：
```python
def _init_scheduler(self) -> BackgroundScheduler:
    job_stores = {"default": self._create_redis_jobstore()}  # ← 使用Redis存储
    scheduler = BackgroundScheduler(
        jobstores=job_stores,
        ...
    )
```

2. **Redis 配置**：
```python
def _create_redis_jobstore(self) -> RedisJobStore:
    return RedisJobStore(
        host=settings.redis.host,  # ← 尝试连接Redis
        port=settings.redis.port,
        password=settings.redis.password,
        socket_timeout=10,
        socket_connect_timeout=5,
    )
```

3. **Docker Compose**：
```yaml
services:
  postgres_db:  # ✓ 有PostgreSQL
    ...
  mushroom_solution:  # ✓ 有应用
    ...
  # ✗ 没有Redis服务！
```

4. **超时发生时机**：
   - `scheduler.start()` 被调用时
   - APScheduler 尝试连接 Redis 来持久化任务状态
   - Redis 不存在 → 连接超时

## 解决方案

### 为什么不需要 Redis？

本项目的调度任务特点：
- ✅ 所有任务都是固定的 cron 任务（每日、每小时）
- ✅ 任务配置在代码中定义，不需要动态添加
- ✅ 容器重启后任务会自动重新注册
- ✅ 不需要跨容器共享任务状态

**结论**：使用内存存储即可，无需 Redis 持久化。

### 代码修改

**文件**：`src/scheduling/optimized_scheduler.py`

#### 1. 移除 Redis 导入

```python
# 移除
from apscheduler.jobstores.redis import RedisJobStore
```

#### 2. 简化调度器初始化

```python
def _init_scheduler(self) -> BackgroundScheduler:
    """初始化调度器"""
    # 使用内存存储而非Redis（任务配置固定，无需持久化）
    job_defaults = {
        "misfire_grace_time": self.misfire_grace_time,
        "max_instances": self.max_job_instances,
        "coalesce": True,
        "replace_existing": True,
    }
    
    try:
        scheduler = BackgroundScheduler(
            timezone=self.timezone,
            job_defaults=job_defaults,
        )
        
        # 注册事件监听器
        scheduler.add_listener(
            exception_listener,
            events.EVENT_JOB_ERROR | events.EVENT_JOB_EXECUTED
        )
        
        # 注册到健康检查模块
        set_scheduler_instance(scheduler)
        
        logger.info("[SCHEDULER] 调度器初始化完成（使用内存存储）")
        return scheduler
    except Exception as e:
        logger.error(f"[SCHEDULER] 调度器初始化失败: {e}", exc_info=True)
        raise
```

#### 3. 移除 Redis 相关方法

删除 `_create_redis_jobstore()` 方法。

## 修复效果

### 修复前

```
1. 测试数据库连接 ✓
2. 执行建表操作 ✓
3. 初始化调度器（创建RedisJobStore） ✓
4. 添加任务 ✓
5. 启动调度器 → 连接Redis → Redis不存在 → 超时 ✗
```

### 修复后

```
1. 测试数据库连接 ✓
2. 执行建表操作 ✓
3. 初始化调度器（使用内存存储） ✓
4. 添加任务 ✓
5. 启动调度器 → 无需连接Redis → 立即成功 ✓
6. 进入主循环 ✓
```

## 内存存储 vs Redis 存储

| 特性 | 内存存储 | Redis 存储 |
|------|---------|-----------|
| 持久化 | ✗ 容器重启丢失 | ✓ 持久化到Redis |
| 性能 | ✓ 更快 | 稍慢（网络IO） |
| 依赖 | ✓ 无额外依赖 | ✗ 需要Redis服务 |
| 适用场景 | 固定任务配置 | 动态任务管理 |
| 复杂度 | ✓ 简单 | 更复杂 |

**本项目选择内存存储的原因**：
- 任务配置固定，在代码中定义
- 容器重启时任务会自动重新注册
- 无需动态添加/删除任务
- 减少外部依赖，降低复杂度

## 如果未来需要 Redis

如果将来需要 Redis（例如动态任务管理），可以：

### 1. 添加 Redis 服务到 Docker Compose

```yaml
services:
  redis:
    image: redis:7-alpine
    container_name: redis
    networks:
      - plant_backend
    ports:
      - "6379:6379"
    volumes:
      - redis_data:/data
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 10s
      timeout: 5s
      retries: 3

volumes:
  redis_data:
    driver: local
```

### 2. 恢复 Redis 配置

```python
def _create_redis_jobstore(self) -> RedisJobStore:
    return RedisJobStore(
        host=settings.redis.host,
        port=settings.redis.port,
        password=settings.redis.password,
        socket_timeout=10,
        socket_connect_timeout=5,
    )

def _init_scheduler(self) -> BackgroundScheduler:
    job_stores = {"default": self._create_redis_jobstore()}
    # ...
```

### 3. 添加 Redis 依赖

```bash
mushroom_solution:
  depends_on:
    postgres_db:
      condition: service_healthy
    redis:
      condition: service_healthy
```

## 预期日志输出

修复后应该看到：

```
[SCHEDULER] === 优化版调度器启动 ===
[SCHEDULER] 初始化调度器 (尝试 1/5)
[SCHEDULER] 测试数据库连接...
[SCHEDULER] 数据库连接测试成功
[SCHEDULER] 执行建表操作...
[TASK] 开始执行建表任务 (尝试 1/3)
[0.1.1] Tables created/verified successfully.
[TASK] 建表任务完成，耗时: 0.01秒
[SCHEDULER] 建表操作完成
[SCHEDULER] 调度器初始化完成（使用内存存储）  ← 新增提示
[SCHEDULER] 信号处理器已注册
[SCHEDULER] 每日环境统计任务已添加
[SCHEDULER] 每小时设定点监控任务已添加
[SCHEDULER] 每日CLIP推理任务已添加 (每天 03:02:25 执行)
[SCHEDULER] 总共添加了 3 个任务
[SCHEDULER] 调度器启动成功  ← 应该立即成功，无超时
[SCHEDULER] 调度器初始化成功，进入主循环
[SCHEDULER] 进入主循环
所有服务已成功启动。保持容器活跃中...
```

## 部署步骤

1. **构建新镜像**：
```bash
cd docker
./build.sh
```

2. **部署到服务器**：
```bash
IMAGE_TAG=<new_version> docker compose -f mushroom_solution.yml up -d
```

3. **验证日志**：
```bash
docker logs -f --tail 100 mushroom_solution
```

关键验证点：
- ✅ 看到 `调度器初始化完成（使用内存存储）`
- ✅ 看到 `调度器启动成功`（无超时）
- ✅ 看到 `进入主循环`
- ✅ 容器持续运行，不重启

## 技术要点

1. **APScheduler 存储机制**：
   - 默认使用内存存储（MemoryJobStore）
   - 可选 Redis、MongoDB、SQLAlchemy 等持久化存储
   - 内存存储足够满足大多数场景

2. **任务持久化的必要性**：
   - 动态添加的任务需要持久化
   - 固定配置的任务无需持久化
   - 容器重启会重新注册所有任务

3. **Docker 服务依赖**：
   - 使用外部服务前必须确保服务存在
   - `depends_on` 只控制启动顺序，不保证服务可用
   - 需要配合 `healthcheck` 确保服务就绪

## 相关文档

- `SCHEDULER_DB_CONNECTION_FIX.md` - 数据库连接修复
- `SCHEDULER_INITIALIZATION_FIX_V2.md` - 初始化流程优化
- `COMPLETE_FIX_SUMMARY.md` - 完整修复总结

---

**修复日期**: 2026-01-14  
**修复类型**: 移除不必要的 Redis 依赖  
**影响范围**: 调度器初始化逻辑  
**测试状态**: 代码验证通过，待部署测试
