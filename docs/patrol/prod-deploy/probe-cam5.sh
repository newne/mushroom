#!/usr/bin/env bash
# 只读：采图服务运行形态盘点（容器 / 宿主机 systemd / xvfb 现况）
# 不含任何写操作、不重启任何服务。

echo "===== 1. 7003 监听者（谁在占用）====="
ss -ltnp 2>/dev/null | grep -E ':7003|:7004' || echo "  (无 7003 监听)"
echo

echo "===== 2. docker 容器全景 ====="
docker ps --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}' 2>&1
echo "--- 全部（含已停）---"
docker ps -a --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}' 2>&1 | head -20
echo

echo "===== 3. 采图容器明细（Env / Cmd / Entrypoint / Mounts）====="
CNAME=$(docker ps --format '{{.Names}}' 2>/dev/null | grep -i 'capture' | head -1)
echo "匹配到容器: ${CNAME:-<无>}"
if [ -n "${CNAME:-}" ]; then
  echo "--- Env ---"
  docker inspect "$CNAME" --format '{{range .Config.Env}}  {{.}}
{{end}}' 2>&1
  echo "--- Entrypoint / Cmd / WorkingDir ---"
  docker inspect "$CNAME" --format '  Entrypoint={{json .Config.Entrypoint}}
  Cmd={{json .Config.Cmd}}
  WorkingDir={{.Config.WorkingDir}}' 2>&1
  echo "--- Ports / RestartPolicy / Compose 项目 ---"
  docker inspect "$CNAME" --format '  Restart={{json .HostConfig.RestartPolicy}}
  Labels.compose={{index .Config.Labels "com.docker.compose.project"}} workdir={{index .Config.Labels "com.docker.compose.project.working_dir"}}
  Labels.configfiles={{index .Config.Labels "com.docker.compose.project.config_files"}}' 2>&1
  echo "--- Mounts ---"
  docker inspect "$CNAME" --format '{{range .Mounts}}  {{.Source}} -> {{.Destination}} ({{.Mode}})
{{end}}' 2>&1
fi
echo

echo "===== 4. 容器内 X 环境自检 ====="
if [ -n "${CNAME:-}" ]; then
  echo "--- DISPLAY / xvfb 可执行文件 ---"
  docker exec "$CNAME" sh -lc 'echo "  DISPLAY=[${DISPLAY:-<空>}]"; command -v Xvfb xvfb-run || echo "  (无 Xvfb/xvfb-run)"' 2>&1
  echo "--- 容器内 /tmp/.X11-unix ---"
  docker exec "$CNAME" sh -lc 'ls -la /tmp/.X11-unix 2>&1 | head -5' 2>&1
fi
echo

echo "===== 5. 宿主机 Xvfb / X 进程 ====="
ps -eo pid,ppid,etime,cmd 2>/dev/null | grep -E '[X]vfb|[x]vfb-run|[X]org' || echo "  (无 Xvfb/Xorg 进程)"
echo "--- /tmp/.X11-unix ---"
ls -la /tmp/.X11-unix 2>&1 | head -8
echo

echo "===== 6. 宿主机 systemd 单元 ====="
for u in xcloud-capture xcloud-capture.service; do
  echo "--- $u ---"
  systemctl status "$u" --no-pager 2>&1 | head -14
done
echo "--- 单元文件全文 ---"
for f in /etc/systemd/system/xcloud-capture.service /lib/systemd/system/xcloud-capture.service; do
  [ -f "$f" ] && { echo "### $f"; cat "$f"; echo; }
done
echo

echo "===== 7. compose / 启动脚本落点 ====="
find /home /opt /root /srv /data -maxdepth 4 \( -name 'docker-compose*.yml' -o -name 'docker-compose*.yaml' -o -name 'compose*.yml' \) 2>/dev/null | head -20
echo
