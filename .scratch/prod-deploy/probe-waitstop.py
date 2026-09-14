#!/usr/bin/env python3
"""直接给 `wait_stop` 做体检：它到底什么时候、为什么返回。

## 已知

- 我自己直接下发 `Line_2Axis` 时，信号完全正常：RUN 置起 → 2.613 s 后 RUN 清零 +
  speed=0，位置 374.329 正确。
- 但 `client.goto()` 在 **1 ms** 就返回了（探针里 t_clear=0.001）。而 `goto` 内部
  正是 `wait_stop`。⇒ **`wait_stop` 提前返回了**，不是状态字的问题。

## 本探针

在**一次运动**上同时做两件事：

1. 后台线程 50 Hz 采样状态（RUN/speed/pos），记时间线；
2. 主线程调用 `client.wait_stop(...)`，记录它何时返回、返回值。

两者对照即可看出 `wait_stop` 在时间线的哪一点返回、当时状态字是什么。
再叠加 `client.goto()` 做同样对照，确认 `goto` 是否受其影响。
"""

from __future__ import annotations

import ctypes
import os
import sys
import threading
import time

os.environ.setdefault("FMC4030_LIB_PATH", "/opt/mushroom-patrol/lib/libFMC4030_2009_1.so")
sys.path.insert(0, "/opt/mushroom-patrol/src")

from patrol.fmc import Fmc4030  # noqa: E402
from patrol.fmc.loader import load_library  # noqa: E402
from patrol.fmc.status import MachineStatusStruct  # noqa: E402
from patrol.motion_profile import CONTROLLER_IP, CONTROLLER_PORT, DEVICE_ID, M1  # noqa: E402

AXIS = 1
BUF = (ctypes.c_ubyte * ctypes.sizeof(MachineStatusStruct))()
stop_flag = threading.Event()
timeline: list[tuple[float, int, float, float]] = []


def sampler(t0: float) -> None:
    while not stop_flag.is_set():
        client._lib.FMC4030_Get_Machine_Status(client.id, BUF)
        s = MachineStatusStruct.from_buffer_copy(bytes(BUF))
        timeline.append((time.monotonic() - t0, int(s.axisStatus[AXIS]),
                         float(s.realPos[AXIS]), float(s.realSpeed[AXIS])))
        time.sleep(0.02)


def report(title: str) -> None:
    print(f"\n--- {title}：状态时间线（只打变化点） ---", flush=True)
    prev = None
    for t, flags, p, sp in timeline:
        key = (flags, round(sp, 2))
        if key != prev:
            print(f"    t={t:6.3f}s raw=0x{flags:04x} RUN={bool(flags & 1)} "
                  f"pos={p:9.3f} speed={sp:7.3f}", flush=True)
            prev = key
    print(f"    （共 {len(timeline)} 个采样）", flush=True)


client = Fmc4030.connect(lib=load_library(), device_id=DEVICE_ID,
                         ip=CONTROLLER_IP, port=CONTROLLER_PORT)
try:
    st = client.get_status()
    if not all(st.axes[a.index].homed for a in M1.axes):
        print("未回零（上一次失败后 homed 位被清零）——先回零", flush=True)
        client.home_all(timeout_s=150)
        print(f"回零落点 {client.current_yz()}", flush=True)

    # ---------- A) 裸 wait_stop ----------
    print("=== A) 裸 Line_2Axis + wait_stop（不复用 goto） ===", flush=True)
    client.home_all(timeout_s=150)
    timeline.clear()
    stop_flag.clear()
    th = threading.Thread(target=sampler, args=(time.monotonic(),), daemon=True)
    t0 = time.monotonic()
    th.start()
    v = M1.y.travel_speed
    client._lib.FMC4030_Line_2Axis(client.id, M1.axis_mask, 374.33, -21.2, v, v * 10, v * 10)
    t_issue = time.monotonic() - t0
    ok = client.wait_stop(timeout_s=60)
    t_ret = time.monotonic() - t0
    stop_flag.set()
    th.join(timeout=2)
    print(f"    下发于 t={t_issue:.3f}s；wait_stop 返回 {ok} 于 t={t_ret:.3f}s"
          f"（耗时 {t_ret - t_issue:.3f}s）", flush=True)
    print(f"    返回时 pos={client.current_yz()[0]:.3f}", flush=True)
    report("A")

    # ---------- B) client.goto ----------
    print("\n=== B) client.goto（内部走 wait_stop） ===", flush=True)
    client.home_all(timeout_s=150)
    timeline.clear()
    stop_flag.clear()
    th = threading.Thread(target=sampler, args=(time.monotonic(),), daemon=True)
    t0 = time.monotonic()
    th.start()
    client.goto(374.33, -21.2)
    t_ret = time.monotonic() - t0
    stop_flag.set()
    th.join(timeout=2)
    print(f"    goto 返回于 t={t_ret:.3f}s；返回时 pos={client.current_yz()[0]:.3f}", flush=True)
    report("B")
    time.sleep(1.5)
    print(f"    1.5s 后 pos={client.current_yz()[0]:.3f}", flush=True)

    print("\n收尾回零…", flush=True)
    client.home_all(timeout_s=150)
    print(f"落点 = {client.current_yz()}", flush=True)
finally:
    client.close()
