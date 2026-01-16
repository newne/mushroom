# Docker数据库连接超时问题修复

## 问题描述

在Docker生产环境中，调度器启动后约30秒就因为 "Timeout connecting to server" 错误而崩溃重启。

## 根本原因

1. **Docker网络DNS解析**：生产环境使用 `postgres_db` 服务名而非IP地址
2. **连接池配置不足**：SQLAlchemy默认配置对Docker网络环境的超时设置过于严格
3. **缺少重试机制**：任务函数在遇到连接错误时立即失败，导致调度器崩溃

## 解决方案

### 1. 优化数据库连接池配置

**文件**: `src/global_const/global_const.py`

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
    echo=False,                   # 不输出SQL日志
    future=True                   # 使用SQLAlchemy 2.0风格
)
```

**关键参数说明**：
- `pool_pre_ping=True`: 每次从连接池获取连接前先测试连接是否有效
- `connect_timeout=10`: TCP连接超时设置为10秒，适应Docker网络延迟
- `pool_timeout=30`: 从连接池获取连接的最大等待时间
- `statement_timeout=300000`: SQL语句执行超时（5分钟），防止长查询阻塞

### 2. 添加任务级别的重试机制

**文件**: `src/scheduling/optimized_scheduler.py`

为所有定时任务添加了重试逻辑：

```python
def safe_hourly_setpoint_monitoring() -> None:
    """每小时设定点变更监控任务（带数据库连接重试）"""
    max_retries = 3
    retry_delay = 5  # 秒
    
    for attempt in range(1, max_retries + 1):
        try:
            # 任务执行逻辑
            ...
            return  # 成功执行，退出重试循环
            
        except Exception as e:
            error_msg = str(e)
            
            # 检查是否是数据库连接错误
            is_connection_error = any(keyword in error_msg.lower() for keyword in [
                'timeout', 'connection', 'connect', 'database', 'server'
            ])
            
            if is_connection_error and attempt < max_retries:
                logger.warning(f"检测到连接错误，{retry_delay}秒后重试...")
                time.sleep(retry_delay)
            else:
                # 不再抛出异常，避免调度器崩溃
                return
```

**重试策略**：
- 最多重试3次
- 每次重试间隔5秒
- 只对连接错误进行重试
- 失败后不抛出异常，避免调度器崩溃

### 3. 调度器初始化重试机制

```python
def run(self) -> NoReturn:
    """运行调度器（带数据库连接重试）"""
    max_init_retries = 5
    init_retry_delay = 10
    
    # 初始化阶段的重试逻辑
    for init_attempt in range(1, max_init_retries + 1):
        try:
            # 初始化调度器
            self.scheduler = self._init_scheduler()
            # 注册信号处理器
            self._register_signal_handlers()
            # 设置任务
            self._setup_jobs()
            # 启动调度器
            self._start_scheduler()
            
            logger.info("调度器初始化成功，进入主循环")
            break
            
        except Exception as e:
            if init_attempt < max_init_retries:
                logger.warning(f"初始化失败，{init_retry_delay}秒后重试...")
                time.sleep(init_retry_delay)
            else:
                logger.critical("初始化失败，已达到最大重试次数")
                raise
```

**初始化重试**：
- 最多重试5次
- 每次重试间隔10秒
- 给数据库更多时间完成启动

### 4. Docker Compose配置优化

**文件**: `docker/mushroom_solution.yml`

```yaml
services:
  postgres_db:
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U $POSTGRES_USER -d $POSTGRES_DB"]
      interval: 20s
      timeout: 5s
      retries: 5
      start_period: 60s  # 给数据库60秒的启动时间

  mushroom_solution:
    depends_on:
      postgres_db:
        condition: service_healthy  # 等待数据库健康检查通过
```

**依赖管理**：
- 应用容器等待数据库健康检查通过后才启动
- 数据库有60秒的启动缓冲期
- 健康检查每20秒执行一次

## 测试工具

### 1. 数据库连接测试脚本

```bash
# 在容器内测试数据库连接
python scripts/test_db_connection.py

# 使用生产环境配置测试
prod=true python scripts/test_db_connection.py
```

**测试内容**：
- 环境配置验证
- DNS解析测试
- TCP连接测试
- 数据库连接测试
- 连接池功能测试

### 2. 调度器容错测试

```bash
# 测试调度器的容错能力
python scripts/test_scheduler_resilience.py
```

## 部署步骤

### 本地构建和推送

1. **构建并推送镜像到私有仓库**：
   ```bash
   cd docker
   ./build.sh
   ```
   
   这将：
   - 使用 PyArmor 加密代码
   - 构建 Docker 镜像
   - 生成版本标签（格式：`BASE_VERSION-YYYYMMDDHHMMSS-GIT_HASH`）
   - 推送到阿里云镜像仓库

2. **记录生成的镜像标签**：
   ```bash
   # 示例输出
   Version: 0.1.0-20260114100000-abc1234
   Successfully pushed images to registry
   ```

### 服务器部署

1. **SSH登录到服务器**：
   ```bash
   ssh user@your-server
   cd /path/to/mushroom_solution/docker
   ```

2. **停止现有服务**（如果正在运行）：
   ```bash
   docker compose -f mushroom_solution.yml down
   ```

3. **使用新镜像标签启动服务**：
   ```bash
   # 使用本地构建时生成的IMAGE_TAG
   IMAGE_TAG=0.1.0-20260114100000-abc1234 docker compose -f mushroom_solution.yml up -d
   ```

4. **验证部署**：
   ```bash
   # 查看容器状态
   docker ps | grep mushroom
   
   # 查看应用日志
   docker logs -f mushroom_solution
   
   # 查看数据库日志
   docker logs -f postgres_db
   ```

5. **测试数据库连接**（在容器内）：
   ```bash
   docker exec mushroom_solution prod=true python scripts/test_db_connection.py
   ```

6. **监控调度器运行**：
   ```bash
   # 查看调度器日志
   docker exec mushroom_solution tail -f /app/Logs/mushroom_solution-info.log
   
   # 查看错误日志
   docker exec mushroom_solution tail -f /app/Logs/mushroom_solution-error.log
   ```

### 快速部署命令（服务器端）

```bash
# 一键部署脚本
IMAGE_TAG=0.1.0-20260114100000-abc1234  # 替换为实际版本

# 停止旧版本
docker compose -f mushroom_solution.yml down

# 拉取新镜像
docker pull registry.cn-beijing.aliyuncs.com/ncgnewne/mushroom_solution:${IMAGE_TAG}

# 启动新版本
IMAGE_TAG=${IMAGE_TAG} docker compose -f mushroom_solution.yml up -d

# 等待服务就绪
sleep 30

# 检查状态
docker ps | grep mushroom
docker logs --tail 50 mushroom_solution
```

## 预期结果

修复后的系统应该：

1. ✅ 调度器能够成功启动并保持运行
2. ✅ 数据库连接超时后自动重试
3. ✅ 单个任务失败不会导致调度器崩溃
4. ✅ 所有定时任务正常执行
5. ✅ 日志中显示重试和恢复信息

## 故障排查

如果问题仍然存在：

1. **检查网络连接**：
   ```bash
   docker exec mushroom_solution ping postgres_db
   docker exec mushroom_solution nc -zv postgres_db 5432
   ```

2. **检查数据库状态**：
   ```bash
   docker exec postgres_db pg_isready -U postgres
   ```

3. **查看详细日志**：
   ```bash
   docker logs --tail 100 mushroom_solution
   docker logs --tail 100 postgres_db
   ```

4. **检查网络配置**：
   ```bash
   docker network inspect plant_backend
   ```

5. **验证环境变量**：
   ```bash
   docker exec mushroom_solution env | grep -E '(POSTGRES|prod)'
   ```

## 配置文件变更总结

| 文件 | 变更内容 | 目的 |
|------|---------|------|
| `src/global_const/global_const.py` | 优化数据库连接池配置 | 增加超时容忍度，适应Docker网络 |
| `src/scheduling/optimized_scheduler.py` | 添加任务重试机制 | 防止单个任务失败导致调度器崩溃 |
| `docker/mushroom_solution.yml` | 优化健康检查和依赖 | 确保数据库就绪后再启动应用 |

## 性能影响

- **启动时间**: 增加约10-30秒（等待数据库就绪）
- **内存使用**: 连接池配置增加约10-20MB
- **CPU使用**: 重试机制对CPU影响可忽略不计
- **可靠性**: 显著提升，能够自动处理临时网络问题

## 后续优化建议

1. **监控告警**: 添加Prometheus指标监控数据库连接状态
2. **连接池调优**: 根据实际负载调整连接池大小
3. **日志聚合**: 使用ELK或Loki收集和分析日志
4. **健康检查端点**: 暴露应用健康状态API
5. **断路器模式**: 考虑实现断路器防止级联失败

## 参考文档

- [SQLAlchemy连接池配置](https://docs.sqlalchemy.org/en/20/core/pooling.html)
- [Docker网络最佳实践](https://docs.docker.com/network/)
- [PostgreSQL连接参数](https://www.postgresql.org/docs/current/libpq-connect.html)
- [APScheduler错误处理](https://apscheduler.readthedocs.io/en/stable/userguide.html#error-handling)
