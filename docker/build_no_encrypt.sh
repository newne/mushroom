#!/bin/bash
set -e

# 专门用于无加密构建的脚本
echo "Building without PyArmor encryption..."

# 配置变量
: "${DOCKER_REGISTRY:=registry.cn-beijing.aliyuncs.com/ncgnewne}"
: "${BUILD_IMAGE:=true}"
: "${PUSH_IMAGE:=true}"

REGISTRY="${DOCKER_REGISTRY}"
PROJECT_NAME="mushroom_solution"
GIT_HASH=$(git rev-parse --short HEAD 2>/dev/null || echo "no-git")
BUILD_DATE=$(date -u +'%Y-%m-%dT%H:%M:%SZ')
VERSION=$(date +%Y%m%d%H%M%S)

# 清理旧的构建信息
[ -f build_info.json ] && rm build_info.json

# 直接复制源代码，不使用PyArmor
echo "Copying source code without encryption..."
[ -d dist ] && rm -rf dist
mkdir -p dist
cp -r src dist/
echo "Source code copied to dist/src/"

# 读取版本信息
BASE_VERSION=$(uv version --short --dry-run 2>/dev/null || echo "1.0.0")

if [ "${BUILD_IMAGE}" = "true" ]; then
    # 构建完整版本号
    FULL_VERSION="${BASE_VERSION}-${VERSION}-${GIT_HASH}"

    # 显示构建信息
    echo "Building ${PROJECT_NAME} (unencrypted)"
    echo "Version: ${FULL_VERSION}"
    echo "Git Hash: ${GIT_HASH}"
    echo "Build Date: ${BUILD_DATE}"

    # 启用 BuildKit
    export DOCKER_BUILDKIT=1

    # 构建Docker镜像
    if ! docker build \
        --network host \
        --build-arg BUILD_DATE="${BUILD_DATE}" \
        --build-arg VERSION="${FULL_VERSION}" \
        --build-arg GIT_HASH="${GIT_HASH}" \
        --build-arg BASE_VERSION="${BASE_VERSION}" \
        --tag "${REGISTRY}/${PROJECT_NAME}:${FULL_VERSION}" \
        --tag "${REGISTRY}/${PROJECT_NAME}:latest" \
        --file docker/Dockerfile \
        --progress=plain \
        .; then
        echo "Error: Docker image build failed" >&2
        exit 1
    fi

    echo "Successfully built ${PROJECT_NAME}:${FULL_VERSION} (unencrypted)"

    # 推送到镜像仓库
    if [ "${PUSH_IMAGE}" = "true" ]; then
        echo "Pushing images to registry ${REGISTRY}..."

        # 检查 registry 是否可访问
        if ! docker login "${REGISTRY}" &>/dev/null; then
            echo "Error: Unable to login to registry ${REGISTRY}" >&2
            exit 1
        fi

        if ! docker push "${REGISTRY}/${PROJECT_NAME}:${FULL_VERSION}" || \
           ! docker push "${REGISTRY}/${PROJECT_NAME}:latest"; then
            echo "Error: Failed to push images to registry" >&2
            exit 1
        fi

        echo "Successfully pushed images to registry"
    fi
fi

echo "Build completed successfully (without encryption)"