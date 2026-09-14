#!/usr/bin/env python3
"""巡检档上机验证：新速度（Y 150 mm/s）在同一框距上反复走，给出**实测耗时**。

    python3 travel-speed-check.py [--dist 374.33] [--cycles 3] [--no-return]

为什么需要它：`motion_profile` 的 150 mm/s 是从"控制器脉冲上界 + 改造前现场值"推出的，
**没有在这台机器上跑过**。控制器发得出 158 kHz 不等于驱动器收得下；收不下会**静默丢步**
（位置计数器只数发出去的脉冲，`real_pos` 照样报到位）。所以这一步是人机同场验证：

  1. 本脚本给**实测耗时**（对照模型的理论值，偏差大就说明没跑满速度）；
  2. 眼/耳判断：有无异响、抖动、皮带拍打；
  3. 丢步判定用「限位基准法」（见 `motor-command-review.md` §2.2），必须人工做。

安全前提：**只在已回零的前提下跑**（原点不可信时绝对坐标会整体偏移，脚本硬拒绝）。
"""

from __future__ import annotations

import os
import sys
import time

os.environ.setdefault("FMC4030_LIB_PATH", "/opt/mushroom-patrol/lib/libFMC4030_2009_1.so")
sys.path.insert(0, "/opt/mushroom-patrol/src")

from patrol.fmc import Fmc4030, HomeTimeoutError  # noqa: E402
from patrol.fmc.loader import load_library  # noqa: E402
from patrol.motion_profile import M1, CONTROLLER_IP, CONTROLLER_PORT, DEVICE_ID  # noqa: E402

DIST = 374.33          # 一个框距（12 框均分 4492 mm），即站间实际空程
CYCLES = 3
RETURN = "--no-return" not in sys.argv
if "--dist" in sys.argv:
    DIST = float(sys.argv[sys.argv.index("--dist") + 1])
if "--cycles" in sys.argv:
    CYCLES = int(sys.argv[sys.argv.index("--cycles") + 1])

PPMM = 100_000 / 95    # 细分/导程，实测


def trapezoid(distance: float, speed: float, acc: float) -> float:
    ramp = speed * speed / acc
    if distance >= ramp:
        return distance / speed + speed / acc
    return 2.0 * (distance / acc) ** 0.5


client = Fmc4030.connect(lib=load_library(), device_id=DEVICE_ID,
                         ip=CONTROLLER_IP, port=CONTROLLER_PORT)
try:
    st = client.get_status()
    print(f"位置 Y={st.real_pos[1]:.3f} Z={st.real_pos[2]:.3f}  模式={st.run_mode}")
    if not all(st.axes[a.index].homed for a in M1.axes):
        raise SystemExit("✋ 未回零（原点不可信）——先用 `python3 -m patrol.debug` 跑 `home` 再来")
    issues = client.soft_limit_issues()
    if issues:
        print("⚠️ 软限位体检未通过，长行程可能被控制器静默截断：")
        for it in issues:
            print("   -", it)
        raise SystemExit("先按 ADR-0008 整定软限位再跑速度验证")

    y0, z0 = client.current_yz()
    v = M1.y.travel_speed
    print(f"轴参数：Y 巡检档 {v} mm/s / acc {M1.y.travel_acc}  "
          f"= {v * PPMM / 1000:.0f} kHz（控制器上限 200 kHz）")
    print(f"行程 {DIST} mm，往复 {CYCLES} 次；理论单程 "
          f"{trapezoid(DIST, v, M1.y.travel_acc):.2f} s（不含接近段与到位确认）\n")

    total = 0.0
    for i in range(1, CYCLES + 1):
        for direction, target in (("→", y0 + DIST), ("←", y0)):
            t0 = time.monotonic()
            client.goto(target, z0)
            dt = time.monotonic() - t0
            total += dt
            print(f"  第{i}次 {direction} Y={target:8.2f}  实测 {dt:6.2f} s")
    print(f"\n合计 {total:.1f} s / {CYCLES * 2} 段（平均 {total / (CYCLES * 2):.2f} s/段）")

    end = client.current_yz()
    print(f"回到起点偏差：ΔY={end[0] - y0:+.3f} mm  ΔZ={end[1] - z0:+.3f} mm"
          f"   ← 注意：这是**指令计数**，丢步它看不出来（见文件头注释）")
    if RETURN and abs(end[0] - y0) > 0.01:
        client.goto(y0, z0)
        print("已回起点坐标")
except HomeTimeoutError as e:
    print(f"回零超时（原点不可信）：{e}")
    raise SystemExit(2)
finally:
    client.close()
