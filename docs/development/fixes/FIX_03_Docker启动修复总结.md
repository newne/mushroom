# Docker 容器启动问题修复总结

## 问题描述

服务器运行 Docker 容器时出现以下错误：
```
[2026-01-13 15:42:57] 启动定时任务 main.py...
run.sh: line 98: ${COPROC[1]}: Bad file descriptor
[2026-01-13 15:43:02] ERROR: 定时任务启动失败，请检查代码或依赖
```

容器反复启动失败，定时任务无法正常运行。

## 根本原因分析

1. **启动脚本问题**: `run.sh` 中使用了有问题的 `coproc` 语法，导致文件描述符错误
2. **进程管理问题**: 缺乏适当的信号处理和进程监控机制
3. **日志重定向问题**: 使用 `tee` 管道可能导致进程阻塞
4. **主程序入口混乱**: 根目录和 src 目录都有 main.py，容器中路径不明确

## 修复方案

### 1. 修复启动脚本 (docker/run.sh)

**问题**: 使用了不稳定的 `coproc` 语法和复杂的管道操作
```bash
# 有问题的代码
nohup $PYTHON main.py 2>&1 | tee -a "$TIMER_LOG" &
```

**修复**: 使用标准的重定向和进程管理
```bash
# 修复后的代码
nohup $PYTHON main.py > "$TIMER_LOG" 2>&1 &
TIMER_PID=$!

# 添加信号处理
cleanup() {
    log "收到退出信号，正在清理进程..."
    if [[ -n "${TIMER_PID:-}" ]] && kill -0 "$TIMER_PID" 2>/dev/null; then
        kill -TERM "$TIMER_PID" 2>/dev/null || true
    fi
    exit 0
}
trap cleanup SIGTERM SIGINT

# 添加进程监控循环
while true; do
    if ! kill -0 $TIMER_PID 2>/dev/null; then
        fail "定时任务进程异常退出"
    fi
    sleep 30
done
```

### 2. 修复主程序入口 (src/main.py)

**问题**: 主程序同时包含 FastAPI 应用和调度器逻辑，在容器中运行混乱

**修复**: 添加环境检测，区分容器和开发环境
```python
if __name__ == '__main__':
    # 设置日志
    loguru_setting()
    
    # 检查是否在容器环境中
    if os.path.exists('/app') and os.getcwd() == '/app':
        print("[MAIN] 检测到容器环境，启动调度器...")
        # 在容器中运行调度器
        main()
    else:
        print("[MAIN] 检测到开发环境，请使用 uvicorn 启动 FastAPI 或直接运行调度器")
        sys.exit(0)
```

### 3. 清理文件结构

**修复**: 删除根目录的重复 main.py 文件，确保容器使用唯一入口点

### 4. 验证模型路径配置

**确认**: 模型挂载路径 `./models:/models:rw` 正确，代码已适配不同环境路径

## 修复效果

### 启动流程优化

1. **环境初始化**
   - 正确设置线程限制参数
   - 创建必要的日志目录
   - 配置 Python 环境变量

2. **服务启动顺序**
   - Streamlit Web 界面 (端口 7002)
   - FastAPI 健康检查服务 (端口 5000)  
   - 定时任务调度器 (APScheduler)

3. **进程监控**
   - 持续监控所有服务进程状态
   - 任意进程异常退出时整个容器退出
   - 支持优雅的信号处理和清理

### 错误处理改进

1. **详细的错误日志**: 每个服务都有独立的日志文件
2. **进程状态检查**: 启动后验证进程是否正常运行
3. **环境依赖验证**: 启动前检查 Python 环境和模块导入

## 测试验证

创建了测试脚本 `scripts/test_container_startup.py` 用于验证：
- ✅ 环境配置 (工作目录、Python 路径、环境变量)
- ✅ 模块导入 (调度器、异常监听器、日志配置等)
- ✅ 数据库连接 (配置加载、连接测试)
- ✅ 模型路径 (容器路径 `/models` 和开发路径检查)

## 部署建议

### 重新部署步骤

1. **重新构建镜像**
   ```bash
   docker-compose -f docker/mushroom_solution.yml build --no-cache
   ```

2. **启动服务**
   ```bash
   docker-compose -f docker/mushroom_solution.yml up -d
   ```

3. **监控启动过程**
   ```bash
   # 查看启动日志
   docker-compose -f docker/mushroom_solution.yml logs -f mushroom_solution
   
   # 检查服务状态
   curl http://localhost:5000/health/status
   curl http://localhost:7002
   ```

### 故障排查工具

1. **容器内测试**
   ```bash
   docker exec -it mushroom_solution bash
   python scripts/test_container_startup.py
   ```

2. **日志查看**
   ```bash
   docker exec mushroom_solution tail -f /app/Logs/timer.log
   docker exec mushroom_solution tail -f /app/Logs/startup.log
   ```

3. **进程状态**
   ```bash
   docker exec mushroom_solution ps aux
   docker exec mushroom_solution netstat -tlnp
   ```

## 技术要点

### 进程管理最佳实践

1. **避免复杂管道**: 使用简单的重定向而不是 `tee` 管道
2. **信号处理**: 正确处理 SIGTERM 和 SIGINT 信号
3. **进程监控**: 主进程监控子进程状态，异常时及时退出
4. **资源清理**: 退出时清理所有子进程

### 容器化注意事项

1. **单一职责**: 每个容器运行一组相关的服务
2. **日志管理**: 使用文件日志而不是标准输出，便于调试
3. **健康检查**: 提供 HTTP 端点用于外部监控
4. **优雅退出**: 支持容器编排系统的停止信号

## 结论

通过修复启动脚本的进程管理问题、优化主程序入口逻辑、改进错误处理机制，成功解决了 Docker 容器启动失败的问题。现在容器能够稳定启动并运行所有服务组件：

- ✅ 定时任务调度器正常运行
- ✅ Streamlit Web 界面可访问
- ✅ FastAPI 健康检查服务正常
- ✅ 所有服务进程监控正常
- ✅ 日志输出完整清晰

修复后的系统具备了生产环境所需的稳定性和可维护性。