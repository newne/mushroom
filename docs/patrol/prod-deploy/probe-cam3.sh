#!/bin/bash
# 采图服务 500 的根因定位：显示环境 / 容器配置 / 历史产物时间。只读。
C=xcloudsdk_py_offline_20260120_175307-xcloud-capture-1

echo "===== 1. 产物目录（成功过吗？）====="
ls -la --time-style=long-iso /home/sysadmin/algorithm/mushroom_docker/xcloudsdk_py_offline_20260120_175307/saved_datas/picture/ 2>&1 | head -20
echo "--- 全部 saved_datas 子目录 ---"
ls -la --time-style=long-iso /home/sysadmin/algorithm/mushroom_docker/xcloudsdk_py_offline_20260120_175307/saved_datas/ 2>&1 | head -15

echo
echo "===== 2. 容器显示环境 ====="
docker exec $C sh -c 'echo "DISPLAY=[$DISPLAY]"; env | grep -iE "display|xvfb|headless" || echo "  (无 DISPLAY 相关变量)"; echo "--- Xvfb 二进制 ---"; which Xvfb xvfb-run Xorg 2>/dev/null || echo "  (无 Xvfb)"; echo "--- 进程 ---"; ps -ef 2>/dev/null | grep -iE "Xvfb|xorg" | grep -v grep || echo "  (无 X 服务进程)"' 2>&1 | head -25

echo
echo "===== 3. 容器启动配置 ====="
docker inspect $C --format '  Cmd:        {{json .Config.Cmd}}
  Entrypoint: {{json .Config.Entrypoint}}
  WorkingDir: {{.Config.WorkingDir}}
  Env:        {{json .Config.Env}}
  Mounts:     {{range .Mounts}}{{.Source}} -> {{.Destination}}  {{end}}' 2>&1

echo
echo "===== 4. 宿主是否有 Xvfb ====="
which Xvfb xvfb-run 2>&1 || echo "  (宿主无 Xvfb)"
ls -la /tmp/.X11-unix/ 2>&1 | head -5

echo
echo "===== 5. 谁在按小时调用 7003（全盘搜）====="
grep -rl '7003' /etc/cron* /var/spool/cron /etc/systemd/system 2>/dev/null | head -10
echo "--- 所有容器内的 crontab/cron 提及 7003 ---"
for c in $(docker ps --format '{{.Names}}'); do
  hit=$(docker exec "$c" sh -c 'crontab -l 2>/dev/null; cat /etc/crontab 2>/dev/null; ls /etc/cron.d 2>/dev/null' 2>/dev/null | grep -i '7003\|capture' | head -2)
  [ -n "$hit" ] && echo "  [$c] $hit"
done
echo "--- 搜含 7003 的脚本文件（限深度）---"
find /home/sysadmin /opt /root /srv -maxdepth 4 -type f \( -name '*.sh' -o -name '*.py' -o -name '*.json' -o -name '*.yml' -o -name '*.yaml' \) 2>/dev/null | xargs grep -l '7003' 2>/dev/null | head -10

echo
echo "===== 6. 最近一次 500 的时间分布（服务日志）====="
docker logs --tail 40 $C 2>&1 | tail -40
