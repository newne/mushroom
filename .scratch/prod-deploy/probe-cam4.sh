#!/bin/bash
# 只读：采图服务的启动方式 / 源码 / 旧系统调度
D=/home/sysadmin/algorithm/mushroom_docker/xcloudsdk_py_offline_20260120_175307

echo "===== 1. 部署目录 ====="
ls -la --time-style=long-iso $D 2>&1 | head -25

echo
echo "===== 2. entrypoint.sh（关键：有没有 xvfb 分支）====="
cat $D/entrypoint.sh 2>&1 | head -60

echo
echo "===== 3. 应用源码里 headless / DISPLAY 相关 ====="
grep -rn 'DISPLAY\|headless\|xvfb\|Xvfb\|MediaSnapImage\|X11' $D/*.py $D/app/*.py 2>/dev/null | head -30

echo
echo "===== 4. systemd 单元 ====="
echo "--- xcloud-capture.service ---"
cat /etc/systemd/system/xcloud-capture.service 2>&1 | head -40
echo "--- xcloud-capture-tt.service ---"
cat /etc/systemd/system/xcloud-capture-tt.service 2>&1 | head -40
echo "--- 服务状态 ---"
systemctl is-active xcloud-capture.service xcloud-capture-tt.service 2>&1

echo
echo "===== 5. 旧系统（image_capture_tt）调度 ====="
ls -la --time-style=long-iso /home/sysadmin/algorithm/image_capture_tt 2>&1 | head -30

echo
echo "===== 6. 旧系统里用到 7003 与相机分配的行 ====="
grep -rn '7003' /home/sysadmin/algorithm/image_capture_tt --include='*.sh' --include='*.py' --include='*.json' --include='*.ini' 2>/dev/null | head -20
echo "--- 相机↔框 映射 ---"
grep -rn '192\.168\.1\.\(231\|232\|233\|234\|235\|236\|237\|238\)' /home/sysadmin/algorithm/image_capture_tt 2>/dev/null | head -20
