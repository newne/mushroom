#!/usr/bin/env python3
"""降速 A/B：静默短停到底在哪个速度档上出现？

## 背景（2026-09-13 实测）

| 速度 | 距离 | 结果 |
| --- | --- | --- |
| 150 mm/s（当前巡检档） | 4442 mm | 约 50% 的趟次短停（偏差最高 +3006 mm） |
| 150 mm/s | 374 mm（站间框距） | 整轮 46 次移动里 5 次短停（≈11%） |
| **10 mm/s（点动档）** | 0→4442 全程、两个方向 | **整段通过，零短停** |

⇒ 不是机械卡阻（否则低速也该卡），是**速度相关**：怀疑驱动器在 158 kHz 附近
（150 mm/s × 1052.632 脉冲/mm）收不下脉冲而失步，控制器计数器却照常报"到位"。

## 本脚本要回答

1. **能复现吗**：150 档在 4442 mm 上再跑若干趟，短停率是否仍显著。
2. **降速能不能解决**：90、50 两档同样跑，短停是否消失。
3. **短程（374 mm）在各档是否也干净**——巡检主要走这个距离。

## 安全与口径

- 每趟前若上一趟失败，先 `home_all()` 把坐标系拉回硬基准再继续
- 判定完全交给 `goto` 自带的到位校验（`TravelShortfallError`），脚本不另设判据
- 结束回零并停在原点；全程只在 Y 行程内、两端留 50 mm 余量
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

PPMM = 100_000 / 95.0                 # 脉冲/mm（实测 div/lead）
LONG = 4442.0                         # 换层行程
SHORT = 374.33                        # 站间框距
SPEEDS = (150.0, 90.0, 50.0)          # 当前档、回零档、最保守
N_LONG = int(sys.argv[sys.argv.index("--long") + 1]) if "--long" in sys.argv else 6
N_SHORT = int(sys.argv[sys.argv.index("--short") + 1]) if "--short" in sys.argv else 12


def run_segment(target: float, speed: float) -> tuple[bool, float, float, str]:
    """一段绝对移动，返回 (成功, 耗时, 位置偏差 mm, 说明)。显式给速度 ⇒ 原样下发。"""
    t0 = time.monotonic()
    try:
        client.goto(target, 21.2, speed=speed, acc=speed * 10.0)
        return True, time.monotonic() - t0, 0.0, ""
    except TravelShortfallError as e:
        return False, time.monotonic() - t0, e.shortfall, str(e)


def bench(label: str, distance: float, trips: int, speed: float) -> dict:
    print(f"\n=== {label}：0 ↔ {distance:.0f} mm × {trips} 趟 @ {speed:.0f} mm/s "
          f"= {speed * PPMM / 1000:.0f} kHz ===", flush=True)
    fails, oks, times = [], 0, []
    for i in range(1, trips + 1):
        for target, d in ((distance, "→"), (0.0, "←")):
            ok, dt, dev, why = run_segment(target, speed)
            if ok:
                oks += 1
                times.append(dt)
            else:
                fails.append((i, d, dev, dt))
                print(f"  第{i}趟 {d} 短停：偏差 {dev:+9.1f} mm（{dt:5.1f}s）", flush=True)
                client.home_all(timeout_s=150)      # 拉回硬基准再继续
    total = trips * 2
    rate = len(fails) / total * 100
    avg = sum(times) / len(times) if times else float("nan")
    print(f"  → 成功 {oks}/{total}，短停 {len(fails)}（{rate:.0f}%），"
          f"成功段均 {avg:.2f} s", flush=True)
    return {"label": label, "speed": speed, "total": total, "fails": fails,
            "rate": rate, "avg_s": avg}


client = Fmc4030.connect(lib=load_library(), device_id=DEVICE_ID,
                         ip=CONTROLLER_IP, port=CONTROLLER_PORT)
results = []
try:
    st = client.get_status()
    if not all(st.axes[a.index].homed for a in M1.axes):
        raise SystemExit("✋ 未回零——先 home 再来")
    print(f"Y 行程 0–{M1.y.travel_max:.0f} mm；脉冲当量 {PPMM:.3f} 脉冲/mm；"
          f"控制器上界 200 kHz ⇒ {200_000 / PPMM:.0f} mm/s", flush=True)
    client.home_all(timeout_s=150)

    for v in SPEEDS:
        results.append(bench("长程换层", LONG, N_LONG, v))
        client.home_all(timeout_s=150)
        results.append(bench("站间框距", SHORT, N_SHORT, v))
        client.home_all(timeout_s=150)

    print(f"\n{'=' * 74}")
    print(f"{'距离':<12}{'速度':>8}{'kHz':>7}{'段数':>6}{'短停':>6}{'短停率':>8}{'成功段均':>10}")
    for r in results:
        print(f"{r['label']:<12}{r['speed']:>7.0f}{r['speed'] * PPMM / 1000:>7.0f}"
              f"{r['total']:>6}{len(r['fails']):>6}{r['rate']:>7.0f}%{r['avg_s']:>9.2f}s")
    print(f"{'=' * 74}")
    clean = [r for r in results if not r["fails"]]
    if clean:
        best = max(clean, key=lambda r: r["speed"])
        print(f"\n⇒ 零短停的最高档：**{best['speed']:.0f} mm/s**"
              f"（{best['speed'] * PPMM / 1000:.0f} kHz）")
        print(f"   巡检档若定在此值，整轮时长按纯运动估算："
              f"Y 空程 20.8 m / {best['speed']:.0f} mm/s ≈ {20775 / best['speed'] / 60:.1f} min 运动")
    else:
        print("\n⇒ 三档都有短停：问题不在速度，转查机械/驱动（皮带张力、联轴器、驱动器电流）")
    print(flush=True)

    print("收尾回零…", flush=True)
    client.home_all(timeout_s=150)
    y, z = client.current_yz()
    print(f"落点 Y={y:.3f} Z={z:.3f}", flush=True)
finally:
    client.close()
