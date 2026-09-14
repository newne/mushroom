#!/usr/bin/env python3
"""只读探针：设备参数 + 状态 + IO（逐位与逐口）+ 控制器内脚本清单。

不发任何运动指令、不写任何输出口。用于「补光灯极性 / IN0–IN3 含义」取证的第一步。

    FMC4030_LIB_PATH=/opt/mushroom-patrol/lib/libFMC4030_2009_1.so \
    PYTHONPATH=/opt/mushroom-patrol/src python3 probe-io.py [采样秒数]
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
from patrol.motion_profile import CONTROLLER_IP, CONTROLLER_PORT, DEVICE_ID  # noqa: E402

SAMPLES = int(sys.argv[1]) if len(sys.argv) > 1 else 10
DEV = DEVICE_ID

lib = load_library()
print("① 连接控制器 ...", flush=True)
client = Fmc4030.connect(lib=lib, device_id=DEV, ip=CONTROLLER_IP, port=CONTROLLER_PORT)
print("   已连接；② 读设备参数（Get_Device_Para）...", flush=True)


def bits(value: int) -> str:
    return " ".join(f"{value >> b & 1}" for b in range(4))


def io_bits(value: int) -> str:
    return " ".join(f"IN{b}={value >> b & 1}" for b in range(4))


def out_bits(value: int) -> str:
    return " ".join(f"OUT{b}={value >> b & 1}" for b in range(4))


try:
    print("=" * 78)
    print("一、设备参数（读回，用于核对脉冲当量）")
    dp = client.get_device_para()
    print("   设备参数读回 OK")
    for i in (0, 1, 2):
        ppmm = dp.div[i] / dp.lead[i] if dp.lead[i] else float("nan")
        print(f"  轴{i}: 细分={dp.div[i]:>8} 导程={dp.lead[i]:>6} mm/r 脉冲/mm={ppmm:>10.3f}"
              f"  软限位原值={dp.raw_limits(i)} 生效={dp.effective_limits(i)}")
    print(f"  回零超时 homeTime = {dp.home_time}")

    print()
    print("=" * 78)
    print("二、控制器内脚本清单 / 版本 / 运行模式")
    print("   Get_Machine_Status ...", flush=True)
    buf = (ctypes.c_ubyte * ctypes.sizeof(MachineStatusStruct))()
    rc = lib.FMC4030_Get_Machine_Status(DEV, buf)
    raw = bytes(buf)
    st = MachineStatusStruct.from_buffer_copy(raw)
    names = []
    for i in range(20):
        chunk = raw[60 + i * 30: 60 + (i + 1) * 30]
        name = chunk.split(b"\x00", 1)[0]
        if name:
            names.append(name.decode("utf-8", "replace"))
    print(f"  Get_Machine_Status rc={rc}  脚本文件 {len(names)} 个: {names or '(无)'}")
    ver = (ctypes.c_ubyte * 12)()
    if lib.FMC4030_Get_Version_Info(DEV, ver) == 0:
        fw, libv, sn = (ctypes.c_uint * 3).from_buffer_copy(bytes(ver))
        print(f"  固件={fw} 动态库={libv} 序列号={sn}")
    print(f"  homeStatus=0x{st.homeStatus:04x}  axisStatus="
          + " ".join(f"{v:#06x}" for v in st.axisStatus))

    print()
    print("=" * 78)
    print(f"三、IO 时间序列（{SAMPLES} 次采样，1 Hz；只读）")
    print("  #   real_pos(Y,Z)          inputStatus        outputStatus       mode")
    for k in range(SAMPLES):
        st = client.get_status()
        print(f"  {k:>2}  Y={st.real_pos[1]:>9.3f} Z={st.real_pos[2]:>9.3f}  "
              f"0x{st.inputs:04x} [{io_bits(st.inputs)}]  "
              f"0x{st.outputs:04x} [{out_bits(st.outputs)}]  {st.run_mode}")
        if k != SAMPLES - 1:
            time.sleep(1.0)

    print()
    print("四、逐口 Get_Input（与上面位图的第 0–3 位应一致）")
    st = client.get_status()
    for io in range(4):
        v = ctypes.c_int(-1)
        rc = lib.FMC4030_Get_Input(DEV, io, ctypes.byref(v))
        bit = (st.inputs >> io) & 1
        print(f"  IN{io}: rc={rc} value={v.value}  位图第{io}位={bit}"
              f"  （低电平有效 ⇒ 1 表示该输入被触发）")
    print("  OUT 逐口无法单独读；outputStatus 位图 = 控制器输出寄存器的回读")
finally:
    client.close()
