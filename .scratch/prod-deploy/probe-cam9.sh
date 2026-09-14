#!/usr/bin/env bash
# 只读+微扰（自带清理）：死PID锁是否自动回收；旧调用者身份与时刻表
C=xcloudsdk_py_offline_20260120_175307-xcloud-capture-1

echo "===== A. 死 PID 的锁会被 Xvfb 自动回收吗 ====="
docker exec "$C" sh -lc '
echo 999999 > /tmp/.X97-lock
echo "  预置 /tmp/.X97-lock 内容=999999（不存在的 PID）"
Xvfb :97 -screen 0 640x480x24 -ac > /tmp/_97.log 2>&1 &
p=$!; sleep 1.5
if [ -S /tmp/.X11-unix/X97 ]; then
  echo "  A1 结果: 启动【成功】→ Xvfb 会自愈死锁（加固里 rm 属冗余保险）"
else
  echo "  A1 结果: 启动【失败】→ 死锁不会自愈（加固里必须 rm）"
  echo "       输出: $(head -3 /tmp/_97.log | tr "\n" " | ")"
fi
kill $p 2>/dev/null; wait $p 2>/dev/null
rm -f /tmp/_97.log /tmp/.X97-lock /tmp/.X11-unix/X97
echo "  已清理 :97"
echo
echo "===== A2. 同类测试：锁+残留 socket 同时存在 ====="
echo 999998 > /tmp/.X96-lock
python3 -c "
import socket
s=socket.socket(socket.AF_UNIX); s.bind(\"/tmp/.X11-unix/X96\"); s.listen(1); s.close()
print(\"  预置: /tmp/.X96-lock(死PID) + 残留 socket X96\")"
Xvfb :96 -screen 0 640x480x24 -ac > /tmp/_96.log 2>&1 &
p=$!; sleep 1.5
if [ -S /tmp/.X11-unix/X96 ]; then
  python3 -c "
import socket
try:
    s=socket.socket(socket.AF_UNIX); s.settimeout(1); s.connect(\"/tmp/.X11-unix/X96\"); s.close()
    print(\"  A2 结果: 启动【成功】且可连接 → 残留 socket 被覆盖\")
except Exception as e:
    print(\"  A2 结果: 启动【成功】但【连不上】->\", type(e).__name__, e)"
else
  echo "  A2 结果: 启动【失败】-> $(head -3 /tmp/_96.log | tr "\n" " | ")"
fi
kill $p 2>/dev/null; wait $p 2>/dev/null
rm -f /tmp/_96.log /tmp/.X96-lock /tmp/.X11-unix/X96
echo "  已清理 :96"
' 2>&1
echo

echo "===== B. 谁在调用 7003（旧系统调用者身份）====="
echo "--- 当前活动连接 ---"
ss -tnp 2>/dev/null | grep -E ':7003' || echo "  (此刻无活动连接)"
echo "--- 容器网络 ---"
docker inspect "$C" --format '  NetworkMode={{.HostConfig.NetworkMode}}  IP={{range .NetworkSettings.Networks}}{{.IPAddress}} {{end}}' 2>&1
echo "--- 宿主进程里提到 7003 / capture 的 ---"
ps -eo pid,user,cmd 2>/dev/null | grep -E '7003|_capture' | grep -v grep || echo "  (无)"
echo "--- 宿主机上含 dynamic_capture/pool_capture 字样的文件 ---"
grep -rl -E 'dynamic_capture|pool_capture' /home /opt /etc /root /data 2>/dev/null | head -15 || echo "  (无)"
echo
echo "--- 旧系统相关目录 ---"
ls -la /home/sysadmin/algorithm/ 2>&1 | head -25
echo
echo "--- 取像调用记录（容器日志里带 camera ip 的行，末 12 条）---"
docker logs --tail 400 "$C" 2>&1 | grep -oE '"GET /[a-z_]+_capture\?ip=[0-9.]+[^"]*"' | tail -12
