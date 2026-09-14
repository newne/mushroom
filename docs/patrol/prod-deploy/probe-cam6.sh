#!/usr/bin/env bash
# 只读：定位容器内 Xvfb 的启动方式（entrypoint / compose / 手工）
D=/home/sysadmin/algorithm/mushroom_docker/xcloudsdk_py_offline_20260120_175307
C=xcloudsdk_py_offline_20260120_175307-xcloud-capture-1

echo "===== 1. compose 文件全文 ====="
ls -la "$D" 2>&1
echo "--- docker-compose.yml ---"
cat "$D/docker-compose.yml" 2>&1
echo

echo "===== 2. 目录里的启动脚本 ====="
for f in "$D"/*.sh "$D"/Dockerfile* "$D"/entrypoint*; do
  [ -f "$f" ] && { echo "### $f"; cat "$f"; echo; }
done
echo

echo "===== 3. 容器内 entrypoint 与进程 ====="
docker exec "$C" sh -lc 'echo "--- /entrypoint.sh ---"; cat /entrypoint.sh 2>&1; echo; echo "--- ps ---"; ps aux 2>&1 | head -20' 2>&1
echo

echo "===== 4. 容器内此刻的 DISPLAY / X 状态 ====="
docker exec "$C" sh -lc 'echo "DISPLAY=[${DISPLAY:-<空>}]"; ls -la /tmp/.X11-unix 2>&1; pgrep -a Xvfb 2>&1 || echo "(容器内无 Xvfb 进程视图)"' 2>&1
echo

echo "===== 5. 宿主机上谁在管 Xvfb :99 ====="
ps -o pid,ppid,user,etime,cmd -p 453755 2>&1
echo "--- 453755 的父进程链 ---"
P=453755
for i in 1 2 3; do
  read -r PP CMD < <(ps -o ppid=,cmd= -p "$P" 2>/dev/null | head -1)
  echo "  pid=$P  ppid=${PP:-?}  cmd=$(echo "${CMD:-}" | cut -c1-90)"
  [ -z "${PP:-}" ] && break
  [ "$PP" = "0" ] && break
  P=$PP
done
echo "--- 该进程是否在容器命名空间内 ---"
cat /proc/453818/cgroup 2>&1 | head -3
echo "--- 该进程的 root 链接（容器 or 宿主）---"
readlink /proc/453818/root 2>&1
echo

echo "===== 6. 最近成功截图（确认链路已通）====="
ls -lat "$D/saved_datas/picture" 2>&1 | head -12
echo

echo "===== 7. 服务的 journal 尾部（容器 / 宿主）====="
echo "--- 容器日志（末 25 行）---"
docker logs --tail 25 "$C" 2>&1
echo
echo "--- 宿主 xcloud-capture.service journal ---"
journalctl -u xcloud-capture --no-pager -n 10 2>&1
echo

echo "===== 8. 容器 restart 策略与 compose 是否声明 xvfb ====="
grep -nE 'xvfb|DISPLAY|command|entrypoint|restart' "$D/docker-compose.yml" 2>&1 || echo "  (compose 里无 xvfb/DISPLAY 相关声明)"
