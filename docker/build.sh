#!/bin/bash
set -e
# 配置变量
: "${DOCKER_REGISTRY:=registry.cn-beijing.aliyuncs.com/ncgnewne}"  # 允许通过环境变量覆盖
: "${ENCRYPT:=true}"
: "${BUILD_IMAGE:=true}"
: "${PUSH_IMAGE:=true}"

REGISTRY="${DOCKER_REGISTRY}"
PROJECT_NAME="mushroom_solution"
GIT_HASH=$(git rev-parse --short HEAD 2>/dev/null || echo "no-git")
BUILD_DATE=$(date -u +'%Y-%m-%dT%H:%M:%SZ')
VERSION=$(date +%Y%m%d%H%M%S)

# 清理旧的构建信息
[ -f build_info.json ] && rm build_info.json

if [ "${ENCRYPT}" = "true" ]; then
    echo "Running pyarmor to obfuscate the code..."

    # 检查 pyarmor 是否已安装
    if ! command -v pyarmor &> /dev/null; then
        echo "Error: pyarmor is not installed" >&2
        exit 1
    fi

    pyarmor cfg clear_module_co=0
    pyarmor cfg restrict_module=0
		pyarmor cfg -p src/streamlit_app.py obf_code=0
    # 检查并删除dist目录
    # 生成pyarmor保护的代码
    if ! pyarmor gen -O dist  -r src/; then
        echo "Error: PyArmor generation failed" >&2
        exit 1
    fi
    echo "PyArmor generated successfully"

    # 检查并移动 pyarmor_runtime
    PYARMOR_RUNTIME_DIR=dist/pyarmor_runtime_000000
    cp -f src/streamlit_app.py dist/src/streamlit_app.py
    if [ -d "$PYARMOR_RUNTIME_DIR" ]; then
        TARGET_DIR=dist/src/pyarmor_runtime_000000
        if [ -d "$TARGET_DIR" ] && [ "$(ls -A $TARGET_DIR)" ]; then
            echo "Warning: Target directory $TARGET_DIR is not empty, skipping move"
        else
            if ! mv "$PYARMOR_RUNTIME_DIR" dist/src/ ; then
                echo "Error: Failed to move PyArmor runtime " >&2
                exit 1
            fi
            echo "PyArmor runtime moved successfully"

        fi
    else
        echo "Warning: PyArmor runtime directory does not exist"
    fi
fi
# 读取版本信息
BASE_VERSION=$(uv version --short --dry-run  2>/dev/null || echo "1.0.0")

if [ "${BUILD_IMAGE}" = "true" ]; then
    # 构建完整版本号
    FULL_VERSION="${BASE_VERSION}-${VERSION}-${GIT_HASH}"

    # 显示构建信息
    echo "Building ${PROJECT_NAME}"
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

    echo "Successfully built ${PROJECT_NAME}:${FULL_VERSION}"

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