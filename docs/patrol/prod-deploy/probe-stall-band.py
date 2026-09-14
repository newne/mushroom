#!/usr/bin/env python3
"""诊断：Y 轴在哪一段"卡住"——低速分级扫描 + 每次都在原地核实。

## 起因（2026-09-13 16:33 那轮巡检）

5 次 `TravelShortfallError` 全部停在 **Y ≈ 1920–2560**，而成对的失败说明它
进去出不来：

    idx18 S207 要求 2438 → 停在 2558    idx19 S206 要求 2064 → 停在 2496（从上面回不去）
    idx44 S405 要求 1690 → 停在 1967    idx45/46 要求 1315/941 → 停在 1947/1927

而 1–17 站（Y 187→2438 区间内更低的那些）与 20–43 站都正常 ⇒ 怀疑该区间有
**机械卡阻/局部阻力**（异物、拖链、导轨局部变形、皮带张力），而不是随机丢步。

## 方法

**由低到高逐格前进**（不从高处回冲，避免带着速度撞进去）：
从原点起，每步 +250 mm，用**点动档 10 mm/s**（比巡检档低 15 倍，撞上也不伤机构）；
每步失败即记录"卡阻下界"，然后**退回已证明可通过的区域**再试下一格。
反向（由高到低）另跑一遍，得到"卡阻上界"。

只读 + 常规运动指令，不新造控制路径；结束回零并把机构停在原点。
"""

from __future__ import annotations

import ctypes
import os
import sys
import time

os.environ.setdefault("FMC4030_LIB_PATH", "/opt/mushroom-patrol/lib/libFMC4030_2009_1.so")
sys.path.insert(0, "/opt/mushroom-patrol/src")

from patrol.fmc import Fmc4030, TravelShortfallError  # noqa: E402
from patrol.fmc.loader import load_library  # noqa: E402
from patrol.fmc.status import MachineStatusStruct  # noqa: E402
from patrol.motion_profile import CONTROLLER_IP, CONTROLLER_PORT, DEVICE_ID, M1  # noqa: E402

AXIS = 1
STEP = 250.0
BUF = (ctypes.c_ubyte * ctypes.sizeof(MachineStatusStruct))()


def pos() -> float:
    client._lib.FMC4030_Get_Machine_Status(client.id, BUF)
    s = MachineStatusStruct.from_buffer_copy(bytes(BUF))
    return float(s.realPos[AXIS])


def jog_to(target: float) -> tuple[bool, float, str]:
    """用**点动档**相对走到 target（相对移动，低速）。返回 (成功, 实到, 说明)。"""
    here = pos()
    delta = target - here
    if abs(delta) < 1.0:
        return True, here, "已在位"
    client.jog(AXIS, delta)
    time.sleep(0.5)
    while client.get_status().axes[AXIS].running:
        time.sleep(0.05)
    got = pos()
    if abs(target - got) > 1.0:
        return False, got, f"要求 {target:.0f} 停在 {got:.0f}（差 {target - got:+.0f}）"
    return True, got, ""


def clear_to(target: float) -> None:
    """退回一个"已证明通过"的点（多步点到点，避免一次长退再次卡住）。"""
    while abs(pos() - target) > 1.0:
        here = pos()
        nxt = target if abs(target - here) <= STEP else here + STEP * (1 if target > here else -1)
        ok, got, why = jog_to(nxt)
        if not ok:
            print(f"      ! 退回 {nxt:.0f} 也失败（停在 {got:.0f}）——退出", flush=True)
            return


client = Fmc4030.connect(lib=load_library(), device_id=DEVICE_ID,
                         ip=CONTROLLER_IP, port=CONTROLLER_PORT)
try:
    st = client.get_status()
    print(f"起始 pos=({st.real_pos[0]:.1f}, {st.real_pos[1]:.1f}, {st.real_pos[2]:.1f}) "
          f"running={[s.name for s in M1.axes if st.axes[s.index].running] or '无'}", flush=True)
    print(f"点动档 {M1.y.jog_speed} mm/s，步长 {STEP:.0f} mm\n", flush=True)

    print("【0】先回零，建立可信起点", flush=True)
    client.home_all(timeout_s=150)
    print(f"    Y={pos():.3f}\n", flush=True)

    print("【1】由低到高逐格前进（点动档）", flush=True)
    y = 0.0
    last_ok = 0.0
    first_stall = None
    while y < M1.y.travel_max - STEP:
        y += STEP
        ok, got, why = jog_to(y)
        if ok:
            last_ok = y
            print(f"    ✓ Y={got:7.1f}", flush=True)
        else:
            print(f"    ✗ Y={y:7.1f}  {why}", flush=True)
            if first_stall is None:
                first_stall = y
            clear_to(last_ok)
            print(f"      （退回 {last_ok:.0f}）", flush=True)

    print("\n【2】由高到低再扫一遍（验证『进去出不来』）", flush=True)
    y = M1.y.travel_max - STEP
    last_ok_hi = None
    while y > 0:
        ok, got, why = jog_to(y)
        if ok:
            if last_ok_hi is None:
                last_ok_hi = y
            print(f"    ✓ Y={got:7.1f}", flush=True)
        else:
            print(f"    ✗ Y={y:7.1f}  {why}", flush=True)
            if last_ok_hi is not None:
                clear_to(last_ok_hi)
                print(f"      （退回 {last_ok_hi:.0f}）", flush=True)
        y -= STEP

    print(f"\n{'=' * 56}")
    print(f"由低到高：最后一次顺利通过 Y={last_ok:.0f}，首个卡阻点 Y={first_stall}")
    if first_stall is not None:
        print(f"⇒ 卡阻区间大致从 Y≈{last_ok:.0f} 开始（步长 {STEP:.0f} 的粒度）")
    print(f"{'=' * 56}\n", flush=True)

    print("【3】收尾：回零", flush=True)
    client.home_all(timeout_s=150)
    y, z = client.current_yz()
    print(f"    落点 Y={y:.3f} Z={z:.3f}", flush=True)
finally:
    client.close()
