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

# =============================
# 初始化设置
# =============================
log "开始启动服务..."

cd "$APP_ROOT"
export PYTHONPATH="$APP_ROOT"
export TZ=Asia/Shanghai

# 清理旧日志（避免累积）
> "$TIMER_LOG" 2>/dev/null || true
> "$STREAMLIT_LOG" 2>/dev/null || true
> "$FASTAPI_LOG" 2>/dev/null || true

export OMP_NUM_THREADS=4
export OPENBLAS_NUM_THREADS=4
export MKL_NUM_THREADS=4
export NUMEXPR_NUM_THREADS=4
export VECLIB_MAXIMUM_THREADS=4
export NUMBA_NUM_THREADS=4

log "已设置线程限制：OMP/OpenBLAS/MKL ≤ 4 threads"


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
FASTAPI_CMD="$PYTHON -m uvicorn main:app --host 0.0.0.0 --port 5000 --workers 1"
nohup $FASTAPI_CMD 2>&1 | tee -a "$FASTAPI_LOG" &
FASTAPI_PID=$!
log "FastAPI 健康检查服务已启动，PID=$FASTAPI_PID"

sleep 2
if ! kill -0 $FASTAPI_PID 2>/dev/null; then
    fail "FastAPI 健康检查服务启动失败，请检查端口占用或配置"
fi

# =============================
# 启动定时任务
# =============================
# 1. 定时任务（阻塞型）---- 后台，但日志不再写 /proc/1/fd/1
log "启动定时任务 main.py..."
coproc tee -a "$TIMER_LOG" >/dev/stdout          # 当前 bash 的 stdout
nohup $PYTHON main.py 2>&1 >&${COPROC[1]} &
TIMER_PID=$!
log "定时任务已启动，PID=$TIMER_PID"

# 初步验证是否存活
sleep 3
if ! kill -0 $TIMER_PID 2>/dev/null; then
    fail "定时任务启动失败，请检查代码或依赖"
fi




log "所有服务已成功启动。保持容器活跃中..."

# =============================
# 主进程挂起，等待任意后台进程结束
# =============================
wait