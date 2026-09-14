#!/usr/bin/env python3
"""丢步判定（限位基准法，非侵入式）：用**回零自身的触限时刻**当物理基准。

## 为什么不用 real_pos

控制器位置计数器只数**发出去的脉冲**：丢步时它照样报"到位"。所以"回到起点偏差
0.000 mm"这类读数**证明不了**没丢步——它是自证。要抓静默丢步，必须找一个与脉冲
计数无关的物理量。

## 本机为什么改用手动压限位之外的办法

`probe-home-limit.py` 实测：Y 的控制器软限位是 `[0, 4495]`，**原点就是 0**，
所以 `jog` 往负方向在 Y=0 就被软限位拦住（够不着开关）；但**回零走独立路径**，
`LIMIT_N` 在每次回零中置起两次（首次压上 + 脱落回退后再压），实测触限点 Y=-3.629。
⇒ 硬限位是够得着的，只是不能用手动 jog 去压。

于是基准换成**回零的触限时刻**：

    触限耗时 = （下发回零 → LIMIT_N 首次置起）的秒数

它与脉冲计数无关，直接反映"从起点到硬挡的物理距离"（回零段 90 mm/s，测得 2.317 s
对应 ~207 mm）。步骤：

1. 基准轮：先回零到原点，再离开固定距离 D，回零，记触限耗时 `t0`；
2. 扰动：巡检档 150 mm/s 做 N 次**全行程**往复（步数最多、最接近真实巡检）；
3. 复测：再离开同样的 D、回零，记触限耗时 `t_i`；
4. 判定：`|t_i − t0| × 90 mm/s > 0.2 mm` 即丢步（0.2 mm ⇒ 2.2 ms 的时间分辨率）。

每次触限都是**同方向、同速度、同一硬挡**，所以两次之间可比；差值里剩下的就是丢步。

## 安全

- 只在已回零前提下跑；行程内无人、无遮挡
- 全部动作都走 `patrol.fmc` 的既有路径（回零/两段速 goto），不新造运动指令
- 结束时回零并把机构停在原点
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
LIMIT_N = 0x0010
THRESHOLD_MM = 0.2
CYCLES = int(sys.argv[sys.argv.index("--cycles") + 1]) if "--cycles" in sys.argv else 4
D = float(sys.argv[sys.argv.index("--leg") + 1]) if "--leg" in sys.argv else 2000.0
# 测量腿长必须与受扰动的行程**同量级**：被扰的是 4492 mm 的快走，若只测 200 mm 的
# 寻零腿，千分之一的丢步率就完全测不出来。取 2000 mm（全行程的 45%）：
# 2 ms 时间分辨率 × 90 mm/s = 0.18 mm ⇒ 灵敏度 9e-5。
BUF = (ctypes.c_ubyte * ctypes.sizeof(MachineStatusStruct))()


def raw(axis: int) -> tuple[int, float]:
    client._lib.FMC4030_Get_Machine_Status(client.id, BUF)
    s = MachineStatusStruct.from_buffer_copy(bytes(BUF))
    return int(s.axisStatus[axis]), float(s.realPos[axis])


def pos() -> float:
    return client.current_yz()[0]


def home_and_time_trip(label: str) -> tuple[float, float]:
    """回零一次，返回 (触限耗时 s, 回零落点 Y)。触限时刻以 LIMIT_N 首次置起为准。"""
    t0 = time.monotonic()
    client.home_axis(AXIS)
    trip_t = None
    while time.monotonic() - t0 < 90:
        flags, _pos = raw(AXIS)
        if trip_t is None and (flags & LIMIT_N):
            trip_t = time.monotonic() - t0
        # 回零完成：HOME_DONE 置起且不在 HOMING
        if trip_t is not None and (flags & 0x0040) and not (flags & 0x0080):
            break
        time.sleep(0.002)       # 2ms 轮询 ⇒ 0.18mm 位置分辨率（90mm/s）
    y = client.current_yz()[0]
    print(f"    {label}: 触限 {trip_t:.3f} s  落点 Y={y:.3f}", flush=True)
    return (trip_t if trip_t is not None else float("nan")), y


def leave_and_home(label: str) -> tuple[float, float]:
    """离开固定距离 D，再回零并测触限耗时（每次前都先回零，保证起点一致）。"""
    client.home_all(timeout_s=150)          # 起点固定在硬挡
    client.goto(D, -21.2)                    # 两段速走到 D
    p = pos()                                # 每次都要确认真的到位了
    if abs(p - D) > 0.5:
        print(f"    ! 起点未到位：要求 Y={D:.1f}，实到 Y={p:.3f}", flush=True)
    return home_and_time_trip(label)


client = Fmc4030.connect(lib=load_library(), device_id=DEVICE_ID,
                         ip=CONTROLLER_IP, port=CONTROLLER_PORT)
spec = M1.by_index(AXIS)
try:
    st = client.get_status()
    if not all(st.axes[a.index].homed for a in M1.axes):
        raise SystemExit("✋ 未回零——先 home 再来")
    print(f"Y：巡检档 {spec.travel_speed} mm/s · 回零档 {spec.home_speed} mm/s · "
          f"全行程 {spec.travel_max - spec.travel_min:.0f} mm", flush=True)
    print(f"基准轮：离开 {D:.0f} mm 后回零，测触限耗时\n", flush=True)

    trip0, land0 = leave_and_home("基准 trip_0")

    span = spec.travel_max - spec.travel_min
    far = span - 50.0            # 留 50mm 余量，避免贴软限位上沿
    print(f"\n扰动：巡检档全行程往复 {CYCLES} 次（0 ↔ {far:.0f} mm）", flush=True)
    for i in range(1, CYCLES + 1):
        # 每段都记录"下达前/到位后"的实际读数：上一次实测里扰动**静默退化**成了
        # 0.6 s 的空动作（第 1 次真的走了 30.3 s，第 2–4 次没动），而脚本看不出来。
        p0 = pos()
        t0 = time.monotonic()
        client.goto(far, -21.2)
        fwd, p1 = time.monotonic() - t0, pos()
        t0 = time.monotonic()
        client.goto(0.0, 0.0)
        back, p2 = time.monotonic() - t0, pos()
        moved = abs(p1 - p0) + abs(p2 - p1)
        flag = "" if moved > span else "   ⚠️ 未走满（这次扰动无效）"
        print(f"  第{i}次  →{fwd:5.1f}s Y {p0:7.1f}→{p1:7.1f}  "
              f"←{back:5.1f}s →{p2:7.1f}  共 {moved:6.0f} mm{flag}", flush=True)

    print("\n复测：", flush=True)
    results = [leave_and_home(f"trip_{i}") for i in range(1, 3)]

    print(f"\n{'=' * 58}")
    print(f"基准触限耗时 t0 = {trip0:.3f} s  (≈ {trip0 * spec.home_speed:.1f} mm 行程)")
    worst_mm = 0.0
    for i, (t, land) in enumerate(results, 1):
        d_mm = abs(t - trip0) * spec.home_speed
        worst_mm = max(worst_mm, d_mm)
        print(f"复测 t{i} = {t:.3f} s  →  与基准差 {abs(t - trip0) * 1000:5.1f} ms "
              f"= {d_mm:5.3f} mm  落点 Y={land:.3f}")
    print(f"\n最大漂移 = {worst_mm:.3f} mm   （阈值 {THRESHOLD_MM} mm，1 整步 = 0.475 mm）")
    print("判定 = " + ("❌ 检出丢步" if worst_mm > THRESHOLD_MM else "✅ 未检出丢步"))
    print(f"{'=' * 58}", flush=True)
    print("⚠️ 眼/耳复核仍要做：异响、皮带拍打、到位后振动是否变大。", flush=True)

    client.home_all(timeout_s=150)
    y, z = client.current_yz()
    print(f"\n收尾：已回零，落点 Y={y:.3f} Z={z:.3f}", flush=True)
finally:
    client.close()
