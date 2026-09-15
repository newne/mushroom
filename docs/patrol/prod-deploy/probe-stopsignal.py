#!/usr/bin/env python3
"""找**可靠的停稳信号**：`running` 位在指令下发后 1ms 就清零（实测），不能用。

候选信号（都在状态字里，无需新接口）：
  - `realSpeed`：运动时上升，静止后归零
  - `realPos`  ：位置不再变化

本探针以 50 Hz 记录一次移动的 `axisStatus`、`realPos`、`realSpeed` 全程曲线，
回答：**有没有一个信号在"真正停稳"时刻才翻转**，以及它与位置稳定的时间关系。
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
from patrol.motion_profile import CONTROLLER_IP, CONTROLLER_PORT, DEVICE_ID  # noqa: E402

AXIS = 1
BUF = (ctypes.c_ubyte * ctypes.sizeof(MachineStatusStruct))()
FLAG = ((0x0001, "RUN"), (0x0002, "STOP"), (0x0004, "EMG"), (0x0008, "ALM"),
        (0x0010, "LN"), (0x0020, "LP"))


def snap() -> tuple[int, float, float]:
    client._lib.FMC4030_Get_Machine_Status(client.id, BUF)
    s = MachineStatusStruct.from_buffer_copy(bytes(BUF))
    return int(s.axisStatus[AXIS]), float(s.realPos[AXIS]), float(s.realSpeed[AXIS])


def trace(target: float, label: str) -> None:
    print(f"\n=== {label}：指令 Y={target:.0f} ===", flush=True)
    from patrol.motion_profile import M1
    t0 = time.monotonic()
    v = M1.y.travel_speed
    # 直接下发，绕过 wait_stop —— 本探针就是要自己看信号，不能被现有实现挡住
    client._lib.FMC4030_Line_2Axis(client.id, M1.axis_mask, target, 21.2, v, v * 10, v * 10)
    prev = None
    marks: dict[str, float] = {}
    while time.monotonic() - t0 < 60:
        flags, p, sp = snap()
        t = time.monotonic() - t0
        on = tuple(n for b, n in FLAG if flags & b)
        if marks.get("run_clear") is None and not (flags & 0x0001):
            marks["run_clear"] = t
        if marks.get("speed_zero") is None and abs(sp) < 0.01:
            marks["speed_zero"] = t
        if on != prev:
            print(f"    t={t:6.3f}s  raw=0x{flags:04x} {' '.join(on):<18} "
                  f"pos={p:9.3f} speed={sp:8.3f}", flush=True)
            prev = on
        if t > 0.5 and abs(sp) < 0.01:
            if marks.get("speed_zero_stable") is None:
                marks["speed_zero_stable"] = t
            if t - marks["speed_zero_stable"] > 0.2:
                marks["settled"] = t
                print(f"    → 停稳（speed≈0 持续 0.2s）t={t:.3f}s  pos={p:.3f}", flush=True)
                break
        time.sleep(0.02)
    print(f"    标记：{ {k: round(v, 3) for k, v in marks.items()} }", flush=True)


client = Fmc4030.connect(lib=load_library(), device_id=DEVICE_ID,
                         ip=CONTROLLER_IP, port=CONTROLLER_PORT)
try:
    from patrol.motion_profile import M1
    st = client.get_status()
    if not all(st.axes[a.index].homed for a in M1.axes):
        raise SystemExit("✋ 未回零——先 home 再来")
    client.home_all(timeout_s=150)
    trace(374.33, "短程 0→374")
    client.home_all(timeout_s=150)
    trace(4442.0, "长程 0→4442")
    client.home_all(timeout_s=150)
    print("\n收尾回零完成", flush=True)
    print(f"落点 = {client.current_yz()}", flush=True)
finally:
    client.close()
