#!/usr/bin/env bash
# 切换到加固入口 + 验证（重建容器，端口 7003 会短暂中断）
set -uo pipefail
D=/home/sysadmin/algorithm/mushroom_docker/xcloudsdk_py_offline_20260120_175307
cd "$D"

wait_healthy() {
  local label="$1" i=0 st
  for i in $(seq 1 24); do
    st="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' \
          "$(docker compose ps -q)" 2>/dev/null)"
    printf '  [+%02ds] health=%s\n' $((i * 5)) "${st:-?}"
    [ "${st}" = "healthy" ] && { echo "  ✅ ${label} 达到 healthy"; return 0; }
    sleep 5
  done
  echo "  ⚠️ ${label} 未在 120s 内变为 healthy（末尾状态=${st:-?}）"
  return 1
}

echo "===== 1. 重建容器（docker compose up -d）====="
echo "  时间: $(date '+%F %T')"
docker compose up -d 2>&1 | sed 's/^/  /'
echo

echo "===== 2. 容器概况 ====="
docker compose ps 2>&1 | sed 's/^/  /'
CID="$(docker compose ps -q)"
docker inspect "$CID" --format '  Entrypoint={{json .Config.Entrypoint}}
  Init(tini)={{.HostConfig.Init}}
  重启策略={{.HostConfig.RestartPolicy.Name}}
  时区 TZ={{range .Config.Env}}{{if (eq (printf "%.3s" .) "TZ=")}}{{.}}{{end}}{{end}}' 2>&1
echo

echo "===== 3. 等待健康 ====="
wait_healthy "重建后" || true
echo

echo "===== 4. 健康检查明细 ====="
docker inspect "$CID" --format '  状态={{.State.Status}}
  健康={{.State.Health.Status}} 连续失败={{.State.Health.FailingStreak}}
{{range .State.Health.Log}}  最近检查 exit={{.ExitCode}}: {{.Output}}
{{end}}' 2>&1 | head -12
echo

echo "===== 5. 决定性判据：X socket 真能连 + /healthz ====="
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
curl -s -m 3 -w '  http=%{http_code}\n' http://127.0.0.1:7003/healthz 2>&1
docker exec "$CID" date 2>&1 | sed 's/^/  容器时间: /'
echo "  宿主时间: $(date '+%F %T %Z')"
echo

echo "===== 6. 端到端真抓一张（238, storage=local）====="
BEFORE="$(ls -1 "$D/saved_datas/picture"/*.jpg 2>/dev/null | wc -l)"
curl -s -m 60 -o /tmp/_cap.txt -w '  http=%{http_code} 耗时=%{time_total}s\n' \
  "http://127.0.0.1:7003/dynamic_capture?ip=192.168.1.238&user=admin&storage=local&filename=mushfix_238" 2>&1
head -c 300 /tmp/_cap.txt 2>/dev/null; echo
echo "  图片目录最新 3 个文件："
ls -lt "$D/saved_datas/picture"/*.jpg 2>/dev/null | head -3 | awk '{print "    "$6" "$7" "$8"  "$5" B  "$9}'
AFTER="$(ls -1 "$D/saved_datas/picture"/*.jpg 2>/dev/null | wc -l)"
echo "  抓拍前文件数=${BEFORE} 抓拍后=${AFTER}"
rm -f /tmp/_cap.txt
echo

echo "===== 7. 近期日志（确认没有 500）====="
docker logs --tail 400 "$CID" 2>&1 | grep -c 'capture?ip=.*" 500' | sed 's/^/  近 400 行 500 计数: /'
docker logs --tail 6 "$CID" 2>&1 | sed 's/^/  /'
