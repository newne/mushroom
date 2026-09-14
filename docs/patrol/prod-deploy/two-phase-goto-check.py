#!/usr/bin/env python3
"""两段速 ``client.goto()`` 上机验证：走一个框距 374.33 mm，看两段耗时与落点。

为什么单独验它
--------------
M1 巡检路径（``orchestrator.StationCapture`` -> ``client.goto()``）用的是**两段**运动：
先全速走空程到「距目标 ``approach_offset``（5 mm）」的点，等停，再切接近档走完最后 5 mm。
而截止 2026-09-13，真机上只跑过 M0 的单段 ``goto_2axis``。两段速比单段多出**一次同轴指令
接续**（空程段刚停就续发接近段），正是「起转窗口读到上一条指令的残值」与「同轴未完成
指令被 SDK 静默丢弃」最容易发作的地方，所以要在真机上单独确认一次。

测量手法（零并发、零打补丁）
----------------------------
把 SDK 动态库**包一层记录器**：``FMC4030_Line_2Axis`` 每次被调用就记下「时刻 + 全部实参」。

    t1 = 巡检段指令下发    t2 = 接近段指令下发    t3 = goto() 返回
    巡检段耗时 ~= t2 - t1   接近段耗时 ~= t3 - t2   总耗时 = t3 - t1

这比「事后从轨迹里猜换挡点」硬得多：**速度实参直接证明两段用的是不是正确的档**
（预期 150 / 30 mm/s），而位置实参直接给出换挡点是不是 ``dist - 5``。

安全性
------
默认只读演练，必须显式 ``--go`` 才动作；动作前查两轴是否静止、回零归位，异常即急停。

用法::

    python3 two-phase-goto-check.py              # 只读演练（不动机构）
    python3 two-phase-goto-check.py --go         # 真跑：回零 -> 去 -> 回，各一趟
    python3 two-phase-goto-check.py --go --cycles 3 --dist 374.33 --z -21.2
"""

from __future__ import annotations

import argparse
import math
import os
import sys
import time

os.environ.setdefault("FMC4030_LIB_PATH", "/opt/mushroom-patrol/lib/libFMC4030_2009_1.so")
sys.path.insert(0, "/opt/mushroom-patrol/src")

from patrol.fmc import Fmc4030, FmcError, MotionTimeoutError
from patrol.fmc.geometry import composite_limits
from patrol.fmc.loader import load_library
from patrol.motion_profile import CONTROLLER_IP, CONTROLLER_PORT, DEVICE_ID, M1

BOX_PITCH = M1.y.travel_span / 12  # 4492 / 12 = 374.333 mm，一个框距


# ---------------------------------------------------------------- 记录器


class Line2AxisRecorder:
    """包在真库外面，记录每次 ``FMC4030_Line_2Axis`` 的时刻与实参，其余属性原样转发。"""

    def __init__(self, lib):
        self._lib = lib
        self.calls: list[tuple[float, tuple]] = []

    def __getattr__(self, name: str):
        attr = getattr(self._lib, name)
        if name != "FMC4030_Line_2Axis":
            return attr

        def wrapper(*args):
            self.calls.append((time.monotonic(), args))
            return attr(*args)

        return wrapper

    def drain(self) -> list[tuple[float, tuple]]:
        out, self.calls = self.calls, []
        return out


# ---------------------------------------------------------------- 理论值


def trapezoid_time(dist: float, v: float, a: float) -> float:
    """梯形/三角速度曲线走完 dist 所需时间（v 为目标速度，a 为加减速度）。"""
    d_acc = v * v / (2 * a)
    if 2 * d_acc >= dist:  # 加速段还没跑满就得减速 -> 三角形
        return 2 * math.sqrt(dist / a)
    return dist / v + v / a


def expected_speeds(dx: float) -> tuple[float, float]:
    """给定一段的位移，返回 (巡检档合成速度, 接近档合成速度)。"""
    return (
        composite_limits((dx, 0.0), M1.travel_limits)[0],
        composite_limits((dx, 0.0), M1.approach_limits)[0],
    )


# ---------------------------------------------------------------- 打印


def banner(title: str) -> None:
    print(f"\n{'=' * 78}\n{title}\n{'=' * 78}", flush=True)


def describe(client: Fmc4030, tag: str = "") -> None:
    st = client.get_status()
    pos = " ".join(f"{s.name}={st.real_pos[s.index]:8.3f}" for s in M1.axes)
    run = " ".join(
        f"{s.name}{'运行' if st.axes[s.index].running else '停'}"
        for s in M1.axes
    )
    print(f"  [{tag}] 位置 {pos} | {run} | 模式={st.run_mode}", flush=True)


def guard_idle(client: Fmc4030) -> None:
    st = client.get_status()
    moving = [s.name for s in M1.axes if st.axes[s.index].running]
    if moving:
        raise SystemExit(f"!! {'、'.join(moving)} 仍在运行，拒绝叠加指令；先等它停下或急停")


# ---------------------------------------------------------------- 一趟


def run_trip(
    client: Fmc4030,
    recorder: Line2AxisRecorder,
    target_y: float,
    *,
    z: float,
    offset: float,
    timeout_s: float,
) -> bool:
    """走一趟并打印两段分解。返回判定是否全部通过。"""
    start_y, start_z = client.current_yz()
    recorder.drain()

    t0 = time.monotonic()
    client.goto(target_y, z, timeout_s=timeout_s)
    t1 = time.monotonic()

    calls = recorder.drain()
    end_y, end_z = client.current_yz()
    dist = target_y - start_y

    print(f"\n  起点 Y={start_y:.3f} Z={start_z:.3f}  ->  目标 Y={target_y:.3f} Z={z:.3f}"
          f"   (ΔY={dist:+.3f} mm)", flush=True)
    print(f"  总耗时 {t1 - t0:.3f} s，goto 期间下发 {len(calls)} 条 Line_2Axis 指令", flush=True)

    if len(calls) != 2:
        print(f"  !! 预期 2 段，实际 {len(calls)} 段——"
              f"{'来向距离过短，整段退化为接近档单段' if len(calls) == 1 else '异常'}",
              flush=True)

    # 两段的期望档位（按实际段位移折算，与代码同源）
    leg_deltas = []
    prev = (start_y, start_z)
    for _, args in calls:
        leg_deltas.append((args[2] - prev[0], args[3] - prev[1]))
        prev = (args[2], args[3])
    deltas = leg_deltas
    exp = [
        composite_limits(d, M1.travel_limits)[0] if i == 0
        else composite_limits(d, M1.approach_limits)[0]
        for i, d in enumerate(deltas)
    ]

    ok = True
    stamps = [t for t, _ in calls] + [t1]
    labels = ["巡检段", "接近段"]
    for i, (_, args) in enumerate(calls):
        _dev, _mask, y, zz, v, a, _dec = args
        took = stamps[i + 1] - stamps[i]
        want_v = exp[i]
        hit = abs(v - want_v) < 0.01
        ok &= hit
        print(f"    {labels[i] if i < 2 else f'段{i + 1}'}: 目标 ({y:9.3f}, {zz:8.3f}) "
              f"speed={v:7.3f} acc={a:8.3f} | 耗时 {took:6.3f} s | "
              f"期望 speed={want_v:7.3f} {'OK' if hit else '**不符**'}", flush=True)

    # 换挡点 = 巡检段的落点（第一条指令的终点），应落在距目标 offset 处
    if len(calls) == 2:
        switch_y = calls[0][1][2]
        want_switch = target_y - (offset if dist > 0 else -offset)
        hit = abs(switch_y - want_switch) < 0.01
        ok &= hit
        print(f"    换挡点 Y={switch_y:.3f}（巡检段落点），期望 {want_switch:.3f} "
              f"{'OK' if hit else '**不符**'}", flush=True)

    # 落点误差（一个整步 0.475 mm）
    err_y = end_y - target_y
    err_z = end_z - z
    hit = abs(err_y) < 0.1 and abs(err_z) < 0.1
    ok &= hit
    print(f"    落点 Y={end_y:.3f} Z={end_z:.3f}，误差 ΔY={err_y:+.3f} ΔZ={err_z:+.3f} mm "
          f"{'OK' if hit else '**超差**'}（整步 0.475 mm）", flush=True)

    # 总耗时与理论值对照
    theory = 0.0
    for i, d in enumerate(deltas):
        seg_len = math.hypot(*d)
        lim_v = exp[i]
        lim_a = composite_limits(d, M1.travel_limits if i == 0 else M1.approach_limits)[1]
        if seg_len > 1e-6:
            theory += trapezoid_time(seg_len, lim_v, lim_a)
    ratio = (t1 - t0) / theory if theory else float("nan")
    hit = 0.8 <= ratio <= 1.8
    ok &= hit
    print(f"    总耗时/理论 = {t1 - t0:.3f}/{theory:.3f} = {ratio:.2f} 倍 "
          f"{'OK（含握手与轮询开销）' if hit else '**偏离过大**'}", flush=True)

    return ok


# ---------------------------------------------------------------- 主流程


def main() -> int:
    ap = argparse.ArgumentParser(description="两段速 goto() 上机验证")
    ap.add_argument("--go", action="store_true", help="真的动作（默认只读演练）")
    ap.add_argument("--dist", type=float, default=BOX_PITCH, help=f"单程距离 mm（默认一个框距 {BOX_PITCH:.2f}）")
    ap.add_argument("--z", type=float, default=0.0, help="运动期间的 Z 坐标（默认 0=顶端，纯 Y 位移最干净）")
    ap.add_argument("--cycles", type=int, default=2, help="往返趟数（默认 2 = 去 + 回）")
    ap.add_argument("--timeout", type=float, default=M1.travel_timeout, help="单段到位确认超时 s")
    args = ap.parse_args()

    lib = load_library()
    recorder = Line2AxisRecorder(lib)
    client = Fmc4030.connect(
        lib=recorder, device_id=DEVICE_ID, ip=CONTROLLER_IP, port=CONTROLLER_PORT
    )

    try:
        banner("一、只读预检")
        issues = client.soft_limit_issues()
        if issues:
            for i in issues:
                print(f"  !! {i}", flush=True)
            print("  → 控制器会静默截断长行程目标，先整定软限位再跑", flush=True)
            return 2
        print("  软限位体检通过", flush=True)
        describe(client, "当前")
        guard_idle(client)

        off = M1.approach_offset
        v_tr, v_ap = expected_speeds(max(args.dist - off, 1.0))
        leg1 = args.dist - off
        print(f"\n  预期分解（纯 Y，{args.dist:.2f} mm = {args.dist / BOX_PITCH:.2f} 个框距）:", flush=True)
        print(f"    巡检段 {leg1:7.2f} mm @ {v_tr:6.2f} mm/s -> "
              f"{trapezoid_time(leg1, v_tr, composite_limits((leg1, 0.0), M1.travel_limits)[1]):.3f} s", flush=True)
        print(f"    接近段 {off:7.2f} mm @ {v_ap:6.2f} mm/s -> "
              f"{trapezoid_time(off, v_ap, composite_limits((off, 0.0), M1.approach_limits)[1]):.3f} s", flush=True)
        print(f"    ⇒ 理论 {trapezoid_time(leg1, v_tr, composite_limits((leg1, 0.0), M1.travel_limits)[1]) + trapezoid_time(off, v_ap, composite_limits((off, 0.0), M1.approach_limits)[1]):.2f} s"
              f"（若段序颠倒，整段按接近档爬 = {args.dist / v_ap:.1f} s，量级可判别）", flush=True)

        if not args.go:
            print("\n  [演练模式] 未动作。加 --go 真跑。", flush=True)
            return 0

        banner("二、回零归位")
        t0 = time.monotonic()
        client.home_all()
        print(f"  回零完成，耗时 {time.monotonic() - t0:.2f} s", flush=True)
        describe(client, "回零后")

        banner(f"三、往返 {args.cycles} 趟（每趟一个框距）")
        all_ok = True
        for n in range(args.cycles):
            target = args.dist if n % 2 == 0 else 0.0
            print(f"\n  --- 第 {n + 1}/{args.cycles} 趟 ---", flush=True)
            guard_idle(client)
            all_ok &= run_trip(
                client, recorder, target, z=args.z, offset=off, timeout_s=args.timeout
            )

        banner("四、归位与结论")
        guard_idle(client)
        client.goto(0.0, 0.0)
        describe(client, "结束")
        print(f"\n  {'全部判定通过' if all_ok else '有判定未通过，见上面 ** 标记'}", flush=True)
        return 0 if all_ok else 1

    except (FmcError, MotionTimeoutError) as e:
        print(f"\n!! 运动异常：{type(e).__name__}: {e}", flush=True)
        print("   正在急停…", flush=True)
        for f in client.stop_everything():
            print(f"   ! 未确认成功：{f}", flush=True)
        return 3
    except BaseException:
        client.stop_everything()
        raise
    finally:
        try:
            client.close()
        except FmcError as e:
            print(f"  （关闭连接异常，忽略：{e}）", flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
