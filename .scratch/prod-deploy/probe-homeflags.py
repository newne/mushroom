#!/usr/bin/env python3
"""测回零周期的位标志时序：home_done / homing / limit_* 在回零过程中怎么跳。

要回答的问题：
  1. 回零期间 `homing`(0x0080) 会不会置起？置起多久？（决定握手能不能用它）
  2. `home_done`(0x0040) 在回零期间会不会先清零？（若能，就是最干净的握手信号）
  3. 已经停在原点时再回零，标志怎么走？（Z 常见情形）

用法：python3 probe-homeflags.py [y|z]
"""

from __future__ import annotations

import ctypes
import os
import sys
import time

os.environ.setdefault("FMC4030_LIB_PATH", "/opt/mushroom-patrol/lib/libFMC4030_2009_1.so")
sys.path.insert(0, "/opt/mushroom-patrol/src")

from patrol.fmc import Fmc4030
from patrol.fmc.loader import load_library
from patrol.fmc.status import MachineStatusStruct
from patrol.motion_profile import CONTROLLER_IP, CONTROLLER_PORT, DEVICE_ID, M1

WHICH = (sys.argv[1] if len(sys.argv) > 1 else "y").lower()
AXIS = 1 if WHICH == "y" else 2
LEAVE = 200.0 if AXIS == 1 else -60.0   # 先离开原点，制造可观察的寻零行程
BUF = (ctypes.c_ubyte * ctypes.sizeof(MachineStatusStruct))()


def raw(axis: int) -> tuple[int, float]:
    client._lib.FMC4030_Get_Machine_Status(client.id, BUF)
    s = MachineStatusStruct.from_buffer_copy(bytes(BUF))
    return int(s.axisStatus[axis]), float(s.realPos[axis])


client = Fmc4030.connect(lib=load_library(), device_id=DEVICE_ID,
                         ip=CONTROLLER_IP, port=CONTROLLER_PORT)
FLAGS = ((0x0800, "HOME_NONE"), (0x0080, "HOMING"), (0x0040, "HOME_DONE"),
         (0x0010, "LIMIT_N"), (0x0020, "LIMIT_P"), (0x0001, "RUNNING"))
try:
    spec = M1.by_index(AXIS)
    print(f"{spec.name} 轴 {AXIS}：回零方向 homeDir={spec.home_dir}  档 {spec.home_speed}/{spec.home_acc}", flush=True)

    print(f"\n【一】先点动离开原点 {LEAVE:+g} mm", flush=True)
    client.jog(AXIS, LEAVE)
    time.sleep(0.5)                     # 越过起转窗口：此刻 running 位才可信
    while client.get_status().axes[AXIS].running:
        time.sleep(0.05)
    time.sleep(0.3)
    r, p = raw(AXIS)
    print(f"    离开后 raw=0x{r:04x} pos={p:.3f}  "
          f"{' '.join(n for b, n in FLAGS if r & b) or '(无)'}", flush=True)

    print(f"\n【二】下发 home_axis({AXIS})，50 Hz 采样标志变化", flush=True)
    t0 = time.monotonic()
    client.home_axis(AXIS)
    prev = None
    while time.monotonic() - t0 < 60:
        r, p = raw(AXIS)
        on = tuple(n for b, n in FLAGS if r & b)
        if on != prev:
            print(f"    t={time.monotonic() - t0:6.3f}s  raw=0x{r:04x}  pos={p:9.3f}  {' '.join(on) or '(无)'}", flush=True)
            prev = on
        if (r & 0x0040) and not (r & 0x0080) and time.monotonic() - t0 > 1.0:
            print(f"    → 回零完成，总耗时 {time.monotonic() - t0:.3f}s", flush=True)
            break
        time.sleep(0.02)

    r, p = raw(AXIS)
    print(f"    终态 raw=0x{r:04x} pos={p:.3f}", flush=True)
finally:
    client.close()
