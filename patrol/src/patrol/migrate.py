"""一次性数据迁移的入口（`patrol-migrate`）。

现在只有一个子命令：`z-frame`——把站位表的 Z 坐标从旧框架搬到 ADR-0018 的新框架。

为什么单独给个命令而不是"跑个 python 片段"：这是**动生产配置**的操作，必须有
"先看 diff、再落盘、落盘前自动备份"的固定动作，且能被写进上机手册里逐条执行。

    patrol-migrate z-frame --stations configs/stations.yaml --dry-run
    patrol-migrate z-frame --stations configs/stations.yaml
"""

from __future__ import annotations

import argparse
import shutil
import sys
from datetime import datetime
from pathlib import Path

from patrol.stations import load_stations, mirror_z_in_file


def _backup(path: str) -> str:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dst = f"{path}.bak-z-frame-{stamp}"
    shutil.copy2(path, dst)
    return dst


def cmd_z_frame(args: argparse.Namespace) -> int:
    path = Path(args.stations)
    if not path.exists():
        print(f"! 找不到站位表：{path}", file=sys.stderr)
        return 2

    before = load_stations(str(path))
    first = before[0] if before else None
    print(f"站位表：{path}（{len(before)} 个站位）")
    if first is not None:
        print(f"  迁移前 S 例：{first.id} y={first.y:.1f} z={first.z:.1f} "
              f"trim_z={first.trim_z:.1f}")
    if args.dry_run:
        # 预演写到一个临时文件，再把结果读回来看——不碰原文件
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            out = str(Path(tmp) / "stations.yaml")
            mirror_z_in_file(str(path), out=out, log=print)
            after = load_stations(out)
        if after:
            print(f"  迁移后 S 例：{after[0].id} y={after[0].y:.1f} z={after[0].z:.1f} "
                  f"trim_z={after[0].trim_z:.1f}")
        print("（--dry-run：原文件未改动）")
        return 0

    bak = _backup(str(path))
    print(f"已备份：{bak}")
    mirror_z_in_file(str(path), log=print)
    after = load_stations(str(path))
    if after:
        print(f"  迁移后 S 例：{after[0].id} y={after[0].y:.1f} z={after[0].z:.1f} "
              f"trim_z={after[0].trim_z:.1f}")
    print("⚠️ 迁移后**必须先回零**再下发任何绝对坐标（控制器里还是旧框架的零点）")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="patrol-migrate", description="一次性数据迁移")
    sub = ap.add_subparsers(dest="cmd", required=True)

    z = sub.add_parser("z-frame", help="Z 坐标框架镜像（ADR-0018）")
    z.add_argument("--stations", default="/app/configs/stations.yaml")
    z.add_argument("--dry-run", action="store_true", help="只看换算结果，不落盘")
    z.set_defaults(func=cmd_z_frame)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":       # pragma: no cover
    sys.exit(main())
