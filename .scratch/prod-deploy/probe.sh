#!/usr/bin/env bash
# prod 只读盘点（10.77.77.39）—— 不含任何运动指令，可安全执行。
#
# 用法（在能访问 10.77.77.39 的终端里跑）：
#   plink -ssh -pw '<密码>' root@10.77.77.39 -m .scratch/prod-deploy/probe.sh
# 或先 scp 上去再执行：
#   plink -ssh -pw '<密码>' root@10.77.77.39 "bash -s" < .scratch/prod-deploy/probe.sh
#
# 目的：一次性回答「A 类环境」+「B 类控制器寻址前提」，见 gap-list.md。

set -u

hr() { printf '\n===== %s =====\n' "$1"; }

hr "1. 系统与架构"
uname -a
[ -r /etc/os-release ] && head -4 /etc/os-release
echo "arch: $(dpkg --print-architecture 2>/dev/null || uname -m)"
echo "python3: $(python3 -V 2>&1)"
uptime

hr "2. 网络接口与路由"
(ip -br addr 2>/dev/null || ifconfig -a 2>/dev/null) | head -30
echo "--- route -> controller 192.168.1.239 ---"
ip route get 192.168.1.239 2>&1
echo "--- route -> camera 192.168.1.238 ---"
ip route get 192.168.1.238 2>&1

hr "3. 控制器 / 相机 / 本机服务 端口探测"
probe() {
  if timeout 3 bash -c "exec 3<>/dev/tcp/$1/$2" 2>/dev/null; then
    echo "  $1:$2 OPEN"
  else
    echo "  $1:$2 CLOSED/TIMEOUT"
  fi
}
probe 192.168.1.239 8088    # FMC4030 控制器（默认端口）
probe 192.168.1.238 80      # 相机 HTTP
probe 192.168.1.238 554     # 相机 RTSP
probe 127.0.0.1 7003        # 截图服务
probe 127.0.0.1 8000        # analysis 接收 API

hr "4. 厂商 SDK 与既有部署"
echo "--- 厂商动态库 ---"
find / -xdev \( -name 'libFMC4030*' -o -name 'libXCloudSDK*' \) 2>/dev/null | head -20
echo "--- 已有代码目录 ---"
for d in /root /opt /srv /home; do
  [ -d "$d" ] && find "$d" -maxdepth 3 \
    \( -name 'patrol' -o -name 'xcloud*' -o -name 'analysis' -o -name 'mushroom*' \) \
    2>/dev/null | head -20
done

hr "5. 监听端口与相关服务"
ss -ltnp 2>/dev/null | head -25
systemctl list-units --type=service --state=running --no-pager 2>/dev/null \
  | grep -Ei 'capture|xcloud|patrol|analysis|minio|docker'

hr "6. 时钟与资源"
timedatectl 2>/dev/null | head -6
df -h / /var 2>/dev/null
free -h 2>/dev/null

hr "7. Docker（若存在）"
if command -v docker >/dev/null 2>&1; then
  docker ps --format '{{.Names}}\t{{.Image}}\t{{.Status}}' 2>&1 | head -20
else
  echo "  (无 docker)"
fi

hr "DONE"
