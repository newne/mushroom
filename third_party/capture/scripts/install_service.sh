#!/usr/bin/env bash
set -euo pipefail

echo "🚀 XCloud 截图服务（Python版）安装/升级脚本"
echo "================================================"

# 默认参数（可通过环境变量覆盖）
SERVICE_NAME="${SERVICE_NAME:-xcloud-capture-py}"
PROD_DIR="${PROD_DIR:-/home/sysadmin/algorithm/xcloudsdk_py}"
RUN_USER="${RUN_USER:-sysadmin}"
RUN_GROUP="${RUN_GROUP:-sysadmin}"
PORT="${PORT:-7003}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
BACKUP_SUFFIX="$(date +%Y%m%d_%H%M%S)"

echo "Service: ${SERVICE_NAME}"
echo "Prod dir: ${PROD_DIR}"
echo "User:    ${RUN_USER}:${RUN_GROUP}"
echo "Port:    ${PORT}"
echo "Repo:    ${REPO_ROOT}"
echo

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || { echo "❌ 缺少命令: $1"; exit 1; }
}

need_cmd python3
need_cmd curl

echo "⏹️  停止旧服务（如果存在）..."
sudo systemctl stop "${SERVICE_NAME}" 2>/dev/null || true
sleep 2

if [ -d "${PROD_DIR}" ]; then
  echo "💾 备份旧目录 -> ${PROD_DIR}.backup.${BACKUP_SUFFIX}"
  sudo mv "${PROD_DIR}" "${PROD_DIR}.backup.${BACKUP_SUFFIX}"
fi

echo "📦 部署新版本到 ${PROD_DIR}..."
sudo mkdir -p "${PROD_DIR}"
sudo cp -R "${REPO_ROOT}/." "${PROD_DIR}/"
sudo chown -R "${RUN_USER}:${RUN_GROUP}" "${PROD_DIR}"

echo "🔧 安装系统依赖（xvfb + venv）..."
if command -v apt-get >/dev/null 2>&1; then
  sudo apt-get update -y
  sudo apt-get install -y xvfb python3-venv python3-pip
fi

echo "🐍 创建 venv 并安装 Python 依赖..."
sudo -u "${RUN_USER}" bash -lc "cd \"${PROD_DIR}\" && python3 -m venv .venv"
sudo -u "${RUN_USER}" bash -lc "cd \"${PROD_DIR}\" && . .venv/bin/activate && pip install -U pip"
sudo -u "${RUN_USER}" bash -lc "cd \"${PROD_DIR}\" && . .venv/bin/activate && pip install -e ."

echo "📝 写入 systemd 服务文件..."
sudo tee "/etc/systemd/system/${SERVICE_NAME}.service" >/dev/null <<EOF
[Unit]
Description=XCloud Screenshot Capture Service (Python)
After=network.target

[Service]
Type=simple
User=${RUN_USER}
Group=${RUN_GROUP}
WorkingDirectory=${PROD_DIR}
Environment="LD_LIBRARY_PATH=${PROD_DIR}/lib/x86_64/Release"
ExecStart=/usr/bin/xvfb-run -a --server-args="-screen 0 1920x1080x24" ${PROD_DIR}/.venv/bin/python -m xcloudsdk_py --http ${PORT} --lib ./lib/x86_64/Release/libXCloudSDK.so
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal
KillMode=mixed
KillSignal=SIGTERM
TimeoutStopSec=30

[Install]
WantedBy=multi-user.target
EOF

echo "🔄 重新加载 systemd..."
sudo systemctl daemon-reload

echo "✅ 启用并启动服务..."
sudo systemctl enable "${SERVICE_NAME}"
sudo systemctl start "${SERVICE_NAME}"

echo "⏳ 等待服务启动..."
sleep 3

echo "🔍 检查服务状态..."
sudo systemctl status "${SERVICE_NAME}" --no-pager || true

echo "🧪 健康检查..."
curl -s "http://localhost:${PORT}/healthz" || true
echo

echo "🎉 安装/升级完成"
echo "查看日志: sudo journalctl -u ${SERVICE_NAME} -f"

