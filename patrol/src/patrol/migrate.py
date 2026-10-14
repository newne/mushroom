"""一次性数据迁移的入口（`patrol-migrate`）。

两个子命令，动的是**同一张站位表**的不同侧面：

* ``z-frame``——搬**坐标框架**：原点从底端搬到顶端、第 1 层翻到最上（一次性）；
* ``reanchor``——刷**锚点数值**：框架不动，按当前的 `col_y`/`layer_z` 重算每站的
  (y, z)。用户改层心/改列偏移（如 2026-10-12 第 8 列右移 40mm）之后用这个，
  可反复执行（幂等）。`--view-stage` 换**观察高度档**（票 16，生长阶段 → 每层 Z 偏置），
  档位数值由现场量相位后填，未填之前只有默认档 `surface`（全 0）。

为什么单独给个命令而不是"跑个 python 片段"：这是**动生产配置**的操作，必须有
"先看 diff、再落盘、落盘前自动备份"的固定动作，且能被写进上机手册里逐条执行。

    patrol-migrate z-frame  --stations configs/stations.yaml --dry-run
    patrol-migrate z-frame  --stations configs/stations.yaml
    patrol-migrate reanchor --stations configs/stations.yaml --dry-run
    patrol-migrate reanchor --stations configs/stations.yaml
    patrol-migrate reanchor --stations configs/stations.yaml --view-stage stipe --dry-run
"""

from __future__ import annotations

import argparse
import shutil
import sys
from datetime import datetime
from pathlib import Path

from patrol.stations import (
    DEFAULT_VIEW_STAGE,
    GRID_VIEW_STAGE_Z_OFFSETS,
    Station,
    load_station_table,
    load_stations,
    reanchor_in_file,
    retarget_z_in_file,
    stage_consistency,
)


def _backup(path: str, tag: str) -> str:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dst = f"{path}.bak-{tag}-{stamp}"
    shutil.copy2(path, dst)
    return dst


def _first_per_layer(stations: list[Station]) -> list[Station]:
    """每层挑第一个站位当例子（层号升序＝从最上层到最下层）。"""
    seen: dict[int, Station] = {}
    for s in stations:
        seen.setdefault(s.layer, s)
    return [seen[layer] for layer in sorted(seen)]


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
            retarget_z_in_file(str(path), out=out, log=print)
            after = load_stations(out)
        if after:
            print(f"  迁移后 S 例：{after[0].id} y={after[0].y:.1f} z={after[0].z:.1f} "
                  f"trim_z={after[0].trim_z:.1f}")
        print("（--dry-run：原文件未改动）")
        return 0

    bak = _backup(str(path), "z-frame")
    print(f"已备份：{bak}")
    retarget_z_in_file(str(path), log=print)
    after = load_stations(str(path))
    if after:
        print(f"  迁移后 S 例：{after[0].id} y={after[0].y:.1f} z={after[0].z:.1f} "
              f"trim_z={after[0].trim_z:.1f}")
    print("⚠️ 迁移后**必须先回零**再下发任何绝对坐标（控制器里还是旧框架的零点）")
    return 0


def cmd_reanchor(args: argparse.Namespace) -> int:
    """按当前锚点 + 观察高度档重算 (y, z)——只改目标值，不改坐标框架（无需回零）。"""
    path = Path(args.stations)
    if not path.exists():
        print(f"! 找不到站位表：{path}", file=sys.stderr)
        return 2

    before, declared = load_station_table(str(path))
    requested = getattr(args, "view_stage", None)
    effective = (requested or declared).strip() or DEFAULT_VIEW_STAGE
    known = ", ".join(sorted(GRID_VIEW_STAGE_Z_OFFSETS)) or "（空）"
    print(f"站位表：{path}（{len(before)} 个站位）")
    print(f"  观察高度档：表里声明 {declared!r}"
          + (f"，本次指定 {requested!r}" if requested else "（未指定，沿用表里的档）")
          + f" ⇒ 本次按 {effective!r} 重算；档表里已定义的档：{known}")

    mismatched = stage_consistency(before, effective)
    if mismatched:
        head = "、".join(f"{sid}(z={z:.1f}，锚点 {want:.1f})" for sid, z, want in mismatched[:3])
        print(f"  提示：{len(mismatched)} 个站位的 z 与该档锚点不一致"
              f"（含现场示教值，属正常）：{head}{'…' if len(mismatched) > 3 else ''}")
    for s in _first_per_layer(before):
        print(f"  重锚前 第 {s.layer} 层第 {s.col} 框：{s.id} y={s.y:.1f} z={s.z:.1f} "
              f"trim_y={s.trim_y:.1f} trim_z={s.trim_z:.1f}")

    if args.dry_run:
        # 预演写到一个临时文件，再把结果读回来看——不碰原文件
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            out = str(Path(tmp) / "stations.yaml")
            moved = reanchor_in_file(str(path), out=out, stage=effective, log=print)
            after, after_stage = load_station_table(out)
        for s in _first_per_layer(after):
            print(f"  重锚后 第 {s.layer} 层第 {s.col} 框：{s.id} y={s.y:.1f} z={s.z:.1f}")
        print(f"  档名写回：{after_stage!r}")
        print(f"（--dry-run：原文件未改动；落盘会有 {moved} 个站位有改动）")
        return 0

    bak = _backup(str(path), "reanchor")
    print(f"已备份：{bak}")
    moved = reanchor_in_file(str(path), stage=effective, log=print)
    after, after_stage = load_station_table(str(path))
    for s in _first_per_layer(after):
        print(f"  重锚后 第 {s.layer} 层第 {s.col} 框：{s.id} y={s.y:.1f} z={s.z:.1f}")
    print(f"{moved} 个站位有改动，观察高度档 {after_stage!r} 已写回表。"
          "坐标框架未变（原点还在顶端），**不需要重新回零**；下一轮巡检自动读新表生效")
    print("⚠️ console / 手动抓拍的\"最近格点\"归属是**进程启动时的快照** ⇒ 换档后要"
          "重启 mushroom_patrol 才跟上")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="patrol-migrate", description="一次性数据迁移")
    sub = ap.add_subparsers(dest="cmd", required=True)

    z = sub.add_parser("z-frame", help="Z 坐标框架迁移：原点搬到顶端（−212…0，第 1 层在最上）")
    z.add_argument("--stations", default="/app/configs/stations.yaml")
    z.add_argument("--dry-run", action="store_true", help="只看换算结果，不落盘")
    z.set_defaults(func=cmd_z_frame)

    a = sub.add_parser("reanchor", help="重锚：按当前 col_y/layer_z 重算表的 (y, z)，框架不变")
    a.add_argument("--stations", default="/app/configs/stations.yaml")
    a.add_argument("--dry-run", action="store_true", help="只看换算结果，不落盘")
    a.add_argument("--view-stage", default=None,
                   help="观察高度档（票 16）：默认沿用表里声明的档；换档会重算 z 并清 trim_z")
    a.set_defaults(func=cmd_reanchor)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":       # pragma: no cover
    sys.exit(main())
