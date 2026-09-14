#!/usr/bin/env bash
# 回归：验证两条历史故障路径现在都能自愈
#   A. docker restart（同容器重启，/tmp 保留）
#   B. 运行期 Xvfb 死掉（watchdog 是否真的带走容器并被 restart 拉起）
set -uo pipefail
D=/home/sysadmin/algorithm/mushroom_docker/xcloudsdk_py_offline_20260120_175307
cd "$D"
CID="$(docker compose ps -q)"
echo "时间: $(date '+%F %T')   容器 CID=${CID:0:12}"

x_probe() {
  docker exec "$CID" python3 - <<'PY' 2>&1 | sed 's/^/  /'
import os, socket
disp = os.environ.get("XVFB_DISPLAY") or ":99"
p = f"/tmp/.X11-unix/X{disp.lstrip(':')}"
try:
    s = socket.socket(socket.AF_UNIX); s.settimeout(1); s.connect(p); s.close()
    print(f"✅ {p} 可连接")
except Exception as e:
    print(f"❌ {p} 不可连接: {e}")
PY
}

wait_healthy() {
  local i=0 st
  for i in $(seq 1 24); do
    st="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$CID" 2>/dev/null)"
    printf '  [+%02ds] health=%s\n' $((i * 5)) "${st:-?}"
    [ "${st}" = "healthy" ] && { echo "  ✅ healthy"; return 0; }
    sleep 5
  done
  echo "  ⚠️ 120s 内未恢复 healthy（末尾=${st:-?}）"; return 1
}

echo
echo "===== A. docker restart（同容器重启，容器 /tmp 保留）====="
docker inspect "$CID" --format '  restart 前: 启动于={{.State.StartedAt}} RestartCount={{.RestartCount}}'
docker restart "$CID" >/dev/null 2>&1 && echo "  已执行 docker restart"
wait_healthy
x_probe
echo "  近 400 行 500 计数: $(docker logs --tail 400 "$CID" 2>&1 | grep -c 'capture?ip=.*" 500')"
echo

echo "===== B. 运行期杀掉 Xvfb（检验 watchdog 是否带走容器）====="
RC_BEFORE="$(docker inspect "$CID" --format '{{.RestartCount}}')"
echo "  杀之前 RestartCount=${RC_BEFORE}"
docker exec "$CID" sh -c 'p=$(cat /tmp/.X99-lock 2>/dev/null); echo "  容器内 Xvfb pid=$p"; kill "$p"'
echo "  已杀，等待 watchdog（探测间隔 5s × 连续 2 次 → 约 10s）+ restart 拉起"
sleep 15
echo "  杀之后:"
docker inspect "$CID" --format '    RestartCount={{.RestartCount}}  状态={{.State.Status}}  上次退出码={{.State.ExitCode}}  启动于={{.State.StartedAt}}'
echo "  --- 容器日志尾部（应能看到 watchdog 的报错）---"
docker logs --tail 12 "$CID" 2>&1 | grep -E 'watchdog|连续|不可用|Xvfb|采图服务退出|Starting Xvfb|就绪' | tail -8 | sed 's/^/    /'
wait_healthy
x_probe
echo

echo "===== C. 最终端到端抓拍 ====="
curl -s -m 60 -o /tmp/_c.txt -w '  http=%{http_code} 耗时=%{time_total}s\n' \
  "http://127.0.0.1:7003/dynamic_capture?ip=192.168.1.238&user=admin&storage=local&filename=mushfix_regress" 2>&1
head -c 260 /tmp/_c.txt 2>/dev/null; echo; rm -f /tmp/_c.txt
ls -lt "$D/saved_datas/picture"/*.jpg 2>/dev/null | head -3 | awk '{print "    "$6" "$7" "$8"  "$5" B  "$9}'
echo
docker compose ps 2>&1 | sed 's/^/  /'
docker inspect "$CID" --format '  健康={{.State.Health.Status}} RestartCount={{.RestartCount}}' 2>&1
