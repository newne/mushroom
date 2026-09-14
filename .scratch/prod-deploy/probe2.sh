#!/usr/bin/env bash
# prod 第二轮定向盘点：FMC4030 SDK 下落、既有采集服务、控制网络使用痕迹。
# 纯只读，不含任何运动指令。
set -u
hr() { printf '\n===== %s =====\n' "$1"; }

hr "1. 全盘搜 FMC4030 / 运动控制相关库（含大小写变体）"
find / -iname '*fmc*' -not -path '*/proc/*' -not -path '*/sys/*' 2>/dev/null | head -40
echo "--- 含 .so 的候选 ---"
find / -iname '*.so*' \( -iname '*fmc*' -o -iname '*motion*' -o -iname '*step*motor*' \) \
  -not -path '*/proc/*' 2>/dev/null | head -20

hr "2. 谁在监听 7003 / 7005 / 7000 / 7010 / 7011"
ss -ltnp 2>/dev/null | grep -E ':(7000|7002|7003|7005|7009|7010|7011|7035|7040|7070|8080|8081)\b'
echo "--- docker 端口映射 ---"
docker ps --format '{{.Names}}\t{{.Ports}}' 2>/dev/null | head -30

hr "3. 既有部署目录结构（depth 2）"
for d in /home/sysadmin/algorithm/mushroom_service /home/sysadmin/algorithm/mushroom_docker \
         /home/leapapp/image_capture /home/sysadmin/algorithm/image_capture; do
  echo "--- $d ---"
  [ -d "$d" ] && find "$d" -maxdepth 2 -printf '%y %10s %p\n' 2>/dev/null | head -30 || echo "  (不存在)"
done

hr "4. 谁引用过控制器 / 相机 IP"
grep -rIl --exclude-dir=.git --exclude-dir=node_modules --exclude-dir=__pycache__ \
  -e '192\.168\.1\.239' -e '192\.168\.1\.238' -e 'FMC4030' -e 'Line_2Axis' \
  /home /opt /srv /etc 2>/dev/null | head -30

hr "5. Python 环境与已装包（找运动控制/相机 SDK 的 python 绑定）"
python3 -c "import sys;print(sys.version)"
python3 -m pip list 2>/dev/null | grep -Ei 'fmc|motion|motor|serial|modbus|xcloud|camera|opencv|minio|fastapi|httpx|pydantic' | head -30
echo "--- 系统 python 之外的解释器 ---"
ls /usr/bin/python3* /usr/local/bin/python3* 2>/dev/null

hr "6. 相机 192.168.1.238 是什么（HTTP 头 + 命名）"
timeout 4 bash -c 'exec 3<>/dev/tcp/192.168.1.238/80; printf "GET / HTTP/1.0\r\nHost: 192.168.1.238\r\n\r\n" >&3; head -c 400 <&3' 2>&1 | tr -d '\r' | head -20

hr "7. xcloud-capture 容器（既有采集服务）"
docker inspect xcloudsdk_py_offline_20260120_175307-xcloud-capture-1 \
  --format '{{.Config.Image}} | {{.State.Status}} | {{json .Mounts}}' 2>/dev/null | head -5

hr "DONE-2"
