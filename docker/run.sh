#!/bin/bash
# 生产级容器启动脚本 - 双服务模式（主应用[FastAPI+Scheduler] + Streamlit）
# 支持崩溃自动退出、日志透传、资源限制

set -euo pipefail

# =============================
# 配置参数
# =============================
APP_ROOT="/app"
LOG_DIR="$APP_ROOT/Logs"
MAIN_LOG="$LOG_DIR/main.log"
STREAMLIT_LOG="$LOG_DIR/streamlit.log"
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
    if [[ -n "${MAIN_PID:-}" ]] && kill -0 "$MAIN_PID" 2>/dev/null; then
        log "停止主应用 (PID: $MAIN_PID)"
        kill -TERM "$MAIN_PID" 2>/dev/null || true
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
cd "$APP_ROOT"

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
> "$MAIN_LOG" 2>/dev/null || true
> "$STREAMLIT_LOG" 2>/dev/null || true

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
# 启动主应用 (FastAPI + Scheduler)
# =============================
log "启动主应用 (FastAPI + Scheduler)..."

# 先测试Python环境和依赖
log "检查Python环境和依赖..."
if ! $PYTHON -c "import sys; print(f'Python {sys.version}')"; then
    fail "Python环境检查失败"
fi

if ! $PYTHON -c "import sys; sys.path.insert(0, '/app'); import scheduling.core.scheduler; print('调度器模块导入成功')"; then
    fail "调度器模块导入失败，请检查依赖"
fi

# 启动主应用
# 使用 tee 同时输出到文件和标准输出
cd "$APP_ROOT"
nohup $PYTHON main.py 2>&1 | tee -a "$MAIN_LOG" &
MAIN_PID=$!
log "主应用已启动，PID=$MAIN_PID"

# 等待启动
sleep 5
if ! kill -0 $MAIN_PID 2>/dev/null; then
    log "主应用进程已退出，查看最后几行日志："
    tail -10 "$MAIN_LOG" | while read line; do
        log "MAIN_LOG: $line"
    done
    fail "主应用启动失败，请检查代码或依赖"
fi

log "主应用运行正常"

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
    if ! kill -0 $MAIN_PID 2>/dev/null; then
        log "ERROR: 主应用进程异常退出"
        log "最后的 主应用 日志："
        tail -20 "$MAIN_LOG" | while read line; do log "  $line"; done
        
        # 同时检查业务日志
        if [ -f "$LOG_DIR/mushroom_solution-error.log" ]; then
            log "最后的业务错误日志："
            tail -20 "$LOG_DIR/mushroom_solution-error.log" | while read line; do log "  $line"; done
        fi
        fail "主应用进程异常退出"
    fi
    
    sleep 30
done
