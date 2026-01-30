# 统一优化版Dockerfile - 支持加密/非加密构建，提升构建速度和缓存效率
# 
# 优化策略：
# 1. 多阶段构建，分离依赖安装和应用代码
# 2. 优化缓存层级，减少重复下载
# 3. 使用专用缓存目录，提高缓存命中率
# 4. 减少构建上下文大小
# 5. 支持加密和非加密代码构建

# ============================================================================
# Stage 1: UV工具准备阶段
# ============================================================================
FROM ghcr.io/astral-sh/uv:0.9.24 AS uv-tool

# ============================================================================
# Stage 2: 依赖缓存构建阶段
# ============================================================================
FROM python:3.12.5-slim AS dependency-builder

# 设置工作目录
WORKDIR /app

# 设置UV环境变量
ENV UV_CACHE_DIR=/opt/uv-cache \
    UV_HTTP_TIMEOUT=300 \
    UV_LINK_MODE=copy \
    UV_INDEX_URL=https://mirrors.aliyun.com/pypi/simple \
    UV_PYTHON_PREFERENCE=only-system

# 创建虚拟环境
RUN --mount=from=uv-tool,source=/uv,target=./uv \
    ./uv venv /opt/venv

# 激活虚拟环境
ENV VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:$PATH"

# 首先复制依赖文件（这些文件变更频率低，可以有效利用Docker层缓存）
COPY pyproject.toml uv.lock ./

# 安装依赖，使用持久化缓存
# 使用专用的缓存挂载点，提高缓存命中率
RUN --mount=type=cache,target=/opt/uv-cache,uid=0,gid=0,sharing=locked \
    --mount=type=cache,target=/root/.cache/pip,uid=0,gid=0,sharing=locked \
    --mount=from=uv-tool,source=/uv,target=./uv \
    ./uv sync --frozen --extra cpu --no-install-project --no-dev --no-build-isolation -v && \
    cp -r .venv/* /opt/venv/

# 清理不必要的文件，减少镜像大小
RUN --mount=from=uv-tool,source=/uv,target=./uv \
    ./uv clean && \
    find /opt/venv -name "*.pyc" -delete && \
    find /opt/venv -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true

# ============================================================================
# Stage 3: 应用构建阶段
# ============================================================================
FROM python:3.12.5-slim AS app-builder

# 设置工作目录
WORKDIR /app

# 安装系统依赖（健康检查需要curl）
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# 从依赖构建阶段复制虚拟环境
COPY --from=dependency-builder /opt/venv /opt/venv

# 设置环境变量
ENV VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONHASHSEED=random \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    TZ=Asia/Shanghai \
    PYTHONPATH="/app" \
    OMP_NUM_THREADS=4 \
    OPENBLAS_NUM_THREADS=4 \
    MKL_NUM_THREADS=4 \
    NUMEXPR_NUM_THREADS=4 \
    VECLIB_MAXIMUM_THREADS=4 \
    NUMBA_NUM_THREADS=4

# 复制所有源代码和运行时文件
COPY dist/src/ ./

# 验证运行时文件是否存在（加密构建必须包含 runtime）
RUN if [ "$ENCRYPTED" = "true" ]; then \
        if [ -f codeenigma_runtime.cpython-312-x86_64-linux-gnu.so ]; then \
            echo "✓ CodeEnigma runtime file found and copied"; \
        else \
            echo "Error: CodeEnigma runtime file not found for encrypted build"; \
            exit 1; \
        fi; \
    else \
        if [ -f codeenigma_runtime.cpython-312-x86_64-linux-gnu.so ]; then \
            echo "Note: runtime present in unencrypted build"; \
        else \
            echo "Info: runtime not required for unencrypted build"; \
        fi; \
    fi

# 配置文件目录（运行时通过挂载提供）
RUN mkdir -p ./configs

# 复制启动脚本
COPY docker/run.sh ./

# 设置脚本执行权限
RUN chmod +x run.sh

# 创建日志目录
RUN mkdir -p /app/Logs

# 构建参数（用于标记构建信息）
ARG BUILD_DATE
ARG VERSION
ARG GIT_HASH
ARG BASE_VERSION
ARG ENCRYPTED=false

# 设置标签
LABEL maintainer="mushroom-solution-team" \
      version="${VERSION}" \
      git_hash="${GIT_HASH}" \
      build_date="${BUILD_DATE}" \
      encrypted="${ENCRYPTED}" \
      description="Mushroom Solution - AI-powered mushroom growth monitoring system"

# 健康检查 - 检查主要服务是否正常运行
HEALTHCHECK --interval=600s --timeout=10s --start-period=120s --retries=3 \
    CMD curl -f http://localhost:5000/health || exit 1

# 暴露端口
EXPOSE 5000 7002

# 启动命令
CMD ["bash", "run.sh"]