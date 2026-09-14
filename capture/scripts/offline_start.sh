#!/usr/bin/env bash
set -euo pipefail

# 在“无网服务器”离线包目录执行：
# 1) 导入镜像
# 2) 启动 compose

need_cmd() { command -v "$1" >/dev/null 2>&1 || { echo "❌ 缺少命令: $1" >&2; exit 1; }; }
need_cmd docker

if [ ! -f "./image.tar.gz" ]; then
  echo "❌ 缺少 ./image.tar.gz（离线镜像包）" >&2
  exit 1
fi

echo "📦 Load image from image.tar.gz ..."
if command -v gzip >/dev/null 2>&1; then
  gzip -dc ./image.tar.gz | docker load
else
  echo "❌ 缺少 gzip，无法解压 image.tar.gz" >&2
  exit 1
fi

if [ -f "./docker-compose.yml" ]; then
  DOCKER_COMPOSE_FILE="./docker-compose.yml"
elif [ -f "./docker-compose.offline.yml" ]; then
  cp ./docker-compose.offline.yml ./docker-compose.yml
  DOCKER_COMPOSE_FILE="./docker-compose.yml"
else
  DOCKER_COMPOSE_FILE=""
fi

COMPOSE_BIN=""
if docker compose version >/dev/null 2>&1; then
  COMPOSE_BIN="docker compose"
elif command -v docker-compose >/dev/null 2>&1; then
  COMPOSE_BIN="docker-compose"
fi

echo "🚀 Start service ..."
if [ -n "${COMPOSE_BIN}" ] && [ -n "${DOCKER_COMPOSE_FILE}" ]; then
  ${COMPOSE_BIN} -f "${DOCKER_COMPOSE_FILE}" up -d
  echo "✅ Started via compose. Logs:"
  echo "  ${COMPOSE_BIN} -f ${DOCKER_COMPOSE_FILE} logs -f"
  exit 0
fi

echo "⚠️  未检测到 docker compose，改用 docker run 方式启动"
IMAGE="${IMAGE:-xcloudsdk-py:0.1.0}"
NAME="${NAME:-xcloud-capture}"
PORT="${PORT:-27003}"

mkdir -p ./saved_datas ./log ./lib/x86_64/Release ./Device

docker rm -f "${NAME}" >/dev/null 2>&1 || true
docker run -d --name "${NAME}" --restart unless-stopped \
  -p "${PORT}:7003" \
  -v "$(pwd)/lib/x86_64/Release:/app/lib/x86_64/Release:ro" \
  -v "$(pwd)/Device:/app/Device:ro" \
  -v "$(pwd)/XCloudSDKTest_config.ini:/app/XCloudSDKTest_config.ini:ro" \
  -v "$(pwd)/minio_config.json:/app/minio_config.json:ro" \
  -v "$(pwd)/saved_datas:/app/saved_datas" \
  -v "$(pwd)/log:/app/log" \
  "${IMAGE}"

echo "✅ Started via docker run. Logs:"
echo "  docker logs -f ${NAME}"
