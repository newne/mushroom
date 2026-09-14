#!/usr/bin/env bash
# 只读+微扰：为「stale socket / stale lock」根因取证；不改服务、不改 compose
C=xcloudsdk_py_offline_20260120_175307-xcloud-capture-1
D=/home/sysadmin/algorithm/mushroom_docker/xcloudsdk_py_offline_20260120_175307

echo "===== A. 容器内可用工具（决定加固脚本能用什么）====="
docker exec "$C" sh -lc '
for c in python3 curl sh sleep rm kill mkdir date; do
  p=$(command -v "$c" 2>/dev/null); echo "  $c -> ${p:-缺失}"
done
echo "  时区库: $([ -f /usr/share/zoneinfo/Asia/Shanghai ] && echo 有 || echo 无)"
echo "  容器内 TZ=[${TZ:-<未设>}]"
' 2>&1
echo

echo "===== B. 证据1：stale socket 能不能骗过 [-S] 检查 ====="
docker exec "$C" python3 - <<'PY' 2>&1
import os, socket
path = "/tmp/.X11-unix/X98"
try: os.unlink(path)
except FileNotFoundError: pass
s = socket.socket(socket.AF_UNIX); s.bind(path); s.listen(1); s.close()   # 关闭 → 留下 stale socket 文件
print("  B1 [-S stale]=", "-S 判定:", os.path.exists(path) and "通过（socket 文件存在）")
try:
    t = socket.socket(socket.AF_UNIX); t.settimeout(1); t.connect(path); t.close()
    print("  B1 连接 stale socket: 成功（异常！）")
except Exception as e:
    print(f"  B1 连接 stale socket: 失败 -> {type(e).__name__}: {e}   <-- 真liveness 检查能识破")
try:
    t = socket.socket(socket.AF_UNIX); t.settimeout(1); t.connect("/tmp/.X11-unix/X99"); t.close()
    print("  B2 连接 live   socket(:99): 成功  <-- 正常时通过")
except Exception as e:
    print(f"  B2 连接 live   socket(:99): 失败 -> {type(e).__name__}: {e}")
os.unlink(path)
PY
echo

echo "===== C. 证据2：/tmp/.X99-lock 存在时再起 Xvfb 会怎样 ====="
docker exec "$C" sh -lc '
echo "  锁文件: $(ls -la /tmp/.X99-lock 2>&1 | tr -s " ")"
echo "  锁内容(PID): $(cat /tmp/.X99-lock 2>/dev/null)"
echo "--- 试起 Xvfb :99（应当因 already active 而失败；失败即证明锁是阻塞点）---"
Xvfb :99 -screen 0 1920x1080x24 -ac > /tmp/_x99try.log 2>&1
echo "  exit=$?"
echo "  输出: $(head -3 /tmp/_x99try.log 2>/dev/null | tr "\n" " | ")"
rm -f /tmp/_x99try.log
echo "--- 对照：起 Xvfb :98（应成功，随后杀掉）---"
Xvfb :98 -screen 0 640x480x24 -ac > /tmp/_x98.log 2>&1 &
p=$!; sleep 1.2
if [ -S /tmp/.X11-unix/X98 ]; then echo "  :98 启动成功（socket 就绪）"; else echo "  :98 启动失败: $(head -2 /tmp/_x98.log)"; fi
kill $p 2>/dev/null; wait $p 2>/dev/null
rm -f /tmp/_x98.log /tmp/.X98-lock /tmp/.X11-unix/X98
echo "  清理完成"
' 2>&1
echo

echo "===== D. 旧系统调用者 image_capture_tt 的时刻表（只读）====="
ps -eo pid,etime,user,cmd 2>/dev/null | grep -E '[i]mage_capture' || echo "  (无 image_capture 进程)"
echo "--- 目录 ---"
ls -la /home/sysadmin/algorithm/image_capture_tt 2>&1 | head -12
echo "--- 是否被 systemd / supervisor 托管 ---"
systemctl list-units --all --no-pager 2>/dev/null | grep -i image_capture || echo "  (无 systemd 单元)"
ls /etc/supervisor/conf.d/ 2>/dev/null || echo "  (无 supervisor)"
echo "--- 最近调用时刻（从容器日志里提取）---"
docker logs --tail 200 "$C" 2>&1 | grep -oE '^\[?[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2}' | tail -8 || true
echo

echo "===== E. 宿主是否有 compose 备份位与目录属主 ====="
ls -la "$D/docker-compose.yml" 2>&1
stat -c '  属主=%U:%G 权限=%a 修改=%y' "$D/docker-compose.yml" 2>&1
