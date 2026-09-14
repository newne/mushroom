#!/usr/bin/env python3
"""M0 现场执行：把「回零约定」在真机上跑一遍并留下证据。

    python3 m0-commission.py                 # 只读演练：打印计划与当前状态，不动作
    python3 m0-commission.py --go            # 真执行（P1…P5）
    python3 m0-commission.py --go --no-capture

执行序列：

    P0 预检      位置 / 模式 / 各轴状态 / 软限位；任一轴在动即中止
    P1 离开原点  Y +100、Z -40（M0 慢档），为「回零方向实证」造出可判别的距离
    P2 回零      逐轴计时。**方向对不对由耗时一个数量级地判出来**，不需要额外仪表：
                   Y 负限位回零：从 +100 出发只需走 100 mm ⇒ ~1.2 s
                                 方向若反了则要走 4392 mm ⇒ ~49 s
                   Z 正限位回零：从 -40 出发只需走 40 mm ⇒ ~2.0 s
                                 方向若反了则要走 172 mm ⇒ ~9 s 后硬顶限位
    P3 首站定位  蛇形第 1 站 S101（187.17, -21.2），M0 点动档单段直达
    P4 单站采图  点灯 → 采图服务 → 熄灯，校验 HTTP 与落盘字节数
    P5 回原点    goto_2axis(0, 0)

所有运动走 M0 手动档（Y 10 mm/s、Z 4 mm/s），**不用巡检档 150 mm/s**——
150 是尚未在这台机器上验证过的值（驱动器最大输入频率无手册），首次通检不碰它。
巡检档验证另用 travel-speed-check.py，需人机同场。
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.request

os.environ.setdefault("FMC4030_LIB_PATH", "/opt/mushroom-patrol/lib/libFMC4030_2009_1.so")
sys.path.insert(0, "/opt/mushroom-patrol/src")

from patrol.fmc import Fmc4030, HomeTimeoutError, MotionTimeoutError
from patrol.fmc.loader import load_library
from patrol.motion_profile import CONTROLLER_IP, CONTROLLER_PORT, DEVICE_ID, M1
from patrol.stations import build_grid

GO = "--go" in sys.argv
CAPTURE = "--no-capture" not in sys.argv
CAMERA_IP = "192.168.1.238"
CAPTURE_URL = (
    "http://127.0.0.1:7003/dynamic_capture"
    f"?ip={CAMERA_IP}&user=admin&storage=local&filename="
)
PICTURE_DIR = (
    "/home/sysadmin/algorithm/mushroom_docker/"
    "xcloudsdk_py_offline_20260120_175307/saved_datas/picture"
)
JOG_Y, JOG_Z = 100.0, -40.0   # 离开原点的距离（正负号刻意与原点相反）
HOME_TIMEOUT = 180.0

Y_AXIS, Z_AXIS = M1.y.index, M1.z.index
STEP = 0


def head(title: str) -> None:
    global STEP
    STEP += 1
    print(f"\n{'=' * 78}\nP{STEP}  {title}\n{'=' * 78}", flush=True)


def describe(client: Fmc4030, tag: str = ""):
    st = client.get_status()
    pos = "  ".join(f"{a.name}={st.real_pos[a.index]:9.3f}" for a in M1.axes)
    flags = "  ".join(
        f"{a.name}[{'运行' if st.axes[a.index].running else '停'}"
        f"{'/已回零' if st.axes[a.index].homed else '/未回零'}"
        f"{'/超时' if st.axes[a.index].home_overtime else ''}]"
        for a in M1.axes
    )
    print(f"  {tag}位置 {pos}", flush=True)
    print(f"      {flags}  模式={st.run_mode}  IN={st.inputs:#06x} OUT={st.outputs:#06x}", flush=True)
    return st


class Abort(RuntimeError):
    """现场序列中止：走急停分支，绝不把轴留在运动状态。"""


def guard_idle(client: Fmc4030) -> None:
    """任何一个接线轴在动就拒绝继续——避免在运动中途叠指令（SDK 会静默丢弃）。"""
    st = client.get_status()
    moving = [a.name for a in M1.axes if st.axes[a.index].running]
    if moving:
        raise Abort(f"✋ {'、'.join(moving)} 仍在运行，拒绝叠加指令（先急停，再排查）")


def home_one(client: Fmc4030, axis: int) -> float:
    spec = M1.by_index(axis)
    guard_idle(client)
    t0 = time.monotonic()
    client.home_axis(axis)
    if not client.wait_home(axes=(axis,), timeout_s=HOME_TIMEOUT):
        raise MotionTimeoutError(f"{spec.name} 回零在主機側超时（{HOME_TIMEOUT:g}s）")
    dt = time.monotonic() - t0
    where = "负限位" if spec.home_dir == 2 else "正限位"
    print(f"  {spec.name} 回零完成（{where}，homeDir={spec.home_dir}）  耗时 {dt:6.2f} s", flush=True)
    return dt


client = Fmc4030.connect(lib=load_library(), device_id=DEVICE_ID,
                         ip=CONTROLLER_IP, port=CONTROLLER_PORT)
try:
    head("预检（只读）")
    st = describe(client, "当前")
    guard_idle(client)
    print(f"  控制器 {CONTROLLER_IP}:{CONTROLLER_PORT}  设备ID={DEVICE_ID}", flush=True)
    print("  软限位体检：", flush=True)
    issues = client.soft_limit_issues()
    if issues:
        for it in issues:
            print(f"    ! {it}", flush=True)
        print("    （Y 必须生效在 [0,4495]；Z 已取消由本程序行程校验兜底，见 ADR-0008）", flush=True)
    else:
        print("    OK（无窄于行程的软限位）", flush=True)

    station1 = build_grid()[0]
    print("\n  本机约定（motion_profile 单源）：", flush=True)
    for a in M1.axes:
        d = "正向/向上" if a.home_dir == 1 else "反向/向左"
        print(f"    {a.name}  轴{a.index}  行程[{a.travel_min:g}, {a.travel_max:g}]"
              f"  回零 {d}  落点 {a.home_position:g}"
              f"  （回零档 {a.home_speed:g}/{a.home_acc:g}，M0 档 {a.jog_speed:g}/{a.jog_acc:g}）", flush=True)
    print(f"    首站 {station1.id} = (Y {station1.y:.2f}, Z {station1.z:.2f})", flush=True)

    if not GO:
        head("演练结束（未动作）")
        print("  以上为计划。要真执行：加 --go", flush=True)
        raise SystemExit(0)

    # ---------------- P2 离开原点 ----------------
    head("离开原点（M0 慢档，为方向实证造距离）")
    for axis, dist in ((Y_AXIS, JOG_Y), (Z_AXIS, JOG_Z)):
        spec = M1.by_index(axis)
        guard_idle(client)
        t0 = time.monotonic()
        client.jog(axis, dist)
        if not client.wait_stop(axes=(axis,), timeout_s=HOME_TIMEOUT):
            raise MotionTimeoutError(f"{spec.name} 点动未到位")
        print(f"  {spec.name} 点动 {dist:+g} mm  耗时 {time.monotonic() - t0:6.2f} s", flush=True)
    describe(client, "离开后")

    # ---------------- P3 回零（方向实证） ----------------
    head("回零：Y 反向回零→原点，Z 向上回零→0 点")
    t_y = home_one(client, Y_AXIS)
    t_z = home_one(client, Z_AXIS)
    st = describe(client, "回零后")

    ok = True
    for a in M1.axes:
        if not st.axes[a.index].homed:
            print(f"  ! {a.name} 未报回零完成", flush=True)
            ok = False
        if abs(st.real_pos[a.index] - a.home_position) > 0.05:
            print(f"  ! {a.name} 回零落点 {st.real_pos[a.index]:.3f} ≠ 设定原点 "
                  f"{a.home_position:g}（脱落距离的零点语义待确认）", flush=True)
            ok = False
    # 方向判据：耗时是否在「只走离开的那点距离」的量级
    for name, dt, dist, speed in (("Y", t_y, abs(JOG_Y), M1.y.home_speed),
                                  ("Z", t_z, abs(JOG_Z), M1.z.home_speed)):
        expect = dist / speed
        verdict = "方向正确" if dt < expect * 4 + 3 else "⚠ 耗时远超预期，方向可能反了"
        print(f"  {name} 期望 ≈{expect:.1f}s（只走 {dist:g}mm）  实测 {dt:.2f}s  ⇒ {verdict}", flush=True)
    print(f"  结论：{'两轴回零方向与设定一致，落点=原点' if ok else '有异常，见上面 ! 行'}", flush=True)

    # ---------------- P4 首站定位 ----------------
    head(f"首站定位 {station1.id}")
    guard_idle(client)
    t0 = time.monotonic()
    client.goto_2axis(station1.y, station1.z)
    dt = time.monotonic() - t0
    st = describe(client, "到位后")
    dy = st.real_pos[Y_AXIS] - station1.y
    dz = st.real_pos[Z_AXIS] - station1.z
    print(f"  耗时 {dt:.2f} s  定位偏差 ΔY={dy:+.3f} mm  ΔZ={dz:+.3f} mm"
          f"  （M0 单段直达，走点动档）", flush=True)

    # ---------------- P5 单站采图 ----------------
    if CAPTURE:
        head("单站采图（点灯 → 采图 → 熄灯）")
        name = f"m0_{station1.id}_{time.strftime('%Y%m%d_%H%M%S')}"
        client.lamp(True)
        print("  补光灯 ON（OUT0=1）", flush=True)
        time.sleep(M1.lamp_settle_s)
        try:
            t0 = time.monotonic()
            with urllib.request.urlopen(CAPTURE_URL + name, timeout=60) as resp:
                body = resp.read().decode("utf-8", "replace")
                print(f"  HTTP {resp.status}  耗时 {time.monotonic() - t0:.2f} s", flush=True)
            print(f"  响应 {body[:400]}", flush=True)
            try:
                data = json.loads(body)
                fname = data.get("filename") or data.get("file") or name
            except ValueError:
                fname = name
            path = os.path.join(PICTURE_DIR, fname if fname.endswith(".jpg") else fname + ".jpg")
            if os.path.exists(path):
                print(f"  落盘 {path}  {os.path.getsize(path) / 1024:.0f} KB", flush=True)
            else:
                print(f"  ! 未在 {PICTURE_DIR} 找到 {os.path.basename(path)}", flush=True)
        except Exception as e:  # noqa: BLE001 — 采图失败不该中断收尾
            print(f"  ! 采图失败：{type(e).__name__}: {e}", flush=True)
        finally:
            time.sleep(M1.lamp_after_s)
            client.lamp(False)
            print("  补光灯 OFF（OUT0=0） + 单站采图完成", flush=True)

    # ---------------- P6 回原点 ----------------
    head("回原点")
    guard_idle(client)
    client.goto_2axis(0.0, 0.0)
    describe(client, "收尾")
    print("\nM0 一轮执行完毕。", flush=True)

except (HomeTimeoutError, MotionTimeoutError) as e:
    print(f"\n! 运动异常：{type(e).__name__}: {e}", flush=True)
    print("  正在急停…", flush=True)
    failed = client.stop_everything()
    print(f"  急停{'有未确认项：' + '；'.join(failed) if failed else '已全部送达'}", flush=True)
    describe(client, "急停后")
    raise SystemExit(2)
except SystemExit:
    raise
except BaseException as e:
    print(f"\n! 异常：{type(e).__name__}: {e}", flush=True)
    failed = client.stop_everything()
    print(f"  急停{'有未确认项：' + '；'.join(failed) if failed else '已全部送达'}", flush=True)
    raise
finally:
    client.close()
