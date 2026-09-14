#!/usr/bin/env python3
"""测「起转窗口」：点动下发后，Check_Axis_Is_Stop 与 status.running 什么时候才翻转。

用途：`wait_stop` 现在一下发就问，控制器还没起转 ⇒ 立刻返「已停」。
要修就得知道这个假「已停」窗口有多长、以哪个判据为准。
"""

from __future__ import annotations

import os
import sys
import time

os.environ.setdefault("FMC4030_LIB_PATH", "/opt/mushroom-patrol/lib/libFMC4030_2009_1.so")
sys.path.insert(0, "/opt/mushroom-patrol/src")

from patrol.fmc import Fmc4030
from patrol.fmc.loader import load_library
from patrol.motion_profile import CONTROLLER_IP, CONTROLLER_PORT, DEVICE_ID

DIST, SPEED, ACC = 200.0, 40.0, 400.0   # 200mm @40mm/s ≈ 5s，足够采样

client = Fmc4030.connect(lib=load_library(), device_id=DEVICE_ID,
                         ip=CONTROLLER_IP, port=CONTROLLER_PORT)
try:
    y0, _ = client.current_yz()
    print(f"起点 Y={y0:.3f}；下发点动 {DIST:+g} mm @ {SPEED:g} mm/s", flush=True)
    t0 = time.monotonic()
    client.jog(1, DIST, speed=SPEED, acc=ACC)

    print(f"{'t':>7}  {'Check_Stop':>10}  {'running':>7}  {'real_speed':>10}  {'Y':>9}", flush=True)
    prev = None
    while True:
        rc = int(client._lib.FMC4030_Check_Axis_Is_Stop(client.id, 1))
        st = client.get_status()
        cur = (rc, st.axes[1].running)
        if cur != prev:
            print(f"{time.monotonic() - t0:7.3f}  {rc:>10}  {st.axes[1].running!s:>7}  "
                  f"{st.real_speed[1]:10.2f}  {st.real_pos[1]:9.3f}", flush=True)
            prev = cur
        if time.monotonic() - t0 > 8:
            break
        time.sleep(0.03)

    print(f"\n收尾：回到 Y={y0:.3f}", flush=True)
    client.goto_2axis(y0, 0.0)
    print(f"当前位置 {client.current_yz()}", flush=True)
finally:
    client.close()
