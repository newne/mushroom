#!/bin/bash
# Docker缓存管理脚本
# 用于管理Docker构建缓存，提升构建效率

set -e

CACHE_DIR="/tmp/docker-cache"
REGISTRY="${DOCKER_REGISTRY:-registry.cn-beijing.aliyuncs.com/ncgnewne}"
PROJECT_NAME="mushroom_solution"

# 显示帮助信息
show_help() {
    cat << EOF
Docker缓存管理脚本

用法: $0 [命令] [选项]

命令:
    init        初始化缓存目录
    clean       清理本地缓存
    info        显示缓存信息
    pull        拉取远程缓存镜像
    push        推送缓存镜像到远程
    prune       清理Docker系统缓存
    optimize    优化Docker缓存设置

选项:
    -h, --help  显示此帮助信息
    -v          详细输出

示例:
    $0 init                 # 初始化缓存
    $0 clean                # 清理本地缓存
    $0 pull                 # 拉取远程缓存
    $0 info                 # 显示缓存信息
EOF
}

# 初始化缓存目录
init_cache() {
    echo "初始化Docker缓存目录..."
    mkdir -p "${CACHE_DIR}"
    
    # 设置权限
    chmod 755 "${CACHE_DIR}"
    
    # 创建缓存配置文件
    cat > "${CACHE_DIR}/config.json" << EOF
{
    "version": "1.0",
    "created": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
    "project": "${PROJECT_NAME}",
    "cache_type": "buildkit"
}
EOF
    
    echo "缓存目录已初始化: ${CACHE_DIR}"
}

# 清理本地缓存
clean_cache() {
    echo "清理本地Docker缓存..."
    
    if [ -d "${CACHE_DIR}" ]; then
        echo "清理缓存目录: ${CACHE_DIR}"
        rm -rf "${CACHE_DIR}"/*
        echo "本地缓存已清理"
    else
        echo "缓存目录不存在: ${CACHE_DIR}"
    fi
}

# 显示缓存信息
show_cache_info() {
    echo "Docker缓存信息:"
    echo "==============================================="
    
    # 本地缓存信息
    if [ -d "${CACHE_DIR}" ]; then
        echo "本地缓存目录: ${CACHE_DIR}"
        echo "缓存大小: $(du -sh "${CACHE_DIR}" 2>/dev/null | cut -f1 || echo "0B")"
        echo "文件数量: $(find "${CACHE_DIR}" -type f 2>/dev/null | wc -l || echo "0")"
        
        if [ -f "${CACHE_DIR}/config.json" ]; then
            echo "缓存配置:"
            cat "${CACHE_DIR}/config.json" | jq . 2>/dev/null || cat "${CACHE_DIR}/config.json"
        fi
    else
        echo "本地缓存目录不存在"
    fi
    
    echo ""
    echo "Docker系统缓存:"
    docker system df
    
    echo ""
    echo "BuildKit缓存:"
    docker buildx du 2>/dev/null || echo "BuildKit缓存信息不可用"
}

# 拉取远程缓存镜像
pull_cache() {
    echo "拉取远程缓存镜像..."
    
    CACHE_IMAGES=(
        "${REGISTRY}/${PROJECT_NAME}:latest"
        "${REGISTRY}/${PROJECT_NAME}:cache"
    )
    
    for image in "${CACHE_IMAGES[@]}"; do
        echo "拉取缓存镜像: ${image}"
        if docker pull "${image}" 2>/dev/null; then
            echo "✓ 成功拉取: ${image}"
        else
            echo "✗ 拉取失败: ${image}"
        fi
    done
}

# 推送缓存镜像
push_cache() {
    echo "推送缓存镜像到远程..."
    
    # 检查是否有本地镜像
    if ! docker images "${REGISTRY}/${PROJECT_NAME}" --format "{{.Repository}}:{{.Tag}}" | grep -q "latest"; then
        echo "错误: 没有找到本地镜像 ${REGISTRY}/${PROJECT_NAME}:latest"
        exit 1
    fi
    
    # 创建缓存标签
    CACHE_TAG="${REGISTRY}/${PROJECT_NAME}:cache"
    docker tag "${REGISTRY}/${PROJECT_NAME}:latest" "${CACHE_TAG}"
    
    # 推送缓存镜像
    echo "推送缓存镜像: ${CACHE_TAG}"
    if docker push "${CACHE_TAG}"; then
        echo "✓ 缓存镜像推送成功"
    else
        echo "✗ 缓存镜像推送失败"
        exit 1
    fi
}

# 清理Docker系统缓存
prune_docker() {
    echo "清理Docker系统缓存..."
    
    echo "清理未使用的镜像..."
    docker image prune -f
    
    echo "清理未使用的容器..."
    docker container prune -f
    
    echo "清理未使用的网络..."
    docker network prune -f
    
    echo "清理未使用的卷..."
    docker volume prune -f
    
    echo "清理构建缓存..."
    docker builder prune -f
    
    echo "Docker系统缓存清理完成"
}

# 优化Docker缓存设置
optimize_docker() {
    echo "优化Docker缓存设置..."
    
    # 检查Docker版本
    DOCKER_VERSION=$(docker version --format '{{.Server.Version}}' 2>/dev/null || echo "unknown")
    echo "Docker版本: ${DOCKER_VERSION}"
    
    # 检查BuildKit支持
    if docker buildx version >/dev/null 2>&1; then
        echo "✓ BuildKit支持已启用"
        
        # 创建专用的builder实例
        BUILDER_NAME="mushroom-builder"
        if ! docker buildx ls | grep -q "${BUILDER_NAME}"; then
            echo "创建专用builder实例: ${BUILDER_NAME}"
            docker buildx create --name "${BUILDER_NAME}" --driver docker-container --use
        else
            echo "✓ Builder实例已存在: ${BUILDER_NAME}"
        fi
        
        # 启动builder实例
        docker buildx inspect --bootstrap
        
    else
        echo "✗ BuildKit不可用，建议升级Docker版本"
    fi
    
    # 设置环境变量
    echo "设置Docker环境变量..."
    export DOCKER_BUILDKIT=1
    export COMPOSE_DOCKER_CLI_BUILD=1
    
    echo "Docker缓存优化完成"
}

# 主函数
main() {
    case "${1:-}" in
        init)
            init_cache
            ;;
        clean)
            clean_cache
            ;;
        info)
            show_cache_info
            ;;
        pull)
            pull_cache
            ;;
        push)
            push_cache
            ;;
        prune)
            prune_docker
            ;;
        optimize)
            optimize_docker
            ;;
        -h|--help|help)
            show_help
            ;;
        "")
            echo "错误: 请指定命令"
            echo "使用 '$0 --help' 查看帮助信息"
            exit 1
            ;;
        *)
            echo "错误: 未知命令 '$1'"
            echo "使用 '$0 --help' 查看帮助信息"
            exit 1
            ;;
    esac
}

# 执行主函数
main "$@"