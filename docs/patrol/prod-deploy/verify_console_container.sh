#!/usr/bin/env bash
# 容器化 console 的端到端验证（在 WSL 里跑）。路径全部写死，避免变量与换行符的干扰。
set -u
CFG=/mnt/d/code/mushroom/.scratch/prod-deploy/pc-cfg
mkdir -p "$CFG/configs" "$CFG/data" "$CFG/Logs"
printf 'room_id: "611"\nentry_date: "2026-09-04"\nbatch_no: "mogu-100"\n' > "$CFG/configs/room.yaml"
printf 'stations:\n  - {id: S101, box_id: B101, y: 187.1, z: -21.2, layer: 1, col: 1}\n  - {id: S102, box_id: B102, y: 561.5, z: -21.2, layer: 1, col: 2}\n' > "$CFG/configs/stations.yaml"
echo "配置目录："; ls -l "$CFG/configs"

docker rm -f pcprobe >/dev/null 2>&1 || true
docker run -d --name pcprobe -p 18060:8001 \
  -v "$CFG/configs:/app/configs:ro" -v "$CFG/data:/app/data:ro" -v "$CFG/Logs:/app/Logs:ro" \
  mushroom_patrol:dev console >/dev/null
for _ in $(seq 1 10); do
  sleep 2
  curl -sf -m 3 http://127.0.0.1:18060/healthz >/dev/null 2>&1 && break
done

echo "--- 页面标题 ---"
curl -s -m 5 http://127.0.0.1:18060/ | grep -o '<title>.*</title>'
echo "--- 页面字节数 ---"
curl -s -m 5 http://127.0.0.1:18060/ | wc -c
echo "--- /api/room ---"
curl -s -m 5 http://127.0.0.1:18060/api/room
echo
echo "--- /api/status ---"
curl -s -m 5 http://127.0.0.1:18060/api/status
echo
echo "--- /api/stations（前 260 字节） ---"
curl -s -m 5 http://127.0.0.1:18060/api/stations | head -c 260
echo
docker rm -f pcprobe >/dev/null && echo "=== 清理完成 ==="
