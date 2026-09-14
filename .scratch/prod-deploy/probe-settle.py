#!/usr/bin/env python3
"""阶段 1：量清"运动结束后位置读数怎么走"——决定 wait_stop 的停稳判据与到位容差。

## 要回答三个问题（全部用实测数据，不猜）

1. **过渡值持续多久**：一次移动结束（running 位清零）之后，位置读数还会变多久？
   （现场见过 running 清零时读 1.8 mm，而移动其实完整完成）
2. **最终值是多少**：等完全静定后，落点与指令差多少 ⇒ 正常散布有多大
3. **散布有多大**：跑多次取分布 ⇒ **容差应当由这个分布导出**，而不是拍一个数

## 方法

一次移动结束后，以 50 Hz 采样 `axisStatus` 与 `realPos`，记录：

    t_clear     ：running 首次清零的时刻（= 现在 wait_stop 会返回的时刻）
    t_stable    ：位置首次进入并保持 ±0.05 mm 的时刻（= 真正停稳）
    pos_clear   ：running 清零那一刻的读数（= 现在校验会看到的过渡值）
    pos_final   ：静定后的读数

输出每次移动的 `(t_stable - t_clear)`、`pos_clear`、`pos_final`、`pos_final - 指令`，
并给出汇总分布。**只读 + 常规 goto，不新造控制路径**；结束回零。
"""

from __future__ import annotations

import ctypes
import os
import statistics
import sys
import time

os.environ.setdefault("FMC4030_LIB_PATH", "/opt/mushroom-patrol/lib/libFMC4030_2009_1.so")
sys.path.insert(0, "/opt/mushroom-patrol/src")

from patrol.fmc import Fmc4030  # noqa: E402
from patrol.fmc.loader import load_library  # noqa: E402
from patrol.fmc.status import MachineStatusStruct  # noqa: E402
from patrol.motion_profile import CONTROLLER_IP, CONTROLLER_PORT, DEVICE_ID, M1  # noqa: E402

AXIS = 1
RUNNING = 0x0001
STABLE_TOL = 0.05          # 判"静定"的位置带宽 mm
STABLE_HOLD = 0.20         # 需连续保持多久 s
SAMPLE_DT = 0.02           # 50 Hz
SETTLE_MAX = 6.0           # 静定等待上限 s
BUF = (ctypes.c_ubyte * ctypes.sizeof(MachineStatusStruct))()


def raw() -> tuple[bool, float]:
    client._lib.FMC4030_Get_Machine_Status(client.id, BUF)
    s = MachineStatusStruct.from_buffer_copy(bytes(BUF))
    return bool(s.axisStatus[AXIS] & RUNNING), float(s.realPos[AXIS])


def move_and_trace(target: float, speed: float | None, label: str) -> dict:
    """下发一次 goto，然后 50 Hz 跟踪到静定。返回本次的关键量。"""
    client.goto(target, -21.2, speed=speed, acc=(speed * 10.0 if speed else None))
    t_end_issue = time.monotonic()

    t_clear = None
    pos_clear = None
    t_stable = None
    hold_start = None
    last = None
    samples = 0
    while time.monotonic() - t_end_issue < SETTLE_MAX:
        busy, p = raw()
        samples += 1
        if t_clear is None and not busy:
            t_clear = time.monotonic() - t_end_issue
            pos_clear = p
        if t_clear is not None:
            if last is not None and abs(p - last) < STABLE_TOL:
                hold_start = hold_start or time.monotonic()
                if time.monotonic() - hold_start >= STABLE_HOLD:
                    t_stable = time.monotonic() - t_end_issue
                    break
            else:
                hold_start = None
            last = p
        time.sleep(SAMPLE_DT)

    # 静定后再取一次最终读数
    time.sleep(0.3)
    _, pos_final = raw()
    delta = target - pos_final
    print(f"    {label:<22} t_clear={t_clear if t_clear is None else round(t_clear, 3)}s "
          f"pos_clear={pos_clear if pos_clear is None else round(pos_clear, 3)}  "
          f"t_stable={t_stable if t_stable is None else round(t_stable, 3)}s  "
          f"pos_final={pos_final:.3f}  偏差={delta:+.3f}  ({samples} 采样)", flush=True)
    return {"label": label, "target": target, "speed": speed, "t_clear": t_clear,
            "pos_clear": pos_clear, "t_stable": t_stable, "pos_final": pos_final,
            "delta": delta}


client = Fmc4030.connect(lib=load_library(), device_id=DEVICE_ID,
                         ip=CONTROLLER_IP, port=CONTROLLER_PORT)
rows: list[dict] = []
try:
    st = client.get_status()
    if not all(st.axes[a.index].homed for a in M1.axes):
        raise SystemExit("✋ 未回零——先 home 再来")
    print(f"采样 {1 / SAMPLE_DT:.0f} Hz；静定判据 位置变化 <{STABLE_TOL} mm 持续 {STABLE_HOLD}s\n",
          flush=True)

    print("【一】短程 374 mm @巡检档 150", flush=True)
    client.home_all(timeout_s=150)
    for i in range(1, 6):
        rows.append(move_and_trace(374.33, 150.0, f"短程#{i} 0→374"))
        rows.append(move_and_trace(0.0, 150.0, f"短程#{i} 374→0"))

    print("\n【二】长程 4442 mm @巡检档 150", flush=True)
    client.home_all(timeout_s=150)
    for i in range(1, 4):
        rows.append(move_and_trace(4442.0, 150.0, f"长程#{i} 0→4442"))
        rows.append(move_and_trace(0.0, 150.0, f"长程#{i} 4442→0"))

    print(f"\n{'=' * 92}")
    print("汇总")
    print(f"{'项':<28}{'n':>4}{'min':>10}{'p50':>10}{'max':>10}")
    def stat(name: str, vals: list[float]) -> None:
        vals = [v for v in vals if v is not None]
        if not vals:
            print(f"{name:<28}{0:>4}")
            return
        print(f"{name:<28}{len(vals):>4}{min(vals):>10.3f}"
              f"{statistics.median(vals):>10.3f}{max(vals):>10.3f}")

    stat("running 清零耗时 t_clear (s)", [r["t_clear"] for r in rows])
    stat("真正静定耗时 t_stable (s)", [r["t_stable"] for r in rows])
    stat("两者之差（过渡期）(s)",
         [(r["t_stable"] - r["t_clear"]) for r in rows
          if r["t_stable"] is not None and r["t_clear"] is not None])
    stat("清零时的过渡读数 |pos_clear| (mm)",
         [abs(r["pos_clear"]) for r in rows if r["pos_clear"] is not None])
    stat("静定后 |偏差| (mm)", [abs(r["delta"]) for r in rows])
    stat("最大 |偏差| (mm)", [max(abs(r["delta"]) for r in rows)] if rows else [])
    print(f"{'=' * 92}")
    print("\n⇒ 容差应取『静定后 |偏差| 的最大值』再留余量；", flush=True)
    print("⇒ wait_stop 需覆盖『过渡期』这一列的量级，否则会读到过渡值。", flush=True)

    print("\n收尾回零…", flush=True)
    client.home_all(timeout_s=150)
    y, z = client.current_yz()
    print(f"落点 Y={y:.3f} Z={z:.3f}", flush=True)
finally:
    client.close()
