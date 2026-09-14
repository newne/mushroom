#!/usr/bin/env bash
# M0 只读探测：连接控制器 -> 读设备参数(Get_Device_Para) -> 读状态(Get_Status) -> 断开。
# 全程不含任何运动指令（无 home / jog / abs / goto），可安全执行。
set -u
export FMC4030_LIB_PATH=/opt/mushroom-patrol/lib/libFMC4030_2009_1.so
export PYTHONPATH=/opt/mushroom-patrol/src
cd /opt/mushroom-patrol

printf 'para\nstatus\nquit\n' | python3 -m patrol.debug \
  --ip 192.168.1.239 --port 8088 --device 1 2>&1
echo "exit=$?"
