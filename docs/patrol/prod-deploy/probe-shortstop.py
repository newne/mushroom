#!/usr/bin/env python3
"""诊断 2：短停之后，计数器还准不准？——决定这个缺陷的严重级别。

诊断 1 已确认：4442 mm 的长程 `goto` 偶尔**短停**且不报错（实测停在 324.489 / 1577.191，
要求 0）。现在要分清两种可能，它们的后果完全不同：

| 可能 | 含义 | 后果 |
| --- | --- | --- |
| **计数诚实短停** | 轴真的停在 324.5 处，计数器如实报 324.5 | 后续绝对定位仍然准；只是"这一趟没走完" ⇒ 采图会拍错位置 |
| **计数撒谎** | 轴物理停在别处，计数器却报 324.5 | 坐标系漂移 ⇒ **后续所有绝对定位都偏**，比上一种严重得多 |

判据：从短停位置再走一趟已知距离，看落点误差。若下一趟仍精确到位（误差 ~0），
说明计数器与实际位置一致（只是短停）；若下一趟也偏，就是坐标漂移。

安全：全程用绝对 `goto`，行程两端留余量；结束回零。
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
FAR = 4442.0
SHORT_AT = 1480.0     # 曾经短停的量级


def pos() -> float:
    client._lib.FMC4030_Get_Machine_Status(client.id, BUF)
    s = MachineStatusStruct.from_buffer_copy(bytes(BUF))
    return float(s.realPos[1])


def go(target: float, label: str) -> tuple[float, float]:
    t0 = time.monotonic()
    client.goto(target, 21.2)
    dt = time.monotonic() - t0
    y = pos()
    err = target - y
    tag = "  ← 短停!" if abs(err) > 1.0 else ""
    print(f"  {label:<26} 指令 Y={target:7.1f}  实到 Y={y:8.3f}  "
          f"误差 {err:+8.3f} mm  {dt:5.1f}s{tag}", flush=True)
    return y, err


client = Fmc4030.connect(lib=load_library(), device_id=DEVICE_ID,
                         ip=CONTROLLER_IP, port=CONTROLLER_PORT)
try:
    st = client.get_status()
    if not all(st.axes[a.index].homed for a in M1.axes):
        raise SystemExit("✋ 未回零——先 home 再来")
    print("目标：造出一次长程短停，再从短停处走一趟已知距离验证计数器\n", flush=True)

    shortfalls = 0
    for attempt in range(1, 21):
        client.goto(0.0, 21.2)
        y, err = go(FAR, f"第{attempt}趟 0→4442")
        if abs(err) > 1.0:
            shortfalls += 1
            print(f"\n  ★ 抓到短停：停在 Y={y:.3f}（要求 {FAR:.0f}），误差 {err:+.3f} mm", flush=True)
            print("    现在验证计数器是否仍然诚实：", flush=True)
            # 从短停处走 1000 mm，看落点误差
            want = y + 1000.0
            if want < M1.y.travel_max:
                y2, err2 = go(want, "  → +1000 mm 检验")
                if abs(err2) > 1.0:
                    print(f"    ⇒ ❌ 计数器撒谎：从短停处再走 1000mm 也偏了 {err2:+.3f} mm", flush=True)
                    print("       （坐标系已漂移，后续绝对定位不可信）", flush=True)
                else:
                    print(f"    ⇒ ✅ 计数器诚实：从短停处走 1000mm 误差仅 {err2:+.3f} mm", flush=True)
                    print("       （短停是『这一趟没走完』，不是坐标漂移）", flush=True)
            if shortfalls >= 2:
                break
    print(f"\n共 {shortfalls} 次短停（阈值 1.0 mm）", flush=True)
    client.home_all(timeout_s=150)
    y, z = client.current_yz()
    print(f"收尾回零落点 Y={y:.3f} Z={z:.3f}", flush=True)
finally:
    client.close()
