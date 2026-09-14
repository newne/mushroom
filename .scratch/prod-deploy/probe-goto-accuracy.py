#!/usr/bin/env python3
"""诊断：`goto` 到底有没有走到位——记录运动过程中的位置轨迹。

起因：丢步判定脚本里出现两处"没走到位却不报错"——

    ! 起点未到位：要求 Y=2000.0，实到 Y=1228.081
    第3次  → 32.1s Y 0.0→4374.5（要求 4442）  共 8749 mm

`goto` 的契约是"到位确认由本方法负责，超时抛 MotionTimeoutError"。既然没抛，
就说明它**认为**到位了。本脚本用 20 Hz 采样把一次 goto 的位置/运行位/到达时刻
全部记下来，回答三件事：

1. 停止是"控制器报 running 清零"还是"主机侧 wait_stop 提前认账"？
2. 停车位置与指令位置的偏差是多少（指令计数 vs 实际读数）？
3. 偏差与**同轴前一条指令**有无关系（起转窗口/静默丢弃的老问题，见 ADR-0010）？

只读 + 常规运动指令，不新增控制路径。全程两端留余量，不贴软限位。
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

BUF = (ctypes.c_ubyte * ctypes.sizeof(MachineStatusStruct))()
RUNNING = 0x0001


def raw() -> tuple[int, float, float]:
    client._lib.FMC4030_Get_Machine_Status(client.id, BUF)
    s = MachineStatusStruct.from_buffer_copy(bytes(BUF))
    return (int(s.axisStatus[1]), float(s.realPos[1]), float(s.realSpeed[1]))


def trace_goto(target: float, label: str) -> None:
    """下一次 goto(target, -21.2)，20Hz 采样到 running 清零为止。"""
    flags, y0, _ = raw()
    print(f"\n[{label}] goto → Y={target:.1f}（起点 Y={y0:.3f}）", flush=True)
    t0 = time.monotonic()
    client.goto(target, -21.2)
    host_dt = time.monotonic() - t0
    flags, y1, _ = raw()
    print(f"  goto 返回耗时 {host_dt:.2f} s   返回后读数 Y={y1:.3f}   "
          f"指令-读数差 {target - y1:+.3f} mm   running={'是' if flags & RUNNING else '否'}", flush=True)
    if flags & RUNNING:
        print("  ! goto 返回时轴仍在 running —— 主机侧等待提前认账", flush=True)
        t1 = time.monotonic()
        while raw()[0] & RUNNING and time.monotonic() - t1 < 60:
            time.sleep(0.05)
        flags, y2, _ = raw()
        print(f"  实际停稳于 Y={y2:.3f}（又等了 {time.monotonic() - t1:.2f} s）"
              f"  指令-读数差 {target - y2:+.3f} mm", flush=True)
    else:
        print(f"  → 控制器已停，落点差 {target - y1:+.3f} mm", flush=True)


client = Fmc4030.connect(lib=load_library(), device_id=DEVICE_ID,
                         ip=CONTROLLER_IP, port=CONTROLLER_PORT)
try:
    st = client.get_status()
    print(f"起始 Y={st.real_pos[1]:.3f} Z={st.real_pos[2]:.3f} mode={st.run_mode}")
    if not all(st.axes[a.index].homed for a in M1.axes):
        raise SystemExit("✋ 未回零——先 home 再来")

    print("\n【一】单次长距离 goto（复现『起点未到位』）", flush=True)
    client.home_all(timeout_s=150)
    trace_goto(2000.0, "A 0→2000")
    trace_goto(0.0, "B 2000→0")

    print("\n【二】连做多次长距离 goto（复现『第3次差 67mm』）", flush=True)
    for i in range(7):
        trace_goto(4442.0, f"C{i + 1} 0→4442")
        trace_goto(0.0, f"C{i + 1} 4442→0")

    print("\n【三】收尾回零", flush=True)
    client.home_all(timeout_s=150)
    y, z = client.current_yz()
    print(f"落点 Y={y:.3f} Z={z:.3f}", flush=True)
finally:
    client.close()
