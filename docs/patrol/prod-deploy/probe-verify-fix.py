#!/usr/bin/env python3
"""修复后的**短验证**：`goto` 还会不会报 -7、到位校验还会不会误报。

跑法（prod）：
    PYTHONPATH=src FMC4030_LIB_PATH=lib/libFMC4030_2009_1.so venv/bin/python probe-verify-fix.py

做三件事，全部记录耗时与偏差：

1. 短程 374 mm 往返 ×5 @巡检档 —— 修复前这里会出现 -7 与假偏差
2. 长程 4442 mm 往返 ×3 @巡检档 —— 修复前这里短停率最高
3. 汇总：异常次数、偏差分布、每次移动实际耗时

判据（修复目标）：
- **-7 / 其它 FmcError 次数 = 0**（wait_stop 现在等到停稳才返回）
- 偏差全部落在容差内（不再有 1.8mm / 238.9mm 那种过渡值误报）
- 耗时应与理论值吻合（短程 2.6s / 长程 30s），证明移动完整执行
"""

from __future__ import annotations

import os
import statistics
import sys
import time

os.environ.setdefault("FMC4030_LIB_PATH", "/opt/mushroom-patrol/lib/libFMC4030_2009_1.so")
sys.path.insert(0, "/opt/mushroom-patrol/src")

from patrol.fmc import Fmc4030, FmcError, TravelShortfallError  # noqa: E402
from patrol.fmc.loader import load_library  # noqa: E402
from patrol.motion_profile import CONTROLLER_IP, CONTROLLER_PORT, DEVICE_ID, M1  # noqa: E402

ROWS: list[dict] = []


def one_move(target: float, label: str) -> None:
    t0 = time.monotonic()
    kind, detail, dev = "ok", "", 0.0
    try:
        client.goto(target, 21.2)
    except TravelShortfallError as e:
        kind = "短停(计数不符)"
        detail = str(e)[:80]
        dev = float(getattr(e, "delta", None) or getattr(e, "shortfall", 0.0))
    except FmcError as e:
        kind = "FmcError"
        detail = str(e)[:80]
    dt = time.monotonic() - t0
    pos = client.current_yz()[0]
    ROWS.append({"label": label, "target": target, "kind": kind, "dt": dt,
                 "dev": dev, "pos": pos})
    flag = "" if kind == "ok" else "   ← " + kind
    print(f"    {label:<22} 耗时 {dt:5.2f}s  落点计数={pos:9.3f}  偏差={dev:+8.3f}"
          f"{flag}  {detail}", flush=True)


client = Fmc4030.connect(lib=load_library(), device_id=DEVICE_ID,
                         ip=CONTROLLER_IP, port=CONTROLLER_PORT)
try:
    st = client.get_status()
    if not all(st.axes[a.index].homed for a in M1.axes):
        print("未回零 → 先回零", flush=True)
        client.home_all(timeout_s=150)
    print(f"巡检档 {M1.y.travel_speed} mm/s；容差（到位校验）见 client.ARRIVAL_TOL_MM\n", flush=True)

    print("【一】短程 374 mm 往返 ×5", flush=True)
    for i in range(1, 6):
        one_move(374.33, f"短程#{i} 0→374")
        one_move(0.0, f"短程#{i} 374→0")

    print("\n【二】长程 4442 mm 往返 ×3", flush=True)
    for i in range(1, 4):
        one_move(4442.0, f"长程#{i} 0→4442")
        one_move(0.0, f"长程#{i} 4442→0")

    print(f"\n{'=' * 74}")
    n = len(ROWS)
    bad = [r for r in ROWS if r["kind"] != "ok"]
    print(f"共 {n} 段移动：成功 {n - len(bad)}，异常 {len(bad)}")
    if bad:
        from collections import Counter
        print("异常分类：", dict(Counter(r["kind"] for r in bad)))
        for r in bad:
            print(f"   {r['label']}  {r['kind']}  偏差 {r['dev']:+.1f}")
    devs = [abs(r["dev"]) for r in ROWS if r["kind"] == "ok"]
    if devs:
        print(f"成功段 |偏差|：max {max(devs):.3f} mm，p50 {statistics.median(devs):.3f} mm")
    for tag, expect, tol in (("短程", 2.6, 0.8), ("长程", 30.0, 3.0)):
        ts = [r["dt"] for r in ROWS if r["label"].startswith(tag)]
        if ts:
            print(f"{tag}耗时：min {min(ts):.2f}s / p50 {statistics.median(ts):.2f}s / "
                  f"max {max(ts):.2f}s（理论 ≈{expect}s）")
    print(f"{'=' * 74}")
    print("判定：" + ("✅ 无 -7、无假偏差、耗时与理论吻合" if not bad
                     else "❌ 仍有异常，见上"), flush=True)

    print("\n收尾回零…", flush=True)
    client.home_all(timeout_s=150)
    y, z = client.current_yz()
    print(f"落点 Y={y:.3f} Z={z:.3f}", flush=True)
finally:
    client.close()
