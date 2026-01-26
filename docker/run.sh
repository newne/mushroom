#!/bin/bash
# 生产级容器启动脚本 - 三服务模式（定时任务 + Streamlit + FastAPI）
# 支持崩溃自动退出、日志透传、资源限制

set -euo pipefail

# =============================
# 配置参数
# =============================
APP_ROOT="/app"
LOG_DIR="$APP_ROOT/Logs"
TIMER_LOG="$LOG_DIR/timer.log"
STREAMLIT_LOG="$LOG_DIR/streamlit.log"
FASTAPI_LOG="$LOG_DIR/fastapi.log"
PYTHON="${PYTHON:-python3}"

# 创建日志目录
mkdir -p "$LOG_DIR"

# =============================
# 工具函数
# =============================
log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG_DIR/startup.log"
}

fail() {
    log "ERROR: $*"
    exit 1
}

# 进程清理函数
cleanup() {
    log "收到退出信号，正在清理进程..."
    if [[ -n "${STREAMLIT_PID:-}" ]] && kill -0 "$STREAMLIT_PID" 2>/dev/null; then
        log "停止 Streamlit (PID: $STREAMLIT_PID)"
        kill -TERM "$STREAMLIT_PID" 2>/dev/null || true
    fi
    if [[ -n "${FASTAPI_PID:-}" ]] && kill -0 "$FASTAPI_PID" 2>/dev/null; then
        log "停止 FastAPI (PID: $FASTAPI_PID)"
        kill -TERM "$FASTAPI_PID" 2>/dev/null || true
    fi
    if [[ -n "${TIMER_PID:-}" ]] && kill -0 "$TIMER_PID" 2>/dev/null; then
        log "停止定时任务 (PID: $TIMER_PID)"
        kill -TERM "$TIMER_PID" 2>/dev/null || true
    fi
    exit 0
}

# 设置信号处理
trap cleanup SIGTERM SIGINT

# =============================
# 环境准备
# =============================
log "开始启动服务..."
log "环境变量已通过 Docker 配置设置"

# =============================
# 初始化设置
# =============================
log "开始启动服务..."

cd "$APP_ROOT"
# 环境变量已在脚本开始时设置

# 测试基础配置加载
log "验证配置模块可用性..."
if $PYTHON -c "import sys; sys.path.insert(0, '/app'); from dynaconf import Dynaconf; print('✓ 配置模块可用')"; then
    log "配置模块验证通过"
else
    fail "配置模块验证失败，无法启动服务"
fi

# 激活虚拟环境
if [ -d "/opt/venv" ]; then
    export VIRTUAL_ENV="/opt/venv"
    export PATH="/opt/venv/bin:$PATH"
    log "已激活虚拟环境: /opt/venv"
else
    log "警告: 虚拟环境不存在，使用系统Python"
fi

# 清理旧日志（避免累积）
> "$TIMER_LOG" 2>/dev/null || true
> "$STREAMLIT_LOG" 2>/dev/null || true
> "$FASTAPI_LOG" 2>/dev/null || true

log "线程限制已在环境设置中配置"


# =============================
# 启动 Streamlit 应用
# =============================
log "启动 Streamlit 应用..."
STREAMLIT_CMD="$PYTHON -m streamlit run streamlit_app.py \
    --server.port=7002 \
    --server.address=0.0.0.0 \
    --browser.gatherUsageStats=false \
    --server.enableCORS=false \
    --server.enableXsrfProtection=false \
    --server.maxUploadSize=100 \
    --server.maxMessageSize=200"

nohup $STREAMLIT_CMD 2>&1 | tee -a "$STREAMLIT_LOG" &
STREAMLIT_PID=$!
log "Streamlit 已启动，PID=$STREAMLIT_PID"

sleep 2
if ! kill -0 $STREAMLIT_PID 2>/dev/null; then
    fail "Streamlit 启动失败，请检查端口占用或配置"
fi

# =============================
# 启动 FastAPI 应用 (健康检查API)
# =============================
log "启动 FastAPI 健康检查服务..."
# 创建临时启动脚本避免引号嵌套问题
cat > /tmp/start_fastapi.py << 'EOF'
import sys
sys.path.insert(0, '/app')
from main import app
import uvicorn
uvicorn.run(app, host='0.0.0.0', port=5000, workers=1)
EOF

nohup $PYTHON /tmp/start_fastapi.py 2>&1 | tee -a "$FASTAPI_LOG" &
FASTAPI_PID=$!
log "FastAPI 健康检查服务已启动，PID=$FASTAPI_PID"

sleep 2
if ! kill -0 $FASTAPI_PID 2>/dev/null; then
    fail "FastAPI 健康检查服务启动失败，请检查端口占用或配置"
fi

# =============================
# 启动定时任务
# =============================
log "启动定时任务 main.py..."

# 先测试Python环境和依赖
log "检查Python环境和依赖..."
if ! $PYTHON -c "import sys; print(f'Python {sys.version}')"; then
    fail "Python环境检查失败"
fi

if ! $PYTHON -c "import sys; sys.path.insert(0, '/app'); import scheduling.optimized_scheduler; print('调度器模块导入成功')"; then
    fail "调度器模块导入失败，请检查依赖"
fi

# 启动定时任务（使用main.py，它会调用调度器）
# 使用 tee 同时输出到文件和标准输出，这样 docker logs 也能看到
cd "$APP_ROOT"
nohup $PYTHON main.py 2>&1 | tee -a "$TIMER_LOG" &
TIMER_PID=$!
log "定时任务已启动，PID=$TIMER_PID"

# 等待更长时间让任务完全启动
sleep 5
if ! kill -0 $TIMER_PID 2>/dev/null; then
    log "定时任务进程已退出，查看最后几行日志："
    tail -10 "$TIMER_LOG" | while read line; do
        log "TIMER_LOG: $line"
    done
    fail "定时任务启动失败，请检查代码或依赖"
fi

log "定时任务运行正常"

log "所有服务已成功启动。保持容器活跃中..."

# =============================
# 主进程监控循环
# =============================
while true; do
    # 检查所有进程是否还在运行
    if ! kill -0 $STREAMLIT_PID 2>/dev/null; then
        log "ERROR: Streamlit 进程异常退出"
        log "最后的 Streamlit 日志："
        tail -20 "$STREAMLIT_LOG" | while read line; do log "  $line"; done
        fail "Streamlit 进程异常退出"
    fi
    if ! kill -0 $FASTAPI_PID 2>/dev/null; then
        log "ERROR: FastAPI 进程异常退出"
        log "最后的 FastAPI 日志："
        tail -20 "$FASTAPI_LOG" | while read line; do log "  $line"; done
        fail "FastAPI 进程异常退出"
    fi
    if ! kill -0 $TIMER_PID 2>/dev/null; then
        log "ERROR: 定时任务进程异常退出"
        log "最后的定时任务日志："
        tail -20 "$TIMER_LOG" | while read line; do log "  $line"; done
        # 同时检查业务日志
        if [ -f "$LOG_DIR/mushroom_solution-error.log" ]; then
            log "最后的业务错误日志："
            tail -20 "$LOG_DIR/mushroom_solution-error.log" | while read line; do log "  $line"; done
        fi
        fail "定时任务进程异常退出，业务日志为：$(tail -50 "$TIMER_LOG")"
    fi
    
    sleep 30
done