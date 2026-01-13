# Docker部署指南

本目录包含蘑菇图像处理系统的Docker部署配置。

## 文件说明

### mushroom_solution.yml
主要的Docker Compose配置文件，定义了完整的服务栈。

### 其他文件
- `Dockerfile`: 容器镜像构建文件
- `.env`: 环境变量配置文件
- `secrets/`: 敏感信息存储目录

## 服务架构

### 服务组件
1. **postgres_db**: PostgreSQL数据库 + pgvector扩展
2. **mushroom_solution**: 主应用服务

### 网络配置
- **网络名称**: plant_backend
- **网络类型**: bridge
- **服务间通信**: 内部网络

## 挂载配置

### 目录挂载
```yaml
volumes:
  - ./configs:/app/configs:ro      # 配置文件（只读）
  - ./Logs:/app/Logs:rw           # 日志目录（读写）
  - ./models:/models:rw           # AI模型目录（读写）
  - ./data:/app/data:ro           # 数据目录（只读）
```

### 挂载说明
- **configs**: 应用配置文件，只读挂载保证安全性
- **Logs**: 日志输出目录，需要写权限
- **models**: AI模型文件，挂载到根目录便于访问
- **data**: 输入数据文件，只读挂载

## 环境变量配置

### 基础配置
```yaml
PYTHONUNBUFFERED: 1              # Python输出不缓冲
PYTHONDONTWRITEBYTECODE: 1       # 不生成.pyc文件
TZ: Asia/Shanghai                # 时区设置
ENVIRONMENT: production          # 运行环境
```

### 数据库配置
```yaml
POSTGRES_HOST: postgres_db       # 数据库主机
POSTGRES_PORT: 5432             # 数据库端口
POSTGRES_DB: mushroom_db        # 数据库名称
POSTGRES_USER: postgres         # 数据库用户
```

### AI模型配置
```yaml
TRANSFORMERS_CACHE: /models/.cache             # Transformers缓存目录
HF_HOME: /models/.cache                        # HuggingFace缓存目录
TORCH_HOME: /models/.cache                     # PyTorch缓存目录
CLIP_MODEL_PATH: /models/clip-vit-base-patch32 # CLIP模型路径
HF_HUB_DISABLE_TELEMETRY: 1                   # 禁用遥测
TRANSFORMERS_OFFLINE: 0                        # 允许在线下载
```

### 性能优化配置
数值计算线程优化参数在启动脚本 `run.sh` 中设置：
```bash
export OMP_NUM_THREADS=4              # OpenMP线程数
export OPENBLAS_NUM_THREADS=4         # OpenBLAS线程数
export MKL_NUM_THREADS=4              # MKL线程数
export NUMEXPR_NUM_THREADS=4          # NumExpr线程数
export VECLIB_MAXIMUM_THREADS=4       # VecLib线程数
export NUMBA_NUM_THREADS=4            # Numba线程数
```

## 资源限制

### 应用服务
- **内存限制**: 2048MB
- **CPU限制**: 4.0核心
- **健康检查**: HTTP端点检查

### 数据库服务
- **内存限制**: 512MB
- **CPU限制**: 2.0核心
- **健康检查**: pg_isready检查

## 部署步骤

### 1. 准备环境
```bash
# 创建必要目录
mkdir -p configs Logs models data secrets

# 设置权限
chmod 755 configs Logs models data
chmod 700 secrets
```

### 2. 配置文件
```bash
# 复制配置文件到configs目录
cp src/configs/* configs/

# 创建数据库密码文件
echo "your_password" > secrets/postgres_password.txt
chmod 600 secrets/postgres_password.txt
```

### 3. 模型准备
```bash
# 确保CLIP模型存在
ls -la models/clip-vit-base-patch32/

# 如果不存在，系统会自动下载
# 或手动下载到该目录
```

### 4. 启动服务
```bash
# 启动所有服务
docker-compose -f docker/mushroom_solution.yml up -d

# 查看服务状态
docker-compose -f docker/mushroom_solution.yml ps

# 查看日志
docker-compose -f docker/mushroom_solution.yml logs -f
```

### 5. 验证部署
```bash
# 检查健康状态
curl http://localhost:7002/health

# 检查数据库连接
docker exec postgres_db pg_isready -U postgres

# 检查模型加载
docker exec mushroom_solution ls -la /app/models/
```

## 环境变量文件

### .env文件示例
```bash
# 镜像版本
IMAGE_TAG=latest

# 数据库配置
POSTGRES_DB=mushroom_algorithm
POSTGRES_USER=postgres
POSTGRES_PORT=5432

# 应用端口
STREAMLIT_PORT=7002

# 运行环境
ENVIRONMENT=production
```

## 监控和维护

### 日志管理
```bash
# 查看应用日志
docker-compose -f docker/mushroom_solution.yml logs mushroom_solution

# 查看数据库日志
docker-compose -f docker/mushroom_solution.yml logs postgres_db

# 清理日志
docker system prune -f
```

### 数据备份
```bash
# 备份数据库
docker exec postgres_db pg_dump -U postgres mushroom_algorithm > backup.sql

# 备份模型文件
tar -czf models_backup.tar.gz models/

# 备份配置文件
tar -czf configs_backup.tar.gz configs/
```

### 更新部署
```bash
# 拉取新镜像
docker-compose -f docker/mushroom_solution.yml pull

# 重启服务
docker-compose -f docker/mushroom_solution.yml up -d

# 清理旧镜像
docker image prune -f
```

## 故障排除

### 常见问题

#### 1. 服务启动失败
```bash
# 检查配置文件
docker-compose -f docker/mushroom_solution.yml config

# 查看详细错误
docker-compose -f docker/mushroom_solution.yml up --no-deps mushroom_solution
```

#### 2. 数据库连接失败
```bash
# 检查数据库状态
docker exec postgres_db pg_isready -U postgres

# 检查网络连接
docker network ls
docker network inspect plant_backend
```

#### 3. 模型加载失败
```bash
# 检查模型文件
docker exec mushroom_solution ls -la /models/clip-vit-base-patch32/

# 检查权限
docker exec mushroom_solution ls -ld /models/

# 检查环境变量
docker exec mushroom_solution env | grep -E "(TRANSFORMERS|HF_|TORCH_)"
```

#### 4. 内存不足
```bash
# 检查内存使用
docker stats

# 调整内存限制
# 编辑 mushroom_solution.yml 中的 mem_limit
```

### 调试命令
```bash
# 进入容器调试
docker exec -it mushroom_solution bash

# 检查Python环境
docker exec mushroom_solution python -c "import torch; print(torch.__version__)"

# 测试模型加载
docker exec mushroom_solution python -c "
from pathlib import Path
model_path = Path('/models/clip-vit-base-patch32')
print(f'Model exists: {model_path.exists()}')
if model_path.exists():
    print(f'Files: {list(model_path.glob(\"*\"))[:5]}')
"
```

## 安全配置

### 网络安全
- 使用内部网络隔离服务
- 只暴露必要的端口
- 配置防火墙规则

### 数据安全
- 使用secrets管理敏感信息
- 配置适当的文件权限
- 定期备份重要数据

### 访问控制
- 限制容器权限
- 使用非root用户运行
- 配置资源限制

---

本部署指南确保蘑菇图像处理系统能够在Docker环境中稳定、安全地运行，特别是AI模型的正确挂载和配置。