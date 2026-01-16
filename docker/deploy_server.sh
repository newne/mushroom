#!/bin/bash
# 服务器端部署脚本 - 用于拉取和启动指定版本的镜像
# 
# 用法:
#   ./deploy_server.sh <IMAGE_TAG>
#   例如: ./deploy_server.sh 0.1.0-20260114100000-abc1234

set -e

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# 配置
REGISTRY="registry.cn-beijing.aliyuncs.com/ncgnewne"
PROJECT_NAME="mushroom_solution"
COMPOSE_FILE="mushroom_solution.yml"

# 检查参数
if [ $# -eq 0 ]; then
    echo -e "${RED}错误: 缺少IMAGE_TAG参数${NC}"
    echo ""
    echo "用法: $0 <IMAGE_TAG>"
    echo "示例: $0 0.1.0-20260114100000-abc1234"
    echo ""
    echo "提示: IMAGE_TAG 由本地 build.sh 脚本生成"
    exit 1
fi

IMAGE_TAG=$1
FULL_IMAGE="${REGISTRY}/${PROJECT_NAME}:${IMAGE_TAG}"

echo "=========================================="
echo -e "${BLUE}蘑菇解决方案 - 服务器部署${NC}"
echo "=========================================="
echo ""
echo "镜像标签: ${IMAGE_TAG}"
echo "完整镜像: ${FULL_IMAGE}"
echo ""

# 检查是否在docker目录
if [ ! -f "${COMPOSE_FILE}" ]; then
    echo -e "${RED}错误: 找不到 ${COMPOSE_FILE}${NC}"
    echo "请在docker目录下运行此脚本"
    exit 1
fi

# 确认部署
read -p "确认部署此版本? (y/N): " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "部署已取消"
    exit 0
fi

# 1. 停止现有容器
echo ""
echo -e "${YELLOW}步骤 1/5: 停止现有容器...${NC}"
if docker compose -f ${COMPOSE_FILE} ps | grep -q "Up"; then
    docker compose -f ${COMPOSE_FILE} down
    echo -e "${GREEN}✓ 容器已停止${NC}"
else
    echo -e "${BLUE}ℹ 没有运行中的容器${NC}"
fi

# 2. 拉取新镜像
echo ""
echo -e "${YELLOW}步骤 2/5: 拉取新镜像...${NC}"
if docker pull ${FULL_IMAGE}; then
    echo -e "${GREEN}✓ 镜像拉取成功${NC}"
else
    echo -e "${RED}✗ 镜像拉取失败${NC}"
    echo "请检查:"
    echo "  1. IMAGE_TAG 是否正确"
    echo "  2. 镜像是否已推送到仓库"
    echo "  3. 网络连接是否正常"
    exit 1
fi

# 3. 启动新容器
echo ""
echo -e "${YELLOW}步骤 3/5: 启动新容器...${NC}"
if IMAGE_TAG=${IMAGE_TAG} docker compose -f ${COMPOSE_FILE} up -d; then
    echo -e "${GREEN}✓ 容器已启动${NC}"
else
    echo -e "${RED}✗ 容器启动失败${NC}"
    exit 1
fi

# 4. 等待服务就绪
echo ""
echo -e "${YELLOW}步骤 4/5: 等待服务就绪...${NC}"

# 等待数据库
echo "等待数据库启动..."
for i in {1..12}; do
    if docker exec postgres_db pg_isready -U postgres > /dev/null 2>&1; then
        echo -e "${GREEN}✓ 数据库已就绪${NC}"
        break
    fi
    if [ $i -eq 12 ]; then
        echo -e "${RED}✗ 数据库启动超时${NC}"
        echo "查看数据库日志: docker logs postgres_db"
        exit 1
    fi
    echo "等待数据库就绪... ($i/12)"
    sleep 5
done

# 等待应用
echo "等待应用启动..."
sleep 15

# 检查容器状态
if docker ps | grep -q ${PROJECT_NAME}; then
    echo -e "${GREEN}✓ 应用容器运行中${NC}"
else
    echo -e "${RED}✗ 应用容器未运行${NC}"
    echo "查看容器日志: docker logs ${PROJECT_NAME}"
    exit 1
fi

# 5. 验证部署
echo ""
echo -e "${YELLOW}步骤 5/5: 验证部署...${NC}"

# 显示容器信息
echo ""
echo "容器状态:"
docker ps --filter "name=${PROJECT_NAME}" --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"

# 检查调度器日志
echo ""
echo "检查调度器启动..."
sleep 5

if docker logs ${PROJECT_NAME} 2>&1 | tail -50 | grep -q "调度器初始化成功"; then
    echo -e "${GREEN}✓ 调度器启动成功${NC}"
elif docker logs ${PROJECT_NAME} 2>&1 | tail -50 | grep -q "调度器运行异常"; then
    echo -e "${RED}✗ 调度器启动失败${NC}"
    echo ""
    echo "最近的错误日志:"
    docker logs ${PROJECT_NAME} 2>&1 | tail -20
    exit 1
else
    echo -e "${YELLOW}⚠ 调度器可能仍在初始化中${NC}"
fi

# 测试数据库连接
echo ""
echo "测试数据库连接..."
if docker exec ${PROJECT_NAME} prod=true python scripts/test_db_connection.py > /tmp/db_test_${IMAGE_TAG}.log 2>&1; then
    echo -e "${GREEN}✓ 数据库连接测试通过${NC}"
else
    echo -e "${YELLOW}⚠ 数据库连接测试失败${NC}"
    echo "详细日志已保存到: /tmp/db_test_${IMAGE_TAG}.log"
fi

# 部署完成
echo ""
echo "=========================================="
echo -e "${GREEN}✓ 部署完成！${NC}"
echo "=========================================="
echo ""
echo "部署信息:"
echo "  镜像版本: ${IMAGE_TAG}"
echo "  部署时间: $(date '+%Y-%m-%d %H:%M:%S')"
echo ""
echo "常用命令:"
echo "  查看应用日志:   docker logs -f ${PROJECT_NAME}"
echo "  查看数据库日志: docker logs -f postgres_db"
echo "  进入容器:       docker exec -it ${PROJECT_NAME} bash"
echo "  监控调度器:     docker exec ${PROJECT_NAME} tail -f /app/Logs/mushroom_solution-info.log"
echo "  查看错误日志:   docker exec ${PROJECT_NAME} tail -f /app/Logs/mushroom_solution-error.log"
echo "  容器状态:       docker ps | grep mushroom"
echo "  资源使用:       docker stats ${PROJECT_NAME}"
echo ""
echo "测试命令:"
echo "  数据库连接测试: docker exec ${PROJECT_NAME} prod=true python scripts/test_db_connection.py"
echo ""

# 显示最近的日志
echo "最近的应用日志:"
echo "----------------------------------------"
docker logs --tail 20 ${PROJECT_NAME} 2>&1
echo "----------------------------------------"
echo ""
echo -e "${BLUE}提示: 建议持续监控日志24小时以确保稳定运行${NC}"
echo ""
