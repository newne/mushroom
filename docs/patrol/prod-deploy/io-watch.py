#!/usr/bin/env python3
"""IN0–IN3 只读监测：抓「状态有没有变过」，用于判定输入口是真有信号还是悬空。

    python3 io-watch.py [秒数] [采样率Hz]        # 默认 60 s @ 5 Hz

为什么要"抓变化"而不是"读一次"：一次性读数只能得到此刻的电平。输入口是低电平有效，
未接线的 NPN 输入可能被控制器内部下拉读成低（看起来"有效"），也可能是真有信号在拉低。
**唯一能区分的是"电平会不会随外部动作翻转"**——所以现场配合的动作（按急停 / 开门 /
给一次上料信号）必须与这个监测同时进行，脚本只负责把翻转点带时间戳记下来。

⚠️ 不要用 SDK 的逐口 ``FMC4030_Get_Input``：实测在固件 2015 + 库 20240920 上**该调用
永不返回**（卡在 tcp_recvmsg，厂商库无超时）。位图 ``inputStatus`` 的第 0–3 位就是
IN0–IN3，够用且可靠。
"""

from __future__ import annotations

import os
import sys
import time
from datetime import datetime

os.environ.setdefault("FMC4030_LIB_PATH", "/opt/mushroom-patrol/lib/libFMC4030_2009_1.so")
sys.path.insert(0, "/opt/mushroom-patrol/src")

from patrol.fmc import Fmc4030  # noqa: E402
from patrol.fmc.loader import load_library  # noqa: E402
from patrol.motion_profile import CONTROLLER_IP, CONTROLLER_PORT, DEVICE_ID  # noqa: E402

SECONDS = float(sys.argv[1]) if len(sys.argv) > 1 else 60.0
RATE = float(sys.argv[2]) if len(sys.argv) > 2 else 5.0
PERIOD = 1.0 / RATE

client = Fmc4030.connect(lib=load_library(), device_id=DEVICE_ID,
                         ip=CONTROLLER_IP, port=CONTROLLER_PORT)


def show(mask: int) -> str:
    return " ".join(f"IN{b}={(mask >> b) & 1}" for b in range(4))


try:
    first = client.get_status()
    prev_in, prev_out = first.inputs, first.outputs
    print(f"起始 {datetime.now():%H:%M:%S}  IN[0x{prev_in:04x} {show(prev_in)}]  "
          f"OUT[0x{prev_out:04x}]  run_mode={first.run_mode}")
    print(f"监测 {SECONDS:.0f} s @ {RATE:.0f} Hz —— 只在**有变化**时打印（现场动作请同时进行）...")
    changes = 0
    samples = 0
    t_end = time.monotonic() + SECONDS
    while time.monotonic() < t_end:
        st = client.get_status()
        samples += 1
        if st.inputs != prev_in or st.outputs != prev_out:
            changes += 1
            print(f"  [{datetime.now().strftime('%H:%M:%S.%f')[:-3]}] 变化#{changes}  "
                  f"IN 0x{prev_in:04x}→0x{st.inputs:04x} [{show(st.inputs)}]  "
                  f"OUT 0x{prev_out:04x}→0x{st.outputs:04x}")
            prev_in, prev_out = st.inputs, st.outputs
        time.sleep(PERIOD)
    print(f"结束：{samples} 次采样，电平变化 {changes} 次。")
    if changes == 0:
        print("  ⇒ 全程恒定 = 这四个输入没跟着任何现场动作动。要么悬空/内部下拉，")
        print("    要么接的是**本次没人动过**的信号；需要现场逐个触发候选设备再跑一次。")
finally:
    client.close()
