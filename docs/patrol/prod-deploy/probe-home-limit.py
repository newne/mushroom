#!/usr/bin/env python3
"""回零是否**真的碰到了硬限位**——用 `LIMIT_N` 位直接看。

## 为什么必须问

Y 的控制器软限位是 `[0, 4495]`，机械行程 `[0, 4492]`，而**原点就是 0**：软限位下界
与原点重合。于是"负限位够不着"有两种可能，它们对系统可信度的影响完全不同：

| 可能 | 含义 | 后果 |
| --- | --- | --- |
| 回零**不受**软限位约束（厂商回零走独立路径） | 每次回零都实打实压到同一个硬挡 | 原点＝物理基准，`home` 落点可靠 |
| 回零**也受**软限位约束 | 轴在触到开关前就被软限位拦住 | 落点取决于软限位判定，`home` 的物理含义不可信 |

决定性的证据是回零过程中 `LIMIT_N`(0x0010) 有没有置起：置起＝确实碰到了硬挡。
脚本以 50 Hz 采样轴状态，把整个回零周期的标志跳变逐帧打出来。
"""

from __future__ import annotations

import ctypes
import os
import sys
import time

os.environ.setdefault("FMC4030_LIB_PATH", "/opt/mushroom-patrol/lib/libFMC4030_2009_1.so")
sys.path.insert(0, "/opt/mushroom-patrol/src")

from patrol.fmc import Fmc4030  # noqa: E402
from patrol.fmc.loader import load_library  # noqa: E402
from patrol.fmc.status import MachineStatusStruct  # noqa: E402
from patrol.motion_profile import CONTROLLER_IP, CONTROLLER_PORT, DEVICE_ID, M1  # noqa: E402

AXIS = 1
LEAVE = 200.0                      # 先离开原点，制造可观察的寻零行程
BUF = (ctypes.c_ubyte * ctypes.sizeof(MachineStatusStruct))()
FLAGS = ((0x0800, "HOME_NONE"), (0x0080, "HOMING"), (0x0040, "HOME_DONE"),
         (0x0010, "LIMIT_N"), (0x0020, "LIMIT_P"), (0x0001, "RUNNING"))


def raw(axis: int) -> tuple[int, float]:
    client._lib.FMC4030_Get_Machine_Status(client.id, BUF)
    s = MachineStatusStruct.from_buffer_copy(bytes(BUF))
    return int(s.axisStatus[axis]), float(s.realPos[axis])


client = Fmc4030.connect(lib=load_library(), device_id=DEVICE_ID,
                         ip=CONTROLLER_IP, port=CONTROLLER_PORT)
try:
    spec = M1.by_index(AXIS)
    print(f"{spec.name} 轴：homeDir={spec.home_dir}（2=负限位）  "
          f"{spec.home_speed}/{spec.home_acc} 脱落 {spec.home_release} mm", flush=True)

    print(f"\n【一】点动离开原点 {LEAVE:+.0f} mm", flush=True)
    client.jog(AXIS, LEAVE)
    time.sleep(0.6)
    while raw(AXIS)[0] & 0x0001:
        time.sleep(0.05)
    print(f"    → Y={client.current_yz()[0]:.3f}", flush=True)

    print("\n【二】下发回零，50 Hz 采样（只在标志变化时打印）", flush=True)
    t0 = time.monotonic()
    client.home_axis(AXIS)
    prev = None
    saw_limit_n = False
    rows = []
    while time.monotonic() - t0 < 90:
        flags, pos = raw(AXIS)
        on = tuple(n for b, n in FLAGS if flags & b)
        if flags & 0x0010:
            saw_limit_n = True
        if on != prev:
            rows.append((time.monotonic() - t0, flags, pos, on))
            print(f"    t={time.monotonic() - t0:6.3f}s  raw=0x{flags:04x}  "
                  f"Y={pos:9.3f}  {' '.join(on) or '(无)'}", flush=True)
            prev = on
        if (flags & 0x0040) and not (flags & 0x0080) and time.monotonic() - t0 > 1.0:
            break
        time.sleep(0.02)

    flags, pos = raw(AXIS)
    print(f"\n总耗时 {time.monotonic() - t0:.3f} s   终态 raw=0x{flags:04x} Y={pos:.3f}", flush=True)
    print(f"\n{'=' * 56}")
    print(f"回零期间是否观察到 LIMIT_N 置起：{'是 ✅' if saw_limit_n else '否 ❌'}")
    if saw_limit_n:
        print("⇒ 回零确实压到了硬限位：**原点＝物理基准**，落点不依赖软限位判定。")
        print("  每次 home 都在同一个硬挡上，所以『回零落点』本身可当基准用。")
    else:
        print("⇒ 回零**没有**触到硬限位 ⇒ 落点由软限位决定，物理含义不可信。")
        print("  此时应把 Y 软限位下界下调（如 -50）让回零能真正压到开关，再复核原点。")
    print(f"{'=' * 56}", flush=True)

    print("\n【三】再回零一次，看落点是否可复现（同一硬挡 ⇒ 应逐次一致）", flush=True)
    client.jog(AXIS, LEAVE)
    time.sleep(0.6)
    while raw(AXIS)[0] & 0x0001:
        time.sleep(0.05)
    t0 = time.monotonic()
    client.home_all(timeout_s=150)
    y2, z2 = client.current_yz()
    print(f"    第二次回零 {time.monotonic() - t0:.1f} s，落点 Y={y2:.3f} Z={z2:.3f}", flush=True)
finally:
    client.close()
