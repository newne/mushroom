#!/usr/bin/env bash
# 切换后验证：健康检查严格化 + 端到端真抓一张
set -uo pipefail
D=/home/sysadmin/algorithm/mushroom_docker/xcloudsdk_py_offline_20260120_175307
cd "$D"
CID="$(docker compose ps -q)"

echo "===== 1. /healthz 原始返回 ====="
curl -s -m 3 -w '  http=%{http_code}\n' http://127.0.0.1:7003/healthz 2>&1
echo

echo "===== 2. 严格化后的健康检查（改挂载文件即生效，无需重启容器）====="
docker exec "$CID" python3 /app/capture-healthcheck.py 2>&1 | sed 's/^/  /'
echo "  退出码=$?"
echo

echo "===== 3. X socket 决定性判据 ====="
docker exec "$CID" python3 - <<'PY' 2>&1 | sed 's/^/  /'
import os, socket
disp = os.environ.get("XVFB_DISPLAY") or ":99"
path = f"/tmp/.X11-unix/X{disp.lstrip(':')}"
try:
    s = socket.socket(socket.AF_UNIX); s.settimeout(1); s.connect(path); s.close()
    print(f"✅ {path} 可连接")
except Exception as exc:
    print(f"❌ {path} 不可连接: {exc}")
PY
echo

echo "===== 4. 端到端真抓一张（238, storage=local）====="
echo "  时间: $(date '+%F %T')"
curl -s -m 60 -o /tmp/_c.txt -w '  http=%{http_code} 耗时=%{time_total}s\n' \
  "http://127.0.0.1:7003/dynamic_capture?ip=192.168.1.238&user=admin&storage=local&filename=mushfix_238" 2>&1
head -c 300 /tmp/_c.txt 2>/dev/null; echo
rm -f /tmp/_c.txt
echo "  图片目录最新 4 个："
ls -lt "$D/saved_datas/picture"/*.jpg 2>/dev/null | head -4 | awk '{print "    "$6" "$7" "$8"  "$5" B  "$9}'
echo

echo "===== 5. docker 健康状态 ====="
docker inspect "$CID" --format '  健康={{.State.Health.Status}} 连续失败={{.State.Health.FailingStreak}}
{{range .State.Health.Log}}  最近检查 exit={{.ExitCode}}: {{.Output}}
{{end}}' 2>&1 | head -10
docker compose ps 2>&1 | sed 's/^/  /'
