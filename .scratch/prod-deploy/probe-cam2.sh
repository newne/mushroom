#!/bin/bash
# 定位 7003 服务归属 + 实际采图验证（只读 + 一次采图，不含运动指令）
echo "===== 1. 谁在监听 7003 ====="
ss -tlnp 2>/dev/null | grep -E ':700[0-9]|:8088' || echo "  (ss 无匹配)"
echo "--- 7003 已建立的连接 ---"
ss -tnp 2>/dev/null | grep ':7003' || echo "  (无)"

echo
echo "===== 2. 采集服务进程/容器全景 ====="
docker ps -a --format '{{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}' 2>&1 | grep -iE 'captur|xcloud|mushroom|camera' || echo "  (无名字匹配)"
echo "--- 全部容器名（用于比对）---"
docker ps --format '{{.Names}}' 2>&1 | tr '\n' ' '
echo

echo
echo "===== 3. 采图服务自身信息 ====="
curl -s -m 8 http://127.0.0.1:7003/ -w "\n  http=%{http_code}\n" 2>&1 | head -20
echo "--- /readyz ---"
curl -s -m 8 http://127.0.0.1:7003/readyz -w "\n  http=%{http_code}\n" 2>&1 | head -10
echo "--- /healthz ---"
curl -s -m 8 http://127.0.0.1:7003/healthz -w "\n  http=%{http_code}\n" 2>&1 | head -10

echo
echo "===== 4. 相机 192.168.1.238 直连探测 ====="
curl -s -m 6 -o /dev/null -w "  http://192.168.1.238/  ->  http=%{http_code}  time=%{time_total}s\n" http://192.168.1.238/ 2>&1
timeout 4 bash -c 'exec 3<>/dev/tcp/192.168.1.238/554 && echo "  RTSP 554: OPEN"' 2>&1 || echo "  RTSP 554: 关闭/拒绝"

echo
echo "===== 5. 实际采图（storage=local，只写服务端本地）====="
echo "--- 5a. 用你给的 /dynamic_capture 形式 ---"
curl -s -m 60 "http://127.0.0.1:7003/dynamic_capture?ip=192.168.1.238&user=admin&storage=local&filename=sweep_probe_a" -w "\n  http=%{http_code} time=%{time_total}s\n" 2>&1 | head -30
echo "--- 5b. 对照 /capture ---"
curl -s -m 60 "http://127.0.0.1:7003/capture?ip=192.168.1.238&user=admin&storage=local&filename=sweep_probe_b" -w "\n  http=%{http_code} time=%{time_total}s\n" 2>&1 | head -30

echo
echo "===== 6. 采图落地文件 ====="
find / -xdev -name 'sweep_probe*' -o -name 'sweep_237e*' 2>/dev/null | head -10
echo "--- 可疑输出目录 ---"
for d in /opt /data /home/sysadmin /var/lib/docker/volumes; do
  [ -d "$d" ] && find "$d" -maxdepth 3 -iname '*sweep*' 2>/dev/null | head -5
done
