# 部署检查清单

## 修复内容

本次部署修复了调度器初始化时的数据库连接超时问题。

**核心改动**：将建表操作从调度任务中移出，在调度器启动前执行。

## 部署前检查

- [ ] 确认当前版本号和新版本号
- [ ] 备份当前配置文件（如有修改）
- [ ] 确认数据库服务正常运行
- [ ] 确认有足够的磁盘空间

## 部署步骤

### 1. 本地构建镜像

```bash
cd docker
./build.sh
```

记录生成的 IMAGE_TAG：`_________________`

### 2. 推送到私有镜像仓库

镜像会自动推送（build.sh 中已包含）

### 3. 服务器部署

```bash
# SSH到服务器
ssh user@server

# 进入项目目录
cd /path/to/mushroom_solution/docker

# 停止旧版本
docker compose -f mushroom_solution.yml down

# 启动新版本
IMAGE_TAG=<新版本号> docker compose -f mushroom_solution.yml up -d
```

### 4. 验证部署

#### 4.1 检查容器状态

```bash
docker ps | grep mushroom
```

预期：容器状态为 `Up`，不应该频繁重启

#### 4.2 查看启动日志

```bash
docker logs -f --tail 100 mushroom_solution
```

**关键验证点**：

- [ ] 看到 `[SCHEDULER] 测试数据库连接...`
- [ ] 看到 `[SCHEDULER] 数据库连接测试成功`
- [ ] 看到 `[SCHEDULER] 执行建表操作...`
- [ ] 看到 `[SCHEDULER] 建表操作完成`
- [ ] 看到 `[SCHEDULER] 调度器初始化完成`
- [ ] 看到 `[SCHEDULER] 总共添加了 3 个任务`（注意是3个，不是4个）
- [ ] 看到 `[SCHEDULER] 调度器启动成功`
- [ ] 看到 `[SCHEDULER] 进入主循环`
- [ ] 看到 `所有服务已成功启动。保持容器活跃中...`

#### 4.3 测试数据库连接

```bash
docker exec mushroom_solution prod=true python scripts/test_db_connection.py
```

预期：连接成功，无超时错误

#### 4.4 检查错误日志

```bash
docker exec mushroom_solution tail -50 /app/Logs/mushroom_solution-error.log
```

预期：无新的错误信息

#### 4.5 验证服务可访问

- [ ] Streamlit 应用：http://server:7002
- [ ] FastAPI 健康检查：http://server:5000/health

## 监控计划

### 第一小时

每10分钟检查一次：

```bash
# 检查容器状态
docker ps | grep mushroom

# 查看最新日志
docker logs --tail 50 mushroom_solution
```

### 第一天

每2小时检查一次：

```bash
# 检查容器运行时间（应该持续增长，不重启）
docker ps | grep mushroom

# 检查错误日志
docker exec mushroom_solution tail -20 /app/Logs/mushroom_solution-error.log
```

### 第一周

每天检查一次：

```bash
# 查看容器状态
docker stats mushroom_solution --no-stream

# 检查任务执行情况
docker logs mushroom_solution 2>&1 | grep "\[TASK\]" | tail -20
```

## 回滚方案

如果出现问题，立即回滚：

```bash
# 停止新版本
docker compose -f mushroom_solution.yml down

# 启动旧版本
IMAGE_TAG=<旧版本号> docker compose -f mushroom_solution.yml up -d

# 验证旧版本运行正常
docker logs -f mushroom_solution
```

旧版本号：`_________________`

## 常见问题排查

### 问题1：容器启动后立即退出

```bash
# 查看完整日志
docker logs --tail 200 mushroom_solution

# 检查数据库连接
docker exec postgres_db pg_isready -U postgres

# 测试网络连接
docker exec mushroom_solution ping postgres_db
```

### 问题2：调度器初始化失败

```bash
# 查看详细错误
docker logs mushroom_solution 2>&1 | grep "ERROR\|CRITICAL"

# 手动测试数据库连接
docker exec -it mushroom_solution bash
prod=true python scripts/test_db_connection.py
```

### 问题3：任务执行失败

```bash
# 查看任务日志
docker logs mushroom_solution 2>&1 | grep "\[TASK\]"

# 查看错误日志
docker exec mushroom_solution tail -100 /app/Logs/mushroom_solution-error.log
```

## 成功标准

部署成功的标准：

- [ ] 容器持续运行超过1小时，无重启
- [ ] 调度器成功启动并进入主循环
- [ ] 所有定时任务正常添加（3个任务）
- [ ] 无数据库连接超时错误
- [ ] Streamlit 和 FastAPI 服务可访问
- [ ] 错误日志中无新的严重错误

## 联系信息

如有问题，请联系：

- 开发团队：_______________
- 运维团队：_______________

## 备注

- 本次修复主要改动：`src/scheduling/optimized_scheduler.py`
- 详细文档：`SCHEDULER_DB_CONNECTION_FIX.md`
- 完整总结：`COMPLETE_FIX_SUMMARY.md`

---

**部署日期**：_______________  
**部署人员**：_______________  
**部署结果**：[ ] 成功  [ ] 失败  [ ] 回滚  
**备注**：_______________
