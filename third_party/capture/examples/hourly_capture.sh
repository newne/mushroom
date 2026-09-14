#!/usr/bin/env bash
set -euo pipefail

# 示例：每小时触发一次截图（建议配合 crontab/systemd timer）
# 需要：服务已在本机 7003 端口运行

DEVICE_IP="${1:-192.168.1.231}"
USER_NAME="${2:-admin}"
PASSWORD="${3:-}"

TS="$(date +%Y%m%d_%H)"
FILENAME="hourly/${DEVICE_IP}/${TS}"

url="http://localhost:7003/pool_capture?ip=${DEVICE_IP}&user=${USER_NAME}&storage=cloud&filename=${FILENAME}"
if [ -n "${PASSWORD}" ]; then
  url="${url}&pwd=${PASSWORD}"
fi

curl -s "${url}" --max-time 30
echo
