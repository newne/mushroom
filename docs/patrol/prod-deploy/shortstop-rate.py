#!/usr/bin/env python3
"""量化短停发生率：**巡检实际用的距离** vs **换层用的全行程**。

## 为什么要分距离量

实测短停出现在 4442 mm 的长程上（偏差 3006 / 991 / 2089 mm）。而一次巡检里，
绝大多数站间移动只有 **374 mm**（一个框距），全行程只在"第 5 层走完换回第 1 层"
时发生 2 次/轮（约 4.4 m）。所以"长程会不会短停"与"巡检会不会受影响"是两个问题，
必须分别量。

判定口径：`goto` 现在自带到位校验（`TravelShortfallError`），脚本只负责计数。

安全：全程绝对 `goto`，两端留 50 mm 余量，不贴软限位；结束回零。
"""

from __future__ import annotations

import os
import sys
import time

os.environ.setdefault("FMC4030_LIB_PATH", "/opt/mushroom-patrol/lib/libFMC4030_2009_1.so")
sys.path.insert(0, "/opt/mushroom-patrol/src")

from patrol.fmc import Fmc4030, TravelShortfallError  # noqa: E402
from patrol.fmc.loader import load_library  # noqa: E402
from patrol.motion_profile import CONTROLLER_IP, CONTROLLER_PORT, DEVICE_ID, M1  # noqa: E402

SHORT = 374.33          # 一个框距（站间实际空程）
FULL = 4442.0           # 第 5 层 → 第 1 层的换层行程
N_SHORT = int(sys.argv[sys.argv.index("--n-short") + 1]) if "--n-short" in sys.argv else 20
N_FULL = int(sys.argv[sys.argv.index("--n-full") + 1]) if "--n-full" in sys.argv else 10


def exercise(client, distance: float, trips: int, label: str) -> dict:
    """在 0 ↔ distance 之间往返 trips 次，统计短停次数与耗时。"""
    print(f"\n=== {label}：0 ↔ {distance:.0f} mm × {trips} 趟 ===", flush=True)
    shorts, ok, times = [], 0, []
    for i in range(1, trips + 1):
        for target, direction in ((distance, "→"), (0.0, "←")):
            t0 = time.monotonic()
            try:
                client.goto(target, 21.2)
                ok += 1
                times.append(time.monotonic() - t0)
            except TravelShortfallError as e:
                shorts.append((i, direction, e.shortfall, round(time.monotonic() - t0, 1)))
                print(f"  第{i}趟 {direction} 短停：差 {e.shortfall:+.1f} mm"
                      f"（{time.monotonic() - t0:.1f}s）", flush=True)
                # 短停后先把坐标系拉回已知点再继续（回零是唯一硬基准）
                client.home_all(timeout_s=150)
    rate = len(shorts) / (trips * 2) * 100
    avg = sum(times) / len(times) if times else float("nan")
    print(f"  → 成功 {ok} 段 / 短停 {len(shorts)} 段（{rate:.1f}%），"
          f"单段均 {avg:.2f} s", flush=True)
    return {"label": label, "distance": distance, "trips": trips * 2,
            "shorts": shorts, "rate": rate, "avg_s": avg}


client = Fmc4030.connect(lib=load_library(), device_id=DEVICE_ID,
                         ip=CONTROLLER_IP, port=CONTROLLER_PORT)
try:
    st = client.get_status()
    if not all(st.axes[a.index].homed for a in M1.axes):
        raise SystemExit("✋ 未回零——先 home 再来")

    print(f"当前速度档 {st.real_speed[1]:.1f} mm/s（静止时应为 0）", flush=True)
    client.home_all(timeout_s=150)

    r_short = exercise(client, SHORT, N_SHORT, "巡检距离（站间空程）")
    client.home_all(timeout_s=150)
    r_full = exercise(client, FULL, N_FULL, "换层距离（全行程）")

    print(f"\n{'=' * 62}")
    print(f"{'距离':<22}{'段数':>6}{'短停':>6}{'发生率':>9}{'单段均耗时':>12}")
    for r in (r_short, r_full):
        print(f"{r['label']:<22}{r['trips']:>6}{len(r['shorts']):>6}"
              f"{r['rate']:>8.1f}%{r['avg_s']:>11.2f}s")
    print(f"{'=' * 62}")
    print("\n参照：一个巡检轮次 = 60 站 + 约 59 段框距(374mm) + 2 段换层(4442mm) + 回零")
    est = (59 * r_short["rate"] + 2 * r_full["rate"]) / 61
    print(f"按上表发生率粗估：一轮里约 {est:.1f}% 的移动会短停"
          f"（≈ 每 {100 / est if est else float('inf'):.1f} 轮撞到一次）")

    client.home_all(timeout_s=150)
    y, z = client.current_yz()
    print(f"\n收尾回零落点 Y={y:.3f} Z={z:.3f}", flush=True)
finally:
    client.close()
