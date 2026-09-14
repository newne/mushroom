"""只读：读控制器内已有脚本文件列表 + 固件/库版本。不发任何运动或写入指令。"""

from __future__ import annotations

import ctypes
import os
import sys

sys.path.insert(0, "/opt/mushroom-patrol/src")

from patrol.fmc.loader import load_library  # noqa: E402
from patrol.motion_profile import CONTROLLER_IP, CONTROLLER_PORT, DEVICE_ID  # noqa: E402

lib = load_library()
dev = DEVICE_ID


def _u32(raw: bytes, off: int) -> tuple[int]:
    return (ctypes.c_uint32.from_buffer_copy(raw[off:off + 4]).value,)


rc = lib.FMC4030_Open_Device(dev, CONTROLLER_IP.encode(), CONTROLLER_PORT)
print(f"Open_Device -> {rc}")
if rc < 0:
    raise SystemExit(1)

try:
    # ---- 1. 控制器内脚本文件列表（machine_status.file[20][30]）----
    buf = (ctypes.c_ubyte * 660)()
    rc = lib.FMC4030_Get_Machine_Status(dev, buf)
    raw = bytes(buf)
    print(f"Get_Machine_Status -> {rc}")
    print()
    print("=== 控制器内已有程序文件（至多 20 个，每个 30 字节）===")
    found = 0
    for i in range(20):
        chunk = raw[60 + i * 30: 60 + (i + 1) * 30]
        name = chunk.split(b"\x00", 1)[0]
        if name:
            found += 1
            print(f"  [{i:2d}] {name.decode('utf-8', 'replace')!r}  raw={chunk[:20].hex()}")
    if not found:
        print("  (控制器内无脚本文件)")
    print()

    # ---- 2. 固件 / 动态库 / 序列号 ----
    ver = (ctypes.c_ubyte * 12)()
    rc = lib.FMC4030_Get_Version_Info(dev, ver)
    if rc == 0:
        firmware, libv, serial = (ctypes.c_uint * 3).from_buffer_copy(bytes(ver))
        print("=== 版本 ===")
        print(f"  固件版本 firmware = {firmware}")
        print(f"  动态库版本 lib    = {libv}")
        print(f"  控制器序列号      = {serial}")
    else:
        print(f"Get_Version_Info -> {rc}")

    # ---- 3. 运行模式 / 输出状态（确认没在跑脚本）----
    # 结构体 offset 0..43 依次为 realPos / realSpeed / inputStatus / outputStatus /
    # limitNStatus / limitPStatus —— machineRunStatus 在 offset 40，别数错。
    (inp,) = _u32(raw, 24)
    (out,) = _u32(raw, 28)
    (lim_n,) = _u32(raw, 32)
    (lim_p,) = _u32(raw, 36)
    (run,) = _u32(raw, 40)
    print()
    print("=== 运行状态 ===")
    print(f"  inputStatus    = 0x{inp:04x}")
    print(f"  outputStatus   = 0x{out:04x}")
    print(f"  limitNStatus   = 0x{lim_n:04x}   limitPStatus = 0x{lim_p:04x}")
    print(f"  machineRunStatus = 0x{run:04x}  "
          f"({'MANUAL 手动' if run & 1 else 'AUTO 脚本' if run & 2 else '未置位（两种模式位都未设）'})")
    for i in range(3):
        (ax,) = _u32(raw, 44 + i * 4)
        print(f"  axisStatus[{i}] = 0x{ax:04x}")
finally:
    print()
    print(f"Close_Device -> {lib.FMC4030_Close_Device(dev)}")
