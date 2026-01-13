# 启动脚本说明

## 概述

`run.sh` 是蘑菇图像处理系统的生产级容器启动脚本，采用三服务模式运行：
1. **定时任务服务**: 执行调度器和CLIP推理任务
2. **Streamlit应用**: 提供Web界面
3. **FastAPI服务**: 提供健康检查API

## 脚本特性

### 多服务管理
- **并行启动**: 三个服务同时运行
- **进程监控**: 监控各服务进程状态
- **崩溃退出**: 任意服务崩溃时容器退出
- **日志分离**: 每个服务独立的日志文件

### 性能优化
```bash
export OMP_NUM_THREADS=4              # OpenMP线程数
export OPENBLAS_NUM_THREADS=4         # OpenBLAS线程数
export MKL_NUM_THREADS=4              # MKL线程数
export NUMEXPR_NUM_THREADS=4          # NumExpr线程数
export VECLIB_MAXIMUM_THREADS=4       # VecLib线程数
export NUMBA_NUM_THREADS=4            # Numba线程数
```

### 环境配置
```bash
export PYTHONPATH="$APP_ROOT"         # Python路径
export TZ=Asia/Shanghai               # 时区设置
```

## 服务配置

### 1. Streamlit应用
```bash
streamlit run streamlit_app.py \
    --server.port=7002 \
    --server.address=0.0.0.0 \
    --browser.gatherUsageStats=false \
    --server.enableCORS=false \
    --server.enableXsrfProtection=false \
    --server.maxUploadSize=100 \
    --server.maxMessageSize=200
```

**功能**: 提供Web界面用于图像处理和系统管理
**端口**: 7002
**日志**: `/app/Logs/streamlit.log`

### 2. FastAPI健康检查
```bash
uvicorn main:app --host 0.0.0.0 --port 5000 --workers 1
```

**功能**: 提供健康检查API端点
**端口**: 5000
**日志**: `/app/Logs/fastapi.log`
**端点**: `http://localhost:5000/health`

### 3. 定时任务服务
```bash
python main.py
```

**功能**: 执行调度器和CLIP推理任务
**日志**: `/app/Logs/timer.log`
**任务**: 
- 每日环境统计 (01:03:20)
- 每小时设定点监控 (每小时第5分钟)
- 每日CLIP推理 (03:02:25)

## 日志管理

### 日志文件
- **启动日志**: `/app/Logs/startup.log`
- **定时任务**: `/app/Logs/timer.log`
- **Streamlit**: `/app/Logs/streamlit.log`
- **FastAPI**: `/app/Logs/fastapi.log`

### 日志特性
- **实时输出**: 日志同时输出到文件和控制台
- **自动清理**: 启动时清理旧日志避免累积
- **分离管理**: 每个服务独立的日志文件
- **时间戳**: 统一的时间戳格式

## 启动流程

### 1. 初始化阶段
```bash
# 设置工作目录和环境变量
cd "$APP_ROOT"
export PYTHONPATH="$APP_ROOT"
export TZ=Asia/Shanghai

# 创建日志目录
mkdir -p "$LOG_DIR"

# 清理旧日志
> "$TIMER_LOG" 2>/dev/null || true
```

### 2. 性能优化设置
```bash
# 设置数值计算线程限制
export OMP_NUM_THREADS=4
export OPENBLAS_NUM_THREADS=4
# ... 其他线程参数
```

### 3. 服务启动
```bash
# 启动Streamlit (后台)
nohup $STREAMLIT_CMD 2>&1 | tee -a "$STREAMLIT_LOG" &
STREAMLIT_PID=$!

# 启动FastAPI (后台)
nohup $FASTAPI_CMD 2>&1 | tee -a "$FASTAPI_LOG" &
FASTAPI_PID=$!

# 启动定时任务 (后台)
nohup $PYTHON main.py 2>&1 >&${COPROC[1]} &
TIMER_PID=$!
```

### 4. 进程监控
```bash
# 验证服务启动
sleep 2
if ! kill -0 $STREAMLIT_PID 2>/dev/null; then
    fail "Streamlit 启动失败"
fi

# 主进程等待
wait
```

## 错误处理

### 启动失败检测
- **端口占用**: 检查7002和5000端口
- **配置错误**: 验证配置文件完整性
- **依赖缺失**: 检查Python依赖

### 自动退出机制
- **服务崩溃**: 任意服务进程结束时容器退出
- **资源不足**: 内存或CPU资源耗尽时退出
- **配置错误**: 关键配置错误时立即退出

### 调试方法
```bash
# 查看启动日志
docker exec mushroom_solution cat /app/Logs/startup.log

# 查看服务状态
docker exec mushroom_solution ps aux

# 查看端口占用
docker exec mushroom_solution netstat -tlnp
```

## 性能调优

### 线程控制
脚本中设置的线程限制确保：
- **CPU利用率**: 避免线程过多导致上下文切换开销
- **内存使用**: 控制并行计算的内存占用
- **稳定性**: 防止线程竞争导致的不稳定

### 资源限制
配合Docker Compose中的资源限制：
```yaml
mem_limit: 2048m    # 内存限制
cpus: 4.0          # CPU限制
```

### 优化建议
1. **监控资源使用**: 定期检查CPU和内存使用情况
2. **调整线程数**: 根据实际硬件调整线程参数
3. **日志轮转**: 配置日志轮转避免磁盘占满
4. **健康检查**: 利用FastAPI端点监控服务状态

## 维护操作

### 重启服务
```bash
# 重启容器
docker-compose restart mushroom_solution

# 查看启动日志
docker-compose logs -f mushroom_solution
```

### 调试模式
```bash
# 进入容器调试
docker exec -it mushroom_solution bash

# 手动启动服务
cd /app
bash run.sh
```

### 配置修改
修改启动脚本后需要重新构建镜像：
```bash
# 重新构建
docker-compose build mushroom_solution

# 重启服务
docker-compose up -d mushroom_solution
```

---

启动脚本确保了蘑菇图像处理系统在容器环境中的稳定运行，提供了完整的服务管理和监控能力。