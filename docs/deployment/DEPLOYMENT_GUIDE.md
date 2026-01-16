# 蘑菇解决方案 - 部署指南

## 部署架构

```
┌─────────────────┐         ┌──────────────────┐         ┌─────────────────┐
│   本地开发环境   │  push   │   阿里云镜像仓库   │  pull   │   生产服务器     │
│                 │ ──────> │                  │ ──────> │                 │
│  build.sh       │         │  registry.cn-    │         │  deploy_server  │
│  构建+推送镜像   │         │  beijing.ali...  │         │  拉取+启动容器   │
└─────────────────┘         └──────────────────┘         └─────────────────┘
```

## 前置条件

### 本地环境
- Docker 已安装
- Git 已安装
- PyArmor 已安装（用于代码加密）
- 已登录阿里云镜像仓库

### 服务器环境
- Docker 已安装
- Docker Compose 已安装
- 已加入 `plant_backend` Docker 网络
- PostgreSQL 数据库服务运行中

## 完整部署流程

### 第一步：本地构建镜像

1. **确保代码已提交到Git**：
   ```bash
   git status
   git add .
   git commit -m "Fix: 优化数据库连接池配置，添加重试机制"
   git push
   ```

2. **执行构建脚本**：
   ```bash
   cd docker
   ./build.sh
   ```

3. **记录生成的镜像标签**：
   ```bash
   # 构建脚本会输出类似信息：
   Building mushroom_solution
   Version: 0.1.0-20260114100000-abc1234
   Git Hash: abc1234
   Build Date: 2026-01-14T10:00:00Z
   Successfully built mushroom_solution:0.1.0-20260114100000-abc1234
   Successfully pushed images to registry
   ```
   
   **重要**: 记录 `Version` 行的完整版本号，例如：`0.1.0-20260114100000-abc1234`

### 第二步：服务器部署

#### 方式一：使用部署脚本（推荐）

1. **上传部署脚本到服务器**：
   ```bash
   # 在本地执行
   scp docker/deploy_server.sh user@your-server:/path/to/mushroom_solution/docker/
   ```

2. **SSH登录到服务器**：
   ```bash
   ssh user@your-server
   cd /path/to/mushroom_solution/docker
   ```

3. **执行部署脚本**：
   ```bash
   chmod +x deploy_server.sh
   ./deploy_server.sh 0.1.0-20260114100000-abc1234
   ```
   
   脚本会自动：
   - 停止旧容器
   - 拉取新镜像
   - 启动新容器
   - 等待服务就绪
   - 验证部署结果

#### 方式二：手动部署

1. **SSH登录到服务器**：
   ```bash
   ssh user@your-server
   cd /path/to/mushroom_solution/docker
   ```

2. **停止现有服务**：
   ```bash
   docker compose -f mushroom_solution.yml down
   ```

3. **拉取新镜像**：
   ```bash
   IMAGE_TAG=0.1.0-20260114100000-abc1234
   docker pull registry.cn-beijing.aliyuncs.com/ncgnewne/mushroom_solution:${IMAGE_TAG}
   ```

4. **启动新服务**：
   ```bash
   IMAGE_TAG=0.1.0-20260114100000-abc1234 docker compose -f mushroom_solution.yml up -d
   ```

5. **等待服务启动**：
   ```bash
   sleep 30
   ```

### 第三步：验证部署

1. **检查容器状态**：
   ```bash
   docker ps | grep mushroom
   ```
   
   应该看到两个容器运行中：
   - `postgres_db` - 数据库
   - `mushroom_solution` - 应用

2. **查看启动日志**（包含调度器日志）：
   ```bash
   # 查看完整日志（包括调度器输出）
   docker logs --tail 100 mushroom_solution
   ```
   
   应该看到：
   ```
   [2026-01-14 10:10:51] 定时任务已启动，PID=55
   [SCHEDULER] === 优化版调度器启动 ===
   [SCHEDULER] 初始化调度器 (尝试 1/5)
   [SCHEDULER] 调度器初始化完成
   [SCHEDULER] 建表任务已添加
   [SCHEDULER] 每日环境统计任务已添加
   [SCHEDULER] 每小时设定点监控任务已添加
   [SCHEDULER] 每日CLIP推理任务已添加
   [SCHEDULER] 调度器初始化成功，进入主循环
   ```

3. **查看容器内的业务日志文件**：
   ```bash
   # 查看调度器详细日志
   docker exec mushroom_solution tail -f /app/Logs/mushroom_solution-info.log
   
   # 查看错误日志
   docker exec mushroom_solution tail -f /app/Logs/mushroom_solution-error.log
   
   # 查看定时任务日志
   docker exec mushroom_solution tail -f /app/Logs/timer.log
   ```

4. **测试数据库连接**：
   ```bash
   docker exec mushroom_solution prod=true python scripts/test_db_connection.py
   ```
   
   应该看到：
   ```
   ✅ DNS解析成功
   ✅ TCP连接成功
   ✅ 数据库连接成功
   ✅ 连接池测试通过
   ```

5. **实时监控所有日志**：
   ```bash
   # 方式1: 通过 docker logs（包含所有输出）
   docker logs -f mushroom_solution
   
   # 方式2: 进入容器查看业务日志
   docker exec -it mushroom_solution bash
   tail -f /app/Logs/mushroom_solution-info.log
   ```

### 第四步：持续监控

建议在部署后持续监控24小时：

```bash
# 实时查看应用日志
docker logs -f mushroom_solution

# 查看资源使用情况
docker stats mushroom_solution

# 定期检查容器状态
watch -n 60 'docker ps | grep mushroom'
```

## 常见问题排查

### 问题1：容器启动后立即退出

**症状**：
```bash
docker ps | grep mushroom
# 没有输出
```

**排查步骤**：
```bash
# 查看容器日志
docker logs mushroom_solution

# 查看退出状态
docker ps -a | grep mushroom_solution
```

**可能原因**：
- 数据库连接失败
- 配置文件错误
- 依赖服务未启动

### 问题2：数据库连接超时

**症状**：
```
Timeout connecting to server
```

**排查步骤**：
```bash
# 1. 检查数据库状态
docker exec postgres_db pg_isready -U postgres

# 2. 测试网络连接
docker exec mushroom_solution ping postgres_db
docker exec mushroom_solution nc -zv postgres_db 5432

# 3. 检查网络配置
docker network inspect plant_backend

# 4. 查看数据库日志
docker logs postgres_db
```

**解决方案**：
- 确保数据库容器健康
- 检查网络配置
- 验证 settings.toml 中的数据库配置

### 问题3：调度器任务失败

**症状**：
```
[TASK] 设定点变更监控失败
```

**排查步骤**：
```bash
# 查看详细错误日志
docker exec mushroom_solution tail -100 /app/Logs/mushroom_solution-error.log

# 测试数据库连接
docker exec mushroom_solution prod=true python scripts/test_db_connection.py

# 手动执行任务测试
docker exec -it mushroom_solution bash
prod=true python -c "
import sys
sys.path.insert(0, 'src')
from scheduling.optimized_scheduler import safe_hourly_setpoint_monitoring
safe_hourly_setpoint_monitoring()
"
```

## 回滚操作

如果新版本出现问题，可以快速回滚到上一个版本：

```bash
# 停止当前版本
docker compose -f mushroom_solution.yml down

# 启动上一个版本（替换为实际的旧版本号）
IMAGE_TAG=0.1.0-20260113155156-dbb511b docker compose -f mushroom_solution.yml up -d
```

## 版本管理

### 查看当前运行的版本

```bash
# 查看容器镜像
docker inspect mushroom_solution | grep Image

# 查看镜像标签
docker images | grep mushroom_solution
```

### 清理旧镜像

```bash
# 列出所有镜像
docker images | grep mushroom_solution

# 删除指定版本
docker rmi registry.cn-beijing.aliyuncs.com/ncgnewne/mushroom_solution:OLD_TAG

# 清理未使用的镜像
docker image prune -a
```

## 性能监控

### 资源使用监控

```bash
# 实时监控
docker stats mushroom_solution

# 查看详细信息
docker inspect mushroom_solution | grep -A 10 "Memory"
```

### 日志管理

```bash
# 查看日志大小
docker exec mushroom_solution du -sh /app/Logs/*

# 清理旧日志（如果需要）
docker exec mushroom_solution find /app/Logs -name "*.log.*" -mtime +7 -delete
```

## 安全建议

1. **定期更新镜像**：
   - 每周构建新镜像
   - 及时应用安全补丁

2. **日志审计**：
   - 定期检查错误日志
   - 监控异常访问

3. **备份策略**：
   - 定期备份数据库
   - 保存配置文件

4. **访问控制**：
   - 限制容器端口暴露
   - 使用防火墙规则

## 联系支持

如遇到问题，请提供以下信息：

1. **部署信息**：
   ```bash
   echo "IMAGE_TAG: $(docker inspect mushroom_solution | grep Image)"
   echo "部署时间: $(docker inspect mushroom_solution | grep StartedAt)"
   ```

2. **日志文件**：
   ```bash
   docker logs mushroom_solution > app.log 2>&1
   docker logs postgres_db > db.log 2>&1
   ```

3. **系统信息**：
   ```bash
   docker version
   docker compose version
   docker network ls
   docker ps -a
   ```

---

**文档版本**: 1.0  
**最后更新**: 2026-01-14  
**适用版本**: mushroom_solution >= 0.1.0
