#!/usr/bin/env python3
"""把 Z 的控制器软限位整定为 `0…212`（ADR-0018 的框架迁移配套动作）。

为什么必须做：Z 的框架改成"顶端为 0、向下为正、行程 0…212"之后，控制器里那层软限位
（ADR-0008）还停留在旧值——现场读回是 `softLimitMax[2] = -1`，即**取消**。带着它跑并非
"少一层保护"这么简单：控制器若把目标按旧范围截断，会在**返回成功**的情况下少走一段，
而精度问题在采图偏位上才暴露。

用法（在库房主机上，**先停 patrol 容器**——控制器是单会话设备）：

    docker run --rm -v $BASE/mushroom_patrol/lib:/opt/fmc-lib:ro \
      -e FMC4030_LIB_PATH=/opt/fmc-lib/libFMC4030_2009_1.so \
      -v /path/to/this.py:/tmp/set.py registry.../mushroom_patrol:0.1.0 \
      python3 /tmp/set.py            # 只打印将要写入什么
    ... python3 /tmp/set.py --go     # 真写

写入前会自动打印 before/after，写完再读回一次核对——设备参数是**整体 read-modify-write**，
没有局部写，所以"读回核对"是唯一能确认这一步真的生效的手段。
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import replace

from patrol.fmc import Fmc4030
from patrol.fmc.loader import load_library
from patrol.motion_profile import CONTROLLER_IP, CONTROLLER_PORT, DEVICE_ID, M1


def main() -> int:
    ap = argparse.ArgumentParser(description="整定 Z 的控制器软限位（ADR-0018）")
    ap.add_argument("--go", action="store_true", help="真写入（默认只打印）")
    ap.add_argument("--lib", default=None)
    args = ap.parse_args()

    client = Fmc4030.connect(lib=load_library(args.lib), device_id=DEVICE_ID,
                             ip=CONTROLLER_IP, port=CONTROLLER_PORT)
    try:
        before = client.get_device_para()
        z = M1.z.index
        print("写入前：")
        print("  ", before.describe().replace("\n", "\n   "))

        want_max = list(before.soft_limit_max)
        want_min = list(before.soft_limit_min)
        want_max[z] = int(M1.z.travel_max)      # 212
        want_min[z] = int(M1.z.travel_min)      # 0
        after = replace(before, soft_limit_max=tuple(want_max), soft_limit_min=tuple(want_min))

        print(f"将要写入：轴{z} 软限位 = {want_min[z]} … {want_max[z]}"
              f"（= 行程 {M1.z.travel_min:g}…{M1.z.travel_max:g}）")
        if not args.go:
            print("（未写入：加 --go 才真写）")
            return 0

        client.set_device_para(after)
        back = client.get_device_para()
        got = back.effective_limits(z)
        print("写入后读回：", back.describe().replace("\n", "\n   "))
        print(f"  轴{z} 生效区间 = {got}")
        if got != (M1.z.travel_min, M1.z.travel_max):
            print("! 读回与期望不一致——控制器可能拒收，先别走下一步", file=sys.stderr)
            return 2
        print("✅ 软限位已生效（轴%d %s…%s）" % (z, M1.z.travel_min, M1.z.travel_max))
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    sys.exit(main())
