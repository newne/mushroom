#!/usr/bin/env python3
"""补光灯极性实测（OUT0）。

判据不靠肉眼：**同一机位、同一相机，关灯/开灯各采一帧，比亮度**。
说明书 §三.3 + SDK 详解 p4 都说 `Set_Output(status=1) → 输出低电平 → 回路导通 → 负载得电`，
本脚本实测这条推论链的最后一环：**负载到底亮没亮**。

    python3 lamp-polarity.py            # 关灯采一帧 → 开灯采一帧 → 复位
    python3 lamp-polarity.py --keep     # 测完不关灯（不影响默认行为：默认一定复位）

只碰 OUT0，不动任何轴。测完把 OUT0 恢复到脚本开始时读到的状态。
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.parse
import urllib.request

os.environ.setdefault("FMC4030_LIB_PATH", "/opt/mushroom-patrol/lib/libFMC4030_2009_1.so")
sys.path.insert(0, "/opt/mushroom-patrol/src")

from patrol.fmc import Fmc4030  # noqa: E402
from patrol.fmc.loader import load_library  # noqa: E402
from patrol.motion_profile import CONTROLLER_IP, CONTROLLER_PORT, DEVICE_ID  # noqa: E402

LAMP_IO = 0
CAMERA_IP = "192.168.1.238"
CAPTURE_URL = "http://127.0.0.1:7003/dynamic_capture"
KEEP_ON = "--keep" in sys.argv

client = Fmc4030.connect(lib=load_library(), device_id=DEVICE_ID,
                         ip=CONTROLLER_IP, port=CONTROLLER_PORT)


def out_bit() -> int:
    return (client.get_status().outputs >> LAMP_IO) & 1


def shoot(name: str) -> dict:
    query = urllib.parse.urlencode({"ip": CAMERA_IP, "user": "admin",
                                    "storage": "local", "filename": name})
    url = f"{CAPTURE_URL}?{query}"
    print(f"   GET {url}", flush=True)
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(url, timeout=60) as resp:
            body = resp.read().decode("utf-8", "replace")
            dt = time.monotonic() - t0
            print(f"   HTTP {resp.status}  {dt:.1f}s  {body[:400]}", flush=True)
            try:
                return {"http": resp.status, "elapsed": round(dt, 2), **json.loads(body)}
            except json.JSONDecodeError:
                return {"http": resp.status, "elapsed": round(dt, 2), "raw": body}
    except Exception as e:                                     # noqa: BLE001
        print(f"   !! 采图失败: {e}", flush=True)
        return {"error": str(e)}


orig = out_bit()
print(f"① 起始 OUT{LAMP_IO} = {orig}（0=输出高电平/回路断开；1=输出低电平/回路导通）", flush=True)

result: dict = {"orig_out": orig}
try:
    print("② 关灯态采图（把 OUT0 显式置 0，保证基线是「灯灭」）...", flush=True)
    client.set_output(LAMP_IO, False)
    time.sleep(0.5)
    result["out_before"] = out_bit()
    result["shot_off"] = shoot("lampcheck_off")

    print("③ 开灯（OUT0=1）后采图 ...", flush=True)
    client.set_output(LAMP_IO, True)
    time.sleep(1.0)                       # 与 M1 的 lamp_settle_s 同量级
    result["out_during"] = out_bit()
    result["shot_on"] = shoot("lampcheck_on")
finally:
    if KEEP_ON:
        print("④ --keep：保持开灯", flush=True)
    else:
        client.set_output(LAMP_IO, bool(orig))
        time.sleep(0.3)
        print(f"④ 复位 OUT{LAMP_IO} → {out_bit()}", flush=True)
    client.close()

print()
print("=== 结果 ===")
print(json.dumps(result, ensure_ascii=False, indent=2))
