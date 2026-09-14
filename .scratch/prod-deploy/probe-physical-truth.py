#!/usr/bin/env python3
"""用**硬限位**当物理基准，量出"一次失败的快速移动到底走了多少"。

## 要回答的问题

控制器无位置反馈，`real_pos` 是脉冲自述。所以之前的结论只能说到
"计数器与指令不符"，**说不到"滑块停在 1435 mm"**（见 `TravelShortfallError` 的证据边界）。
本脚本用唯一的物理基准（负限位开关）把物理真相量出来。

## 原理：回零的"触限耗时"是物理距离的代理，且不读计数器

回零走独立路径、**不受软限位约束**（实测 LIMIT_N 每次回零置起两次）。从**同一个起点**出发，
"下发回零 → LIMIT_N 首次置起"的耗时只取决于**从起点到硬挡的物理距离**：

    物理距离 ≈ 触限耗时 × 回零档速度(90 mm/s)   —— 不依赖 real_pos

于是：

| 步骤 | 物理距离（限位法） | 计数器读数 |
| --- | --- | --- |
| ① 从原点做一次**慢速**移动（10 mm/s，已验证这段可靠）到 D | 应为 D | real_pos = D |
| ② 快速移动回到 0（150 mm/s 巡检档）→ **可能失败** | 待测 ΔL_phys | real_pos 最终值 = c |
| ③ 从该处回零 | 用 ② 三次触碰的间隔反推 | — |

**判据**：把 ② 之后首次触限的耗时 `t1` 与 ① 之后（= 从原点出发）的基准耗时 `t0` 比。
参考在硬挡上，所以 `t1 − t0` 直接给出**②之后滑块离原点的物理距离**。再与计数器 `c` 对照：

- `t1 ≈ t0` ⇒ 滑块**真的回到了原点**，而计数器报了 `c` ⇒ **计数器撒谎**
- `t1 − t0 ≈ c / 90mm/s` ⇒ 滑块**物理上确实停在 c 附近** ⇒ 计数器诚实，是"移动没做完"

## 免责

单次耗时差的分辨率约 0.1 s × 90 mm/s ≈ **9 mm**；只报数量级结论。
触限两次的间隔（v1→v2）反映压入量与开关滞回，单独列出供参考，不作判据。
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
LIMIT_N = 0x0010
HOME_DONE = 0x0040
HOMING = 0x0080
RUNNING = 0x0001
HOME_V = M1.y.home_speed            # 90 mm/s：触限耗时的换算基准
D = 4442.0                          # 快速移动的指令距离（换层行程，最容易出问题）
ATTEMPTS = int(sys.argv[sys.argv.index("--attempts") + 1]) if "--attempts" in sys.argv else 4
BUF = (ctypes.c_ubyte * ctypes.sizeof(MachineStatusStruct))()


def raw() -> tuple[int, float]:
    client._lib.FMC4030_Get_Machine_Status(client.id, BUF)
    s = MachineStatusStruct.from_buffer_copy(bytes(BUF))
    return int(s.axisStatus[AXIS]), float(s.realPos[AXIS])


def deviation(exc: TravelShortfallError) -> float:
    """偏差字段名跨版本兼容：新代码叫 delta，旧代码叫 shortfall。"""
    return float(getattr(exc, "delta", None) or getattr(exc, "shortfall", 0.0))
def home_trace(label: str) -> dict:
    """回零一次，记录三次触限时刻。耗时是物理距离的代理（不读计数器）。"""
    t0 = time.monotonic()
    client.home_axis(AXIS)
    trips: list[float] = []
    prev_limit = False
    while time.monotonic() - t0 < 90:
        flags, _ = raw()
        on = bool(flags & LIMIT_N)
        if on and not prev_limit:
            trips.append(time.monotonic() - t0)
        prev_limit = on
        if trips and (flags & HOME_DONE) and not (flags & HOMING):
            break
        time.sleep(0.002)           # 2ms 采样 ⇒ 0.18mm 分辨率
    landed = client.current_yz()[0]
    t1 = trips[0] if trips else float("nan")
    t2 = trips[1] if len(trips) > 1 else float("nan")
    print(f"    {label}: 触限 {['%.3f' % t for t in trips]}  落点计数={landed:.3f}", flush=True)
    return {"trips": trips, "t1": t1, "t2": t2, "landed": landed}


def slow_goto(target: float) -> float:
    """慢速（点动档 10 mm/s）绝对定位，返回计数器读数。已验证这一段可靠。"""
    while abs(client.current_yz()[0] - target) > 1.0:
        here = client.current_yz()[0]
        step = max(-400.0, min(400.0, target - here))
        client.jog(AXIS, step)
        time.sleep(0.6)
        while raw()[0] & RUNNING:
            time.sleep(0.05)
    return client.current_yz()[0]


client = Fmc4030.connect(lib=load_library(), device_id=DEVICE_ID,
                         ip=CONTROLLER_IP, port=CONTROLLER_PORT)
try:
    st = client.get_status()
    if not all(st.axes[a.index].homed for a in M1.axes):
        raise SystemExit("✋ 未回零——先 home 再来")
    print(f"巡检档 {M1.y.travel_speed} · 点动档 {M1.y.jog_speed} · 回零档 {HOME_V} mm/s"
          f"（触限耗时的换算基准）", flush=True)
    print(f"快速移动指令距离 D = {D:.0f} mm，重复 {ATTEMPTS} 次\n", flush=True)

    rows = []
    for i in range(1, ATTEMPTS + 1):
        print(f"===== 第 {i}/{ATTEMPTS} 次 =====", flush=True)

        # ① 回零到硬挡（起点固定），并记"从原点出发"的基准触限耗时
        print("  ① 建立起点", flush=True)
        base = home_trace("基准回零")
        t0 = base["t1"]

        # ② 慢速走到 D（可靠段），为快速移动准备一个已知起点
        got = slow_goto(D)
        print(f"  ② 慢速到 D：计数器={got:.1f}", flush=True)

        # ③ 快速回 0（巡检档）——这一步可能失败
        t_start = time.monotonic()
        short = None
        try:
            client.goto(0.0, -21.2)
            ok = True
        except TravelShortfallError as e:
            ok = False
            short = e
        dt = time.monotonic() - t_start
        counter = client.current_yz()[0]
        print(f"  ③ 快速回 0：{'成功' if ok else '未完成'}  耗时 {dt:.1f} s  "
              f"计数器={counter:.1f}" + (f"  偏差 {deviation(short):+.1f}" if short else ""), flush=True)

        # ④ 从当前位置回零：首次触限耗时 ⇒ 物理距离（参考在硬挡上）
        print("  ④ 回零并测触限", flush=True)
        after = home_trace("复测回零")
        dt_trip = after["t1"] - t0
        phys_mm = dt_trip * HOME_V

        print(f"  ⇒ 触限耗时差 {dt_trip:+.3f} s ≈ 物理距离 {phys_mm:+.1f} mm"
              f"；计数器说 {counter:.1f} mm", flush=True)
        if not ok:
            if abs(phys_mm) < 30:
                verdict = "计数器撒谎：滑块其实回到了原点附近"
            elif abs(phys_mm - counter) < max(50.0, abs(counter) * 0.15):
                verdict = "计数器诚实：滑块物理上确实停在计数器附近 ⇒ 是『移动没做完』"
            else:
                verdict = f"两者都对不上（物理 {phys_mm:.0f} vs 计数 {counter:.0f}）"
            print(f"  ⇒ 判定：{verdict}", flush=True)
        rows.append({"i": i, "ok": ok, "dt": dt, "counter": counter,
                     "phys": phys_mm, "delta": deviation(short) if short else 0.0})
        print(flush=True)

    print("=" * 78, flush=True)
    print(f"{'次':>3}{'结果':>8}{'耗时s':>8}{'计数器':>11}{'限位法物理':>12}{'指令-计数':>12}")
    for r in rows:
        print(f"{r['i']:>3}{'成功' if r['ok'] else '未完成':>8}{r['dt']:>8.1f}"
              f"{r['counter']:>11.1f}{r['phys']:>12.1f}{r['delta']:>12.1f}")
    bad = [r for r in rows if not r["ok"]]
    print("=" * 78)
    if bad:
        lying = [r for r in bad if abs(r["phys"]) < 30]
        honest = [r for r in bad if abs(r["phys"] - r["counter"]) < max(50.0, abs(r["counter"]) * 0.15)]
        print(f"失败 {len(bad)} 次：计数器撒谎 {len(lying)} 次、计数器诚实 {len(honest)} 次、"
              f"无法判定 {len(bad) - len(lying) - len(honest)} 次")
    else:
        print("本轮没复现失败——把 --attempts 加大再试")
    print("\n注意：单次分辨率 ≈ 9 mm（0.1 s × 90 mm/s），只看数量级。", flush=True)

    print("\n收尾回零…", flush=True)
    client.home_all(timeout_s=150)
    y, z = client.current_yz()
    print(f"落点 Y={y:.3f} Z={z:.3f}", flush=True)
finally:
    client.close()
