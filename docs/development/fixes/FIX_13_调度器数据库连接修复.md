# 调度器数据库连接超时修复总结

## 问题描述

调度器在Docker环境中启动时持续失败，错误信息为：
```
[SCHEDULER] 初始化失败: Timeout connecting to server
```

即使添加了数据库连接测试，调度器仍然在 `_start_scheduler()` 阶段超时。

## 根本原因分析

经过深入分析日志，发现问题的根本原因：

1. **数据库连接测试成功**：简单的 `SELECT 1` 查询可以成功执行
2. **调度器初始化成功**：调度器对象创建和任务添加都成功
3. **启动时立即超时**：当调用 `scheduler.start()` 时，`create_tables` 任务立即执行并超时

**关键发现**：
- `create_tables` 任务被配置为 `next_run_time=datetime.now()`，意味着调度器启动时**立即执行**
- 建表操作涉及复杂的DDL语句（CREATE TABLE, CREATE INDEX, CREATE EXTENSION等）
- 这些DDL操作比简单的连接测试需要更长时间，且在Docker网络环境下可能遇到延迟
- 调度器启动时立即执行建表任务，导致在数据库还未完全准备好时就超时

## 解决方案

### 核心修改：将建表操作移出调度器

**修改文件**：`src/scheduling/optimized_scheduler.py`

**关键改动**：

1. **在调度器启动前执行建表**：
```python
def run(self) -> NoReturn:
    # ... 初始化重试循环 ...
    
    # 1. 测试数据库连接
    logger.info("[SCHEDULER] 测试数据库连接...")
    with pgsql_engine.connect() as conn:
        conn.execute(sqlalchemy.text("SELECT 1"))
    logger.info("[SCHEDULER] 数据库连接测试成功")
    
    # 2. 在调度器启动前执行建表操作
    logger.info("[SCHEDULER] 执行建表操作...")
    try:
        safe_create_tables()
        logger.info("[SCHEDULER] 建表操作完成")
    except Exception as table_error:
        logger.warning(f"[SCHEDULER] 建表操作失败: {table_error}")
        logger.warning("[SCHEDULER] 继续启动调度器，但后续任务可能会失败")
    
    # 3. 初始化并启动调度器（不再包含建表任务）
    self.scheduler = self._init_scheduler()
    self._register_signal_handlers()
    self._setup_jobs()  # 不再添加create_tables任务
    self._start_scheduler()
```

2. **移除调度任务中的建表任务**：
```python
def _setup_jobs(self) -> None:
    """设置所有任务"""
    # 注意：建表任务已在调度器启动前执行，不再作为调度任务添加
    
    # 1. 添加业务任务
    self._add_business_jobs()
    
    # 2. 显示任务信息
    jobs = self.scheduler.get_jobs()
    logger.info(f"[SCHEDULER] 总共添加了 {len(jobs)} 个任务")
```

## 修复效果

### 修复前的执行流程：
```
1. 测试连接 (SELECT 1) ✓
2. 初始化调度器 ✓
3. 添加create_tables任务 (next_run_time=now) ✓
4. 启动调度器 → 立即执行create_tables → 超时 ✗
```

### 修复后的执行流程：
```
1. 测试连接 (SELECT 1) ✓
2. 执行建表操作 (在调度器外) ✓
3. 初始化调度器 ✓
4. 添加业务任务 (不含create_tables) ✓
5. 启动调度器 → 无立即执行任务 ✓
6. 进入主循环 ✓
```

## 预期日志输出

成功启动后应该看到：
```
[SCHEDULER] === 优化版调度器启动 ===
[SCHEDULER] 初始化调度器 (尝试 1/5)
[SCHEDULER] 测试数据库连接...
[SCHEDULER] 数据库连接测试成功
[SCHEDULER] 执行建表操作...
[TASK] 开始执行建表任务 (尝试 1/3)
[0.1.1] Tables created/verified successfully.
[TASK] 建表任务完成，耗时: X.XX秒
[SCHEDULER] 建表操作完成
[SCHEDULER] 调度器初始化完成
[SCHEDULER] 信号处理器已注册
[SCHEDULER] 每日环境统计任务已添加
[SCHEDULER] 每小时设定点监控任务已添加
[SCHEDULER] 每日CLIP推理任务已添加 (每天 03:02:25 执行)
[SCHEDULER] 总共添加了 3 个任务
  - daily_env_stats: 触发器: cron[hour='1', minute='3', second='20']
  - hourly_setpoint_monitoring: 触发器: cron[minute='5']
  - daily_clip_inference: 触发器: cron[hour='3', minute='2', second='25']
[SCHEDULER] 调度器启动成功
[SCHEDULER] 调度器初始化成功，进入主循环
[SCHEDULER] 进入主循环
```

## 其他优化

### 1. 建表失败不阻止调度器启动
建表操作失败时记录警告但不抛出异常，允许调度器继续启动：
- 如果表已存在，建表操作会快速完成
- 如果建表失败，后续任务会在执行时报错，但调度器本身保持运行
- 这样可以避免因临时网络问题导致整个调度器无法启动

### 2. 保留任务级重试机制
所有业务任务（`safe_daily_env_stats`, `safe_hourly_setpoint_monitoring`, `safe_daily_clip_inference`）仍然保留3次重试机制，确保任务执行的可靠性。

## 部署步骤

1. **构建新镜像**：
```bash
./docker/build.sh
```

2. **部署到服务器**：
```bash
IMAGE_TAG=<new_version> docker compose -f docker/mushroom_solution.yml up -d
```

3. **验证日志**：
```bash
docker logs -f --tail 100 mushroom_solution
```

4. **监控运行状态**：
- 确认看到 `[SCHEDULER] 建表操作完成`
- 确认看到 `[SCHEDULER] 调度器启动成功`
- 确认看到 `[SCHEDULER] 进入主循环`
- 监控24-48小时确保稳定运行

## 技术要点

1. **DDL操作的特殊性**：CREATE TABLE/INDEX等DDL操作比DML操作（SELECT/INSERT）需要更多时间和资源
2. **Docker网络延迟**：使用服务名（postgres_db）访问数据库时，DNS解析和网络路由可能增加延迟
3. **调度器启动机制**：APScheduler在启动时会立即执行所有 `next_run_time` 为当前时间的任务
4. **分离关注点**：将一次性初始化操作（建表）与周期性任务（业务逻辑）分离，提高系统可靠性

## 相关文件

- `src/scheduling/optimized_scheduler.py` - 调度器主文件
- `src/utils/create_table.py` - 建表逻辑
- `src/global_const/global_const.py` - 数据库连接池配置
- `docker/run.sh` - Docker启动脚本
- `COMPLETE_FIX_SUMMARY.md` - 完整修复总结

---

**修复完成时间**: 2026-01-14  
**修复类型**: 调度器初始化流程重构  
**影响范围**: 调度器启动逻辑，建表任务执行时机  
**测试状态**: 代码修改完成，待构建部署测试
