#!/usr/bin/env python3
"""M0 全量只读读数：设备参数原始字段 + 三轴状态标志逐位解码 + IO。
不含任何运动指令（只调用 Get_Device_Para / Get_Status）。"""
import os
import sys

os.environ.setdefault("FMC4030_LIB_PATH", "/opt/mushroom-patrol/lib/libFMC4030_2009_1.so")
sys.path.insert(0, "/opt/mushroom-patrol/src")

from patrol.fmc import Fmc4030                      # noqa: E402
from patrol.fmc.loader import load_library          # noqa: E402
from patrol.fmc.status import MachineStatusStruct as _MSS  # noqa: E402
from patrol.motion_profile import CONTROLLER_IP, CONTROLLER_PORT, DEVICE_ID  # noqa: E402

LIB = load_library()
client = Fmc4030.connect(lib=LIB, device_id=DEVICE_ID, ip=CONTROLLER_IP, port=CONTROLLER_PORT)

try:
    print("========== 设备参数（控制器内持久化） ==========")
    dp = client.get_device_para()
    print("原始 dataclass :", dp)
    for name in ("ip", "port", "div", "lead", "soft_limit_max", "soft_limit_min", "home_time"):
        print(f"  {name:16} = {getattr(dp, name, '<无此字段>')}")
    print()
    print("--- 逐轴解读 ---")
    for i in (0, 1, 2):
        eff = dp.effective_limits(i)
        status = f"生效区间 [{eff[0]}, {eff[1]}] mm" if eff else \
                 f"软限位已取消（原值 {dp.raw_limits(i)}）"
        ppmm = (dp.div[i] / dp.lead[i]) if dp.lead[i] else float("nan")
        reach = ""
        if eff:
            reach = f"  行程 {eff[1] - eff[0]:.1f} mm"
        print(f"  轴{i}: 细分={dp.div[i]:>8}  导程={dp.lead[i]:>6} mm/r  "
              f"脉冲/mm={ppmm:>10.3f}  {status}{reach}")
    print()
    print("--- 软限位体检 ---")
    try:
        issues = client.soft_limit_issues()
        if issues:
            for it in issues:
                print("  !", it)
        else:
            print("  OK：无告警")
    except Exception as e:                           # noqa: BLE001
        print("  <soft_limit_issues 调用失败>", e)
    print()
    print("========== 状态（实时） ==========")
    st = client.get_status()
    print("real_pos   :", st.real_pos)
    print("real_speed :", st.real_speed)
    print("inputs     : 0x%04x" % st.inputs)
    print("outputs    : 0x%04x" % st.outputs)
    print("run_mode   :", st.run_mode)
    print("--- 逐轴标志 ---")
    flag_names = [
        ("running", "运行中"), ("paused", "暂停"), ("stopped", "停止"),
        ("limit_n", "负限位触发"), ("limit_p", "正限位触发"),
        ("home_done", "回零完成"), ("homing", "回零中"),
        # 注意：厂商 *_NONE 的语义是「未触发 / 未回零」，不是「没有这个开关」
        ("limit_n_none", "负限位未触发"), ("limit_p_none", "正限位未触发"),
        ("home_none", "未回零"), ("home_overtime", "回零超时"),
    ]
    for i, a in enumerate(st.axes):
        on = [f"{cn}({en})" for en, cn in flag_names if getattr(a, en, False)]
        print(f"  轴{i}: homed={getattr(a, 'homed', '?')}  置位标志: {', '.join(on) if on else '(无)'}")
    print()
    print("--- IO 逐位 ---")
    print("  IN  :", " ".join(f"IN{b}={(st.inputs >> b) & 1}" for b in range(8)))
    print("  OUT :", " ".join(f"OUT{b}={(st.outputs >> b) & 1}" for b in range(8)))
    if hasattr(client, "current_yz"):
        print()
        print("current_yz() =", client.current_yz())

    # ---- 结构体里我们尚未解析的三个专用字段（原始值） ----
    print()
    print("--- machine_status 未解析字段（原始） ---")
    import ctypes as _ct
    buf = (_ct.c_ubyte * _ct.sizeof(_MSS))()
    rc = LIB.FMC4030_Get_Machine_Status(DEVICE_ID, buf)
    print(f"FMC4030_Get_Machine_Status rc={rc}  sizeof={_ct.sizeof(_MSS)}")
    raw = _MSS.from_buffer_copy(bytes(buf))
    print("limitNStatus : 0x%08x  -> 逐轴 " % raw.limitNStatus
          + " ".join(f"轴{i}={(raw.limitNStatus >> i) & 1}" for i in range(3)))
    print("limitPStatus : 0x%08x  -> 逐轴 " % raw.limitPStatus
          + " ".join(f"轴{i}={(raw.limitPStatus >> i) & 1}" for i in range(3)))
    print("homeStatus   : 0x%08x  -> 逐轴 " % raw.homeStatus
          + " ".join(f"轴{i}={(raw.homeStatus >> i) & 1}" for i in range(3)))
    print("axisStatus   : " + "  ".join("轴%d=0x%04x" % (i, v) for i, v in enumerate(raw.axisStatus)))
    files = bytes(raw.file).split(b"\x00", 1)[0]
    print("file[0]      :", files[:40] or "(空)")
finally:
    client.close()
    print()
    print("(已断开连接)")
