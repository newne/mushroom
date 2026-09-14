#!/usr/bin/env bash
# 只读：采图服务加固前的最后细节（健康端点、Xvfb 日志、锁文件、旧调用者时刻表）
D=/home/sysadmin/algorithm/mushroom_docker/xcloudsdk_py_offline_20260120_175307
C=xcloudsdk_py_offline_20260120_175307-xcloud-capture-1

echo "===== 1. 健康端点真实返回 ====="
for ep in / /healthz /readyz /openapi.json; do
  echo "--- GET $ep ---"
  curl -s -o /tmp/_r -w '  http=%{http_code} bytes=%{size_download}\n' "http://127.0.0.1:7003$ep" 2>&1
  head -c 400 /tmp/_r 2>/dev/null; echo; echo
done
echo

echo "===== 2. Xvfb 日志（容器内 /app/log/xvfb.log）====="
cat "$D/log/xvfb.log" 2>&1 | head -20
echo "--- /app/log 目录 ---"
ls -la "$D/log" 2>&1
echo

echo "===== 3. 容器内锁文件与 socket 现状 ====="
docker exec "$C" sh -lc 'ls -la /tmp/.X99-lock /tmp/.X11-unix/ 2>&1; echo "--- Xvfb 进程（/proc 扫描）---"; for p in /proc/[0-9]*; do if grep -qa Xvfb "$p/cmdline" 2>/dev/null; then echo "  pid=${p#/proc/} cmd=$(tr "\0" " " < "$p/cmdline")"; fi; done' 2>&1
echo

echo "===== 4. scripts/ 与 DOCKER.md ====="
ls -la "$D/scripts" 2>&1
echo "--- DOCKER.md 关键段（env / health / xvfb / 排障）---"
grep -nE 'USE_XVFB|XVFB|health|排障|排查|500|DISPLAY|headless|无头' "$D/DOCKER.md" 2>&1 | head -30
echo

echo "===== 5. compose 工具版本 ====="
docker compose version 2>&1 | head -2
docker-compose --version 2>&1 | head -2
echo

echo "===== 6. 旧调用者 image_capture_tt（宿主）====="
systemctl list-timers --all --no-pager 2>/dev/null | grep -iE 'capture|image|mushroom' || echo "  (无相关 timer)"
echo "--- 相关 systemd 单元 ---"
systemctl list-units --all --no-pager 2>/dev/null | grep -iE 'capture|image' || echo "  (无)"
echo "--- crontab ---"
crontab -l 2>&1 | head -20
echo "--- image_capture_tt 落点 ---"
find /home/sysadmin -maxdepth 4 -name 'image_capture*' 2>/dev/null | head -10
echo

echo "===== 7. 采集服务容器化后的时区（日志与宿主对照）====="
echo "宿主: $(date '+%F %T %Z')"
docker exec "$C" date 2>&1 | sed 's/^/容器: /'
echo

echo "===== 8. 当前到相机的连通性（238）====="
ping -c 1 -W 1 192.168.1.238 >/dev/null 2>&1 && echo "  238 ping OK" || echo "  238 ping 无响应（可能禁 ICMP）"
curl -s -o /dev/null -w '  238:80 http=%{http_code}\n' --max-time 3 http://192.168.1.238/ 2>&1
