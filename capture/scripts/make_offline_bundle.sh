#!/usr/bin/env bash
set -euo pipefail

# 在“有网/可构建镜像”的机器上执行：
# 产出一个可拷贝到“无网服务器”直接启动的离线包目录。

TAG="${TAG:-xcloudsdk-py:0.1.0}"
OUT_DIR="${OUT_DIR:-dist/xcloudsdk_py_offline_$(date +%Y%m%d_%H%M%S)}"
INCLUDE_MINIO_CONFIG="${INCLUDE_MINIO_CONFIG:-0}" # 1=把 minio_config.json 一起打包（含密钥）
PLATFORM="${PLATFORM:-linux/amd64}" # 强烈建议：目标服务器是 x86_64（厂商 so 也是 x86_64）
BUILD_NETWORK="${BUILD_NETWORK:-}"  # 可选：host/default；遇到 DNS 问题可尝试 BUILD_NETWORK=host
BUILDER="${BUILDER:-}"              # 可选：指定 buildx builder（Docker Desktop 推荐 desktop-linux）
BASE_IMAGE="${BASE_IMAGE:-}"        # 可选：基础镜像（Docker Hub 不可达时可改为镜像源，例如 docker.m.daocloud.io/library/python:3.10-slim-bullseye）
APT_MIRROR_URL="${APT_MIRROR_URL:-}" # 可选：APT 镜像源（默认 https://deb.debian.org；国内/网络不稳定可用 https://mirrors.aliyun.com 等）
SDK_CONFIG_SRC="${SDK_CONFIG_SRC:-}"  # 可选：XCloudSDKTest_config.ini 来源路径（默认用 repo root）
MINIO_CONFIG_SRC="${MINIO_CONFIG_SRC:-}" # 可选：minio_config.json 来源路径（默认用 repo root）
SDK_LIB_DIR_SRC="${SDK_LIB_DIR_SRC:-}"   # 可选：lib/x86_64/Release 来源目录（默认用 repo root）
DEVICE_DIR_SRC="${DEVICE_DIR_SRC:-}"     # 可选：Device/ 来源目录（默认用 repo root）

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

need_cmd() { command -v "$1" >/dev/null 2>&1 || { echo "❌ 缺少命令: $1" >&2; exit 1; }; }
need_cmd docker
need_cmd gzip
need_cmd tar

echo "Repo: ${REPO_ROOT}"
echo "Tag:  ${TAG}"
echo "Out:  ${OUT_DIR}"
echo "Plat: ${PLATFORM}"
if [ -n "${BUILD_NETWORK}" ]; then
  echo "Net:  ${BUILD_NETWORK}"
fi
if [ -n "${BUILDER}" ]; then
  echo "Bldr: ${BUILDER}"
fi
if [ -n "${BASE_IMAGE}" ]; then
  echo "Base: ${BASE_IMAGE}"
fi
if [ -n "${APT_MIRROR_URL}" ]; then
  echo "Apt:  ${APT_MIRROR_URL}"
fi
if [ -n "${SDK_CONFIG_SRC}" ]; then
  echo "Cfg:  ${SDK_CONFIG_SRC}"
fi
if [ -n "${MINIO_CONFIG_SRC}" ]; then
  echo "S3:   ${MINIO_CONFIG_SRC}"
fi
if [ -n "${SDK_LIB_DIR_SRC}" ]; then
  echo "Lib:  ${SDK_LIB_DIR_SRC}"
fi
if [ -n "${DEVICE_DIR_SRC}" ]; then
  echo "Dev:  ${DEVICE_DIR_SRC}"
fi
echo

ctx="$(docker context show 2>/dev/null || true)"
if [ -n "${ctx}" ] && [ "${ctx}" = "desktop-linux" ] && [ "${BUILDER}" = "default" ]; then
  echo "ℹ️  当前 Docker context=desktop-linux（Docker Desktop），BUILDER=default 通常指向 /var/run/docker.sock；已自动切换为 BUILDER=desktop-linux"
  BUILDER="desktop-linux"
fi

# Docker daemon 必须可用（否则后续 build/save/load 都无法执行）
if ! docker info >/dev/null 2>&1; then
  echo "❌ 无法连接 Docker daemon（context=${ctx:-unknown}）。请先启动 Docker Desktop（或启动你的 Docker daemon）后再重试。" >&2
  exit 1
fi

trap 'echo "❌ Build failed. Try: BUILD_NETWORK=host; or set APT_MIRROR_URL=https://mirrors.aliyun.com (or other mirror); if Docker Hub is blocked, try: BASE_IMAGE=docker.m.daocloud.io/library/python:3.10-slim-bullseye" >&2' ERR

echo "🐳 Build image (buildx, platform=${PLATFORM})..."
# Apple Silicon 默认会构建 linux/arm64，apt 源会走 ports.ubuntu.com，
# 而且厂商 SDK 是 x86_64，会导致镜像不可用；因此强制 linux/amd64。
build_args=(--platform "${PLATFORM}" -t "${TAG}" --load)
if [ -n "${BUILD_NETWORK}" ]; then
  build_args+=(--network "${BUILD_NETWORK}")
fi
if [ -n "${BUILDER}" ]; then
  build_args+=(--builder "${BUILDER}")
fi
if [ -n "${BASE_IMAGE}" ]; then
  build_args+=(--build-arg "BASE_IMAGE=${BASE_IMAGE}")
fi
if [ -n "${APT_MIRROR_URL}" ]; then
  build_args+=(--build-arg "APT_MIRROR_URL=${APT_MIRROR_URL}")
fi
if docker buildx build "${build_args[@]}" "${REPO_ROOT}"; then
  :
else
  # 常见问题：用户本机切换过 buildx builder（docker-container），在某些网络环境下可能会出现 IPv6/DNS 异常。
  # 若用户未显式指定 BUILDER，则自动用“当前 context 同名 builder”再试一次（Docker Desktop 通常是 desktop-linux）。
  if [ -z "${BUILDER}" ]; then
    retry_builder="${ctx:-}"
    if [ -z "${retry_builder}" ]; then
      retry_builder="desktop-linux"
    fi
    echo "⚠️  buildx failed, retry with BUILDER=${retry_builder}..."
    retry_args=(--platform "${PLATFORM}" -t "${TAG}" --load --builder "${retry_builder}")
    if [ -n "${BUILD_NETWORK}" ]; then
      retry_args+=(--network "${BUILD_NETWORK}")
    fi
    if [ -n "${BASE_IMAGE}" ]; then
      retry_args+=(--build-arg "BASE_IMAGE=${BASE_IMAGE}")
    fi
    if [ -n "${APT_MIRROR_URL}" ]; then
      retry_args+=(--build-arg "APT_MIRROR_URL=${APT_MIRROR_URL}")
    fi
    if docker buildx build "${retry_args[@]}" "${REPO_ROOT}"; then
      :
    else
      echo "⚠️  buildx failed again, fallback to docker build (still platform=${PLATFORM})..."
      docker_args=(--platform "${PLATFORM}" -t "${TAG}")
      if [ -n "${BUILD_NETWORK}" ]; then
        docker_args+=(--network "${BUILD_NETWORK}")
      fi
      if [ -n "${BASE_IMAGE}" ]; then
        docker_args+=(--build-arg "BASE_IMAGE=${BASE_IMAGE}")
      fi
      if [ -n "${APT_MIRROR_URL}" ]; then
        docker_args+=(--build-arg "APT_MIRROR_URL=${APT_MIRROR_URL}")
      fi
      DOCKER_BUILDKIT=1 docker build "${docker_args[@]}" "${REPO_ROOT}"
    fi
  else
    echo "⚠️  buildx failed, fallback to docker build (still platform=${PLATFORM})..."
    docker_args=(--platform "${PLATFORM}" -t "${TAG}")
    if [ -n "${BUILD_NETWORK}" ]; then
      docker_args+=(--network "${BUILD_NETWORK}")
    fi
    if [ -n "${BASE_IMAGE}" ]; then
      docker_args+=(--build-arg "BASE_IMAGE=${BASE_IMAGE}")
    fi
    if [ -n "${APT_MIRROR_URL}" ]; then
      docker_args+=(--build-arg "APT_MIRROR_URL=${APT_MIRROR_URL}")
    fi
    DOCKER_BUILDKIT=1 docker build "${docker_args[@]}" "${REPO_ROOT}"
  fi
fi

mkdir -p "${OUT_DIR}"

echo "📦 Save image -> ${OUT_DIR}/image.tar.gz"
docker save "${TAG}" | gzip > "${OUT_DIR}/image.tar.gz"

echo "🧾 Copy compose + docs..."
cp "${REPO_ROOT}/docker-compose.offline.yml" "${OUT_DIR}/docker-compose.yml"
cp "${REPO_ROOT}/docs/DOCKER.md" "${OUT_DIR}/DOCKER.md"
mkdir -p "${OUT_DIR}/scripts"
cp "${REPO_ROOT}/scripts/offline_start.sh" "${OUT_DIR}/scripts/offline_start.sh"
chmod +x "${OUT_DIR}/scripts/offline_start.sh" || true

echo "📁 Prepare runtime dirs..."
mkdir -p "${OUT_DIR}/lib/x86_64/Release" "${OUT_DIR}/saved_datas/picture" "${OUT_DIR}/log" "${OUT_DIR}/Device"

echo "🔧 Copy SDK libs..."
sdk_lib_dir="${SDK_LIB_DIR_SRC:-${REPO_ROOT}/lib/x86_64/Release}"
if [ -f "${sdk_lib_dir}/libXCloudSDK.so" ]; then
  # 复制目录内所有文件（包括依赖的 .so）。若存在 symlink（如本机开发用），使用 tar -h 解引用复制真实文件。
  (cd "${sdk_lib_dir}" && tar -chf - .) | (cd "${OUT_DIR}/lib/x86_64/Release" && tar -xf -)
else
  echo "❌ 未找到 ${sdk_lib_dir}/libXCloudSDK.so（请提供厂商 SDK so 后再打包）" >&2
  exit 1
fi

echo "🔐 Copy Device key files (optional)..."
device_dir="${DEVICE_DIR_SRC:-${REPO_ROOT}/Device}"
if [ -d "${device_dir}" ]; then
  (cd "${device_dir}" && tar -chf - .) | (cd "${OUT_DIR}/Device" && tar -xf -)
fi

echo "🧩 Copy SDK config..."
sdk_cfg_src="${SDK_CONFIG_SRC:-${REPO_ROOT}/XCloudSDKTest_config.ini}"
if [ -f "${sdk_cfg_src}" ]; then
  cp "${sdk_cfg_src}" "${OUT_DIR}/XCloudSDKTest_config.ini"
else
  echo "❌ 未找到 ${sdk_cfg_src}（请提供生产环境真实 XCloudSDKTest_config.ini 后再打包）" >&2
  exit 1
fi

echo "☁️  Copy MinIO config..."
minio_cfg_src="${MINIO_CONFIG_SRC:-${REPO_ROOT}/minio_config.json}"
if [ "${INCLUDE_MINIO_CONFIG}" = "1" ] && [ -f "${minio_cfg_src}" ]; then
  cp "${minio_cfg_src}" "${OUT_DIR}/minio_config.json"
elif [ "${INCLUDE_MINIO_CONFIG}" = "1" ]; then
  echo "❌ INCLUDE_MINIO_CONFIG=1 但未找到 ${minio_cfg_src}（请提供生产环境真实 minio_config.json 后再打包）" >&2
  exit 1
else
  cp "${REPO_ROOT}/minio_config.example.json" "${OUT_DIR}/minio_config.json"
  if [ "${INCLUDE_MINIO_CONFIG}" != "1" ]; then
    echo "ℹ️  默认不打包真实 minio_config.json（避免密钥泄露）；已放 example，请在离线包里改成真实配置"
  else
    :
  fi
fi

echo
echo "✅ 离线包已生成: ${OUT_DIR}"
echo "下一步：把整个目录拷贝到无网服务器，执行："
echo "  docker load <(gzip -dc image.tar.gz)   # 或 gunzip -c image.tar.gz | docker load"
echo "  docker compose up -d                   # 或 docker-compose up -d"
