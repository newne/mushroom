# 快速部署参考卡片

## 🚀 本地构建（开发机）

```bash
cd docker
./build.sh
# 记录输出的 IMAGE_TAG，例如：0.1.0-20260114100000-abc1234
```

## 📦 服务器部署

### 方式一：使用脚本（推荐）

```bash
# 上传脚本（首次）
scp docker/deploy_server.sh user@server:/path/to/docker/

# SSH到服务器
ssh user@server
cd /path/to/mushroom_solution/docker

# 执行部署
./deploy_server.sh 0.1.0-20260114100000-abc1234
```

### 方式二：手动命令

```bash
# SSH到服务器
ssh user@server
cd /path/to/mushroom_solution/docker

# 一键部署
IMAGE_TAG=0.1.0-20260114100000-abc1234
docker compose -f mushroom_solution.yml down
docker pull registry.cn-beijing.aliyuncs.com/ncgnewne/mushroom_solution:${IMAGE_TAG}
IMAGE_TAG=${IMAGE_TAG} docker compose -f mushroom_solution.yml up -d
```

## 🔍 验证部署

```bash
# 检查容器状态
docker ps | grep mushroom

# 查看完整日志（包括调度器输出）
docker logs --tail 100 mushroom_solution

# 实时查看日志
docker logs -f mushroom_solution

# 查看容器内业务日志
docker exec mushroom_solution tail -f /app/Logs/mushroom_solution-info.log
docker exec mushroom_solution tail -f /app/Logs/mushroom_solution-error.log
docker exec mushroom_solution tail -f /app/Logs/timer.log

# 测试数据库连接
docker exec mushroom_solution prod=true python scripts/test_db_connection.py
```

## ⚠️ 故障排查

```bash
# 查看完整日志
docker logs mushroom_solution

# 检查数据库
docker exec postgres_db pg_isready -U postgres

# 测试网络
docker exec mushroom_solution ping postgres_db

# 进入容器调试
docker exec -it mushroom_solution bash
```

## 🔄 回滚

```bash
IMAGE_TAG=<旧版本号> docker compose -f mushroom_solution.yml up -d
```

## 📊 监控

```bash
# 资源使用
docker stats mushroom_solution

# 实时日志
docker logs -f mushroom_solution

# 错误日志
docker exec mushroom_solution tail -f /app/Logs/mushroom_solution-error.log
```

## 🎯 关键日志标识

**成功启动**：
```
[SCHEDULER] 调度器初始化成功，进入主循环
```

**连接重试**：
```
[TASK] 检测到连接错误，5秒后重试...
```

**任务执行**：
```
[TASK] 设定点监控完成: 处理 4/4 个库房
```

## 📝 常用环境变量

```bash
# 生产环境标志
prod=true

# 镜像标签
IMAGE_TAG=0.1.0-20260114100000-abc1234

# 数据库配置（在settings.toml中）
host = "postgres_db"
port = 5432
```

---
**提示**: 保存此文件到手机或打印出来，方便快速查阅！
