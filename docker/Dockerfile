# Stage 1: UV installation
FROM ghcr.io/astral-sh/uv:0.9.24 AS uv
# Stage 2: Build stage
FROM python:3.12.12-slim AS builder

WORKDIR /app
# Create and activate virtual environment
RUN --mount=from=uv,source=/uv,target=./uv \
    ./uv venv /opt/venv

ENV VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:$PATH" \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    TZ=Asia/Shanghai \
    UV_HTTP_TIMEOUT=100 \
    UV_LINK_MODE=copy

# Install dependencies and cleanup in one layer
RUN --mount=type=cache,target=~/.cache/uv  \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    --mount=from=uv,source=/uv,target=./uv \
    ./uv sync --active --frozen --extra cpu --no-install-project --no-dev  --no-build-isolation -v \
      --index-url https://mirrors.aliyun.com/pypi/simple && \
    ./uv clean
# Stage 3: Runtime stage
FROM python:3.12.12-slim AS runtime
RUN apt-get update && apt-get install -y net-tools && rm -rf /var/lib/apt/lists/*

# Set runtime environment variables
ENV VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONHASHSEED=random \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    TZ=Asia/Shanghai

WORKDIR /app

COPY --from=builder  /opt/venv /opt/venv
COPY dist/src/ ./
COPY  docker/run.sh ./

CMD ["bash", "run.sh"]


