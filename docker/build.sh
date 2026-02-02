#!/bin/bash
set -e

# 统一构建脚本 - 支持加密/非加密构建，优化缓存和构建速度
echo "Building mushroom_solution with unified Docker strategy..."

# ============================================================================
# 配置变量
# ============================================================================
: "${DOCKER_REGISTRY:=registry.cn-beijing.aliyuncs.com/ncgnewne}"
: "${ENCRYPT:=true}"  # 默认使用加密，可通过环境变量控制
: "${BUILD_IMAGE:=true}"
: "${PUSH_IMAGE:=true}"
: "${USE_CACHE:=true}"
: "${CACHE_FROM:=}"  # 可选的缓存源镜像
: "${EXPIRATION_DATE:=}"  # CodeEnigma过期日期
: "${OBFUSCATION_TOOL:=codeenigma}"  # 混淆工具: codeenigma 或 pyarmor

REGISTRY="${DOCKER_REGISTRY}"
PROJECT_NAME="mushroom_solution"
GIT_HASH=$(git rev-parse --short HEAD 2>/dev/null || echo "no-git")
BUILD_DATE=$(date -u +'%Y-%m-%dT%H:%M:%SZ')
VERSION=$(date +%Y%m%d%H%M%S)

# 清理旧的构建信息
[ -f build_info.json ] && rm build_info.json

# ============================================================================
# 代码准备阶段
# ============================================================================
echo "Preparing source code..."

if [ "${ENCRYPT}" = "true" ]; then
    echo "Running code obfuscation with ${OBFUSCATION_TOOL}..."

    # 激活虚拟环境
    if [ -f ".venv/bin/activate" ]; then
        source .venv/bin/activate
        echo "Activated virtual environment"
    fi

    if [ "${OBFUSCATION_TOOL}" = "codeenigma" ]; then
        # 使用CodeEnigma进行混淆
        if ! command -v codeenigma &> /dev/null; then
            echo "Warning: codeenigma is not installed, skipping encryption" >&2
            ENCRYPT="false"
        else
            # 清理旧的输出目录
            [ -d dist ] && rm -rf dist
            [ -d cedist ] && rm -rf cedist
            
            # 构建CodeEnigma命令 - 只混淆源代码
            CODEENIGMA_CMD="codeenigma obfuscate src --output dist --verbose"
            
            # 添加过期日期（如果指定）
            if [ -n "${EXPIRATION_DATE}" ]; then
                CODEENIGMA_CMD="${CODEENIGMA_CMD} --expiration ${EXPIRATION_DATE}"
                echo "Setting expiration date: ${EXPIRATION_DATE}"
            fi
            
            # 执行代码混淆
            echo "Executing: ${CODEENIGMA_CMD}"
            
            # CodeEnigma需要Poetry格式的pyproject.toml，但我们使用UV格式
            # 临时移除pyproject.toml，避免格式冲突
            TEMP_PYPROJECT=""
            if [ -f "pyproject.toml" ]; then
                TEMP_PYPROJECT="pyproject.toml.backup"
                echo "Temporarily backing up pyproject.toml (UV format not compatible with CodeEnigma)"
                mv pyproject.toml "$TEMP_PYPROJECT"
            fi
            
            # 为CodeEnigma创建一个最小的Poetry格式pyproject.toml
            # 通过设置空的packages列表来跳过wheel构建
            cat > pyproject.toml << 'EOF'
[tool.poetry]
name = "mushroom_solution_temp"
version = "0.1.0"
description = "Temporary project for CodeEnigma obfuscation"
authors = ["temp"]
packages = []

[tool.poetry.dependencies]
python = "^3.12"

[build-system]
requires = ["poetry-core"]
build-backend = "poetry.core.masonry.api"
EOF
            
            # 执行混淆，由于packages为空，wheel构建会被自动跳过
            echo "Executing CodeEnigma obfuscation (wheel building will be skipped)"
            eval ${CODEENIGMA_CMD} 2>&1 | grep -v "No file/folder found for package" | grep -v "Error during obfuscation" || true
            
            # 删除临时的Poetry格式pyproject.toml
            rm -f pyproject.toml
            
            # 恢复原始的UV格式pyproject.toml
            if [ -n "$TEMP_PYPROJECT" ] && [ -f "$TEMP_PYPROJECT" ]; then
                echo "Restoring original UV format pyproject.toml"
                mv "$TEMP_PYPROJECT" pyproject.toml
            fi
            
            # 检查混淆是否成功
            if [ -d "dist/src" ] && [ "$(ls -A dist/src 2>/dev/null)" ]; then
                echo "CodeEnigma obfuscation completed successfully"
                
                # 处理运行时文件（兼容不同输出路径）
                runtime_file=$(find dist -maxdepth 4 -type f -name "codeenigma_runtime*.so" | head -n 1 || true)
                if [ -n "$runtime_file" ] && [ -f "$runtime_file" ]; then
                    echo "Moving CodeEnigma runtime file to src directory: $runtime_file"
                    mv "$runtime_file" dist/src/
                else
                    echo "Error: CodeEnigma runtime file not found, falling back to unencrypted build" >&2
                    ENCRYPT="false"
                fi
                
                # 清理不需要的文件
                cd dist
                find . -maxdepth 1 -type f -name "*.toml" -delete 2>/dev/null || true
                find . -maxdepth 1 -type f -name "*.whl" -delete 2>/dev/null || true
                find . -maxdepth 1 -type d -name "codeenigma_runtime" -exec rm -rf {} + 2>/dev/null || true
                cd ..
                
                # 复制必要的目录
                [ -d examples ] && cp -r examples dist/ 2>/dev/null || echo "Warning: examples directory not found"
                
            else
                echo "Warning: CodeEnigma obfuscation failed" >&2
                ENCRYPT="false"
            fi
        fi
    elif [ "${OBFUSCATION_TOOL}" = "pyarmor" ]; then
        # 使用PyArmor进行混淆
        if ! command -v pyarmor &> /dev/null; then
            echo "Warning: pyarmor is not installed, skipping encryption" >&2
            ENCRYPT="false"
        else
            pyarmor cfg clear_module_co=0
            pyarmor cfg restrict_module=0
            pyarmor cfg -p src/streamlit_app.py obf_code=0
            
            # 检查并删除dist目录
            [ -d dist ] && rm -rf dist
            
            # 生成pyarmor保护的代码
            if ! pyarmor gen -O dist -r src/; then
                echo "Warning: PyArmor generation failed, falling back to unencrypted build" >&2
                ENCRYPT="false"
            else
                echo "PyArmor generated successfully"
                
                # 处理 pyarmor_runtime
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
                
                # 复制必要的目录
                [ -d examples ] && cp -r examples dist/ 2>/dev/null || echo "Warning: examples directory not found"
            fi
        fi
    else
        echo "Warning: Unknown obfuscation tool '${OBFUSCATION_TOOL}', skipping encryption" >&2
        ENCRYPT="false"
    fi
fi

# 如果加密失败或跳过，直接复制源代码
if [ "${ENCRYPT}" = "false" ]; then
    echo "Using unencrypted source code..."
    [ -d dist ] && rm -rf dist
    mkdir -p dist
    cp -r src dist/
    cp -r examples dist/ 2>/dev/null || echo "Warning: examples directory not found"
    echo "Source code copied to dist/"
fi

# ============================================================================
# Docker构建阶段
# ============================================================================
if [ "${BUILD_IMAGE}" = "true" ]; then
    # 构建完整版本号
    BASE_VERSION=$(uv version --short --dry-run 2>/dev/null || echo "1.0.0")
    FULL_VERSION="${BASE_VERSION}-${VERSION}-${GIT_HASH}"
    
    # 显示构建信息（不再在版本号中添加加密标识）
    if [ "${ENCRYPT}" = "true" ]; then
        echo "Building encrypted version"
    else
        echo "Building unencrypted version"
    fi

    # 显示构建信息
    echo "============================================================================"
    echo "Building ${PROJECT_NAME}"
    echo "Version: ${FULL_VERSION}"
    echo "Git Hash: ${GIT_HASH}"
    echo "Build Date: ${BUILD_DATE}"
    echo "Encryption: ${ENCRYPT}"
    echo "Use Cache: ${USE_CACHE}"
    echo "============================================================================"

    # 启用 BuildKit（若 buildx 不可用则自动降级）
    if docker buildx version >/dev/null 2>&1; then
        export DOCKER_BUILDKIT=1
        export BUILDKIT_PROGRESS=plain
        echo "BuildKit enabled (buildx detected)"
    else
        export DOCKER_BUILDKIT=0
        unset BUILDKIT_PROGRESS
        echo "BuildKit disabled (buildx missing); falling back to legacy builder"
    fi
    
    # 构建参数
    BUILD_ARGS=(
        --network host
        --build-arg BUILD_DATE="${BUILD_DATE}"
        --build-arg VERSION="${FULL_VERSION}"
        --build-arg GIT_HASH="${GIT_HASH}"
        --build-arg BASE_VERSION="${BASE_VERSION}"
        --build-arg ENCRYPTED="${ENCRYPT}"
        --tag "${REGISTRY}/${PROJECT_NAME}:${FULL_VERSION}"
        --tag "${REGISTRY}/${PROJECT_NAME}:latest"
        --file docker/Dockerfile
    )

    # BuildKit 才支持 --progress
    if [ "${DOCKER_BUILDKIT}" = "1" ]; then
        BUILD_ARGS+=(--progress=plain)
    fi
    
    # 添加缓存配置
    if [ "${USE_CACHE}" = "true" ]; then
        echo "Using Docker layer cache and build cache"
        
        # 如果指定了缓存源镜像，使用镜像缓存
        if [ -n "${CACHE_FROM}" ]; then
            BUILD_ARGS+=(--cache-from "${CACHE_FROM}")
        fi
        
        # 尝试使用最新镜像作为缓存源
        BUILD_ARGS+=(--cache-from "${REGISTRY}/${PROJECT_NAME}:latest")
        
        # 尝试拉取最新镜像用于缓存（忽略错误）
        echo "Attempting to pull latest image for cache..."
        docker pull "${REGISTRY}/${PROJECT_NAME}:latest" 2>/dev/null || echo "Could not pull latest image, proceeding without image cache"
    fi
    
    # 执行构建
    echo "Starting Docker build..."
    if ! docker build "${BUILD_ARGS[@]}" .; then
        echo "Error: Docker image build failed" >&2
        exit 1
    fi

    echo "Successfully built ${PROJECT_NAME}:${FULL_VERSION}"
    
    # 显示镜像大小
    echo "Image size:"
    docker images "${REGISTRY}/${PROJECT_NAME}:${FULL_VERSION}" --format "table {{.Repository}}:{{.Tag}}\t{{.Size}}"

    # ============================================================================
    # 推送镜像
    # ============================================================================
    if [ "${PUSH_IMAGE}" = "true" ]; then
        echo "Pushing images to registry ${REGISTRY}..."

        # 检查 registry 是否可访问
        if ! docker login "${REGISTRY}" &>/dev/null; then
            echo "Error: Unable to login to registry ${REGISTRY}" >&2
            exit 1
        fi

        # 推送镜像
        echo "Pushing ${REGISTRY}/${PROJECT_NAME}:${FULL_VERSION}..."
        if ! docker push "${REGISTRY}/${PROJECT_NAME}:${FULL_VERSION}"; then
            echo "Error: Failed to push versioned image" >&2
            exit 1
        fi
        
        echo "Pushing ${REGISTRY}/${PROJECT_NAME}:latest..."
        if ! docker push "${REGISTRY}/${PROJECT_NAME}:latest"; then
            echo "Error: Failed to push latest image" >&2
            exit 1
        fi

        echo "Successfully pushed images to registry"
        
        # 推送缓存镜像（可选）
        if [ "${USE_CACHE}" = "true" ]; then
            CACHE_TAG="${REGISTRY}/${PROJECT_NAME}:cache"
            docker tag "${REGISTRY}/${PROJECT_NAME}:latest" "${CACHE_TAG}"
            docker push "${CACHE_TAG}" || echo "Warning: Failed to push cache image"
        fi
    fi
fi

# ============================================================================
# 生成构建信息
# ============================================================================
cat > build_info.json << EOF
{
    "project": "${PROJECT_NAME}",
    "version": "${FULL_VERSION}",
    "git_hash": "${GIT_HASH}",
    "build_date": "${BUILD_DATE}",
    "encrypted": ${ENCRYPT},
    "use_cache": ${USE_CACHE},
    "obfuscation_tool": "${OBFUSCATION_TOOL}",
    "expiration_date": "${EXPIRATION_DATE:-null}",
    "registry": "${REGISTRY}",
    "cache_strategy": "optimized",
    "build_optimization": "enabled"
}
EOF

echo "============================================================================"
echo "Build completed successfully"
echo "Build info saved to build_info.json"
echo "============================================================================"

# 清理临时文件
if [ "${ENCRYPT}" = "true" ] && [ -d "cedist" ]; then
    echo "Cleaning up temporary files..."
    rm -rf cedist
fi

echo "Build process completed!"