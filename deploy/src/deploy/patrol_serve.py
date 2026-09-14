"""触发请求与手动指令的消费方：**持有硬件的那一个进程**。

`console` 只负责收请求（写文件），不碰控制器；真正跑轮、真正动手的是这里。三件事必须
同时成立：

1. **单实例**。FMC4030 是单会话控制器：两个消费方同时跑，第二个连接会把第一个踢掉，
   那一轮 60 张图就废了。所以这个进程只允许一份（compose 里 `replicas: 1` + 不重复起）。
2. **空闲时几乎不耗资源**。轮询触发目录（默认 5 秒一次），没有请求就睡——不是常驻
   跑巡检，巡检由算法侧每 3 小时投一次请求驱动（ADR-0012 §6）。
3. **手动指令优先于触发请求**。来巡检请求时若操作者正在手动操作，先把这一条做完再
   开轮——反过来（把人的动作堵在 11 分钟的一轮后面）在现场是不可接受的。

为什么不做成"每轮一个容器"：那样每轮都要重新加载厂商 SDK、重建连接，而且两个容器
重叠的瞬间就可能双连接。一个常驻进程 + 单飞，是这台设备上最省心的形状。

## 巡检与手动为什么不会撞车（ADR-0013）

同一个线程：**跑轮的时候不领手动指令**。于是轮内提交的指令只有两种下场：

* 在本轮结束前 60 秒内提交的（`deploy.manual_exec.STALE_COMMAND_S`）会在轮末被领走
  并执行——操作者刚点的，执行是对的；
* 更早提交的（含整轮期间一直挂着的那条）一律判"已过期"拒绝——十分钟前点的"点动
  5mm"现在突然动一下，是最坏的一种惊喜。

更好的做法是**根本不让它提交进来**：console 在巡检进行中直接回 409
（`deploy.console` 的 `api_cmd`），这里的过期判定只是兜底：无论指令从哪条路进来，
都不会"十分钟后突然动一下"。
"""

from __future__ import annotations

import os
import signal
import time
from collections.abc import Callable
from datetime import datetime

from deploy.patrol_trigger import RunRequest, TriggerStore

DEFAULT_POLL_S = 5.0
#: 没有触发请求时，多久照看一次手动通道。人点了"点动"等 5 秒才动是不可接受的，
#: 而 0.5 秒一次 stat() 的开销可以忽略。
DEFAULT_MANUAL_POLL_S = 0.5


def serve_forever(
    *,
    store: TriggerStore,
    run_once: Callable[[], int],
    manual=None,
    poll_s: float = DEFAULT_POLL_S,
    manual_poll_s: float = DEFAULT_MANUAL_POLL_S,
    max_rounds: int | None = None,
    log: Callable[[str], None] = print,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> int:
    """轮询触发目录与手动通道；领到请求就跑一轮。返回跑过的轮数（`max_rounds` 用完即退）。

    ``manual`` 给的是 `deploy.manual_exec.ManualExecutor`（构造签名见那里）。不给它
    时行为与从前**完全一致**：只轮询触发目录、每次睡满 ``poll_s``。
    """
    stop = {"flag": False}

    def _on_signal(signum, _frame):        # pragma: no cover - 信号路径
        stop["flag"] = True
        log(f"收到信号 {signum}：本轮结束后退出")

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, _on_signal)
        except ValueError:                 # 非主线程（测试里）
            pass

    def _service_manual() -> bool:
        """照看一次手动通道。返回是否真的处理了一条指令。"""
        if manual is None:
            return False
        try:
            return manual.service_once() is not None
        except Exception as e:  # noqa: BLE001 - 一条指令的意外不能带走执行方
            log(f"手动指令处理异常（{type(e).__name__}: {e}）——继续等下一次触发")
            return False

    what = "触发目录" + (" + 手动通道" if manual is not None else "")
    log(f"巡检执行方就绪：轮询 {store.dir_path}（{what}，触发每 {poll_s:.0f} 秒），单实例")
    rounds = 0
    while not stop["flag"]:
        _service_manual()
        req: RunRequest | None = store.consume()
        if req is None:
            # 空闲等待：按 poll_s 的节奏等下一次触发，**中间继续照看手动通道**。
            deadline = clock() + poll_s
            while not stop["flag"] and clock() < deadline:
                if manual is None:
                    sleep(poll_s)       # 没接手动通道 ⇒ 一次睡满（老行为）
                    break
                if _service_manual():
                    break               # 刚给操作者做完一件事，顺手再看一眼触发目录
                sleep(min(manual_poll_s, max(0.0, deadline - clock())))
            continue
        log(f"领到巡检请求 {req.id}（来自 {req.created_by}"
            f"{'：' + req.reason if req.reason else ''}），开始跑一轮")
        started = datetime.now()
        try:
            rc = run_once()
        except Exception as e:  # noqa: BLE001 - 一轮炸了不能把执行方带走
            log(f"巡检请求 {req.id} 执行异常（{type(e).__name__}: {e}）——继续等下一次触发")
            continue
        rounds += 1
        spent = (datetime.now() - started).total_seconds()
        log(f"巡检请求 {req.id} 完成：rc={rc}，耗时 {spent:.0f} s")
        if max_rounds is not None and rounds >= max_rounds:
            log(f"已达 max_rounds={max_rounds}，退出")
            break
    log("巡检执行方已退出")
    return rounds


def main(argv: list[str] | None = None) -> int:
    """命令行入口（容器里的 `patrol-serve` 角色）。

    跑一轮用的就是 `deploy.m1 --once`：准入判定、软限位校验、急停链、取证与同步
    全都在那一条路径上，这里**不复制**任何一条——复制出来的第二份迟早会与第一份不一致。
    """
    import argparse

    from deploy.m1 import main as m1_main

    ap = argparse.ArgumentParser(prog="deploy.patrol_serve",
                                 description="触发请求与手动指令的执行方（单实例，持有控制器）")
    ap.add_argument("--trigger-dir", default=os.environ.get("PATROL_TRIGGER_DIR",
                                                            "/app/data/trigger"))
    ap.add_argument("--cmd-dir", default=os.environ.get("PATROL_CMD_DIR", "/app/data/cmd"),
                    help="手动指令通道目录（console 往这里写，执行方从这里领；"
                         "其中的 ESTOP 文件也会透给巡检那一轮）")
    ap.add_argument("--poll", type=float, default=float(os.environ.get("PATROL_POLL_S",
                                                                      DEFAULT_POLL_S)))
    ap.add_argument("--manual-poll", type=float,
                    default=float(os.environ.get("PATROL_MANUAL_POLL_S",
                                                 DEFAULT_MANUAL_POLL_S)))
    ap.add_argument("--no-manual", action="store_true",
                    help="不接手动通道（只跑巡检；排障时用来排除干扰）")
    ap.add_argument("--max-rounds", type=int, default=None, help="跑够几轮就退出（调试用）")
    # 其余参数原样透给 deploy.m1（--room/--stations/--outbox/--log/--capture-host/--ingest …）
    ap.add_argument("m1_args", nargs="*", help="透传给 deploy.m1 的参数")
    args = ap.parse_args(argv)

    store = TriggerStore(dir_path=args.trigger_dir)
    manual = None if args.no_manual else build_manual(args)

    # 跑一轮用的仍是 `deploy.m1 --once`（准入/软限位/急停链/取证全在那条路上），只把
    # **指令目录**显式带过去：两边必须看同一个 ESTOP 文件，否则"按了急停"拦不住正在
    # 跑的那一轮。操作者若自己传了 --cmd-dir，以他的为准。
    m1_args = list(args.m1_args)
    if "--cmd-dir" not in m1_args:
        m1_args += ["--cmd-dir", args.cmd_dir]

    rounds = serve_forever(
        store=store,
        run_once=lambda: m1_main([*m1_args, "--once"]),
        manual=manual,
        poll_s=args.poll,
        manual_poll_s=args.manual_poll,
        max_rounds=args.max_rounds,
    )
    return 0 if rounds >= 0 else 1


def build_manual(args):
    """按 `patrol-m1` 的同一套参数装配手动执行方（返回 `ManualExecutor`）。

    刻意**复用 m1 的参数解析**（`build_parser`）而不是另立一套 `--stations/--outbox/…`：
    同机两个进程读的必须是同一份站位表、同一份 outbox、同一份入库日期，参数名一旦
    分叉，迟早会出现"手动用的站位表和巡检的不是同一份"这种事。
    """
    from patrol.fmc import Fmc4030
    from patrol.fmc.loader import load_library
    from patrol.room import RoomStateError, load_room_state
    from patrol.store import JsonlStore

    from deploy.m1 import build_parser, load_station_list, make_capture, make_sync
    from deploy.manual import ManualChannel
    from deploy.manual_exec import ManualExecutor

    m1_args = build_parser().parse_args(list(args.m1_args))

    def log(msg: str) -> None:
        print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)

    # 厂商库**首次用到时才加载**：没人碰手动通道时不该白白加载它。
    sdk: dict = {"lib": None}

    def connect() -> Fmc4030:
        if sdk["lib"] is None:
            sdk["lib"] = load_library(m1_args.lib)
        return Fmc4030.connect(lib=sdk["lib"], device_id=m1_args.device,
                               ip=m1_args.ip, port=m1_args.port)

    # 站位表读不出来**不该拖垮手动移动**：机动（点动/回零/定位）根本不需要站位表，
    # 只有"抓拍归到哪个站位"需要。所以这里降级成空表 + 一句明说，让运维至少还能把
    # 机器挪回来。（巡检那边该失败还是会失败——它读的是同一份文件。）
    try:
        stations = load_station_list(m1_args.stations, camera_ip=m1_args.camera_ip, log_fn=log)
    except Exception as e:  # noqa: BLE001 - 降级，不退出
        stations = []
        log(f"! 站位表不可用（{type(e).__name__}: {e}）——手动移动仍可用，抓拍会被拒绝")

    def room_fields() -> dict:
        """库房维度（与巡检写入的行同源）：读不到就写 NULL，不编默认值。"""
        try:
            rs = load_room_state(m1_args.room)
        except RoomStateError:
            return {"room_id": None, "entry_date": None, "batch_no": None}
        return {"room_id": rs.room_id, "entry_date": rs.entry_date.isoformat(),
                "batch_no": rs.batch_no}

    store = JsonlStore(m1_args.outbox)
    sync = make_sync(m1_args)

    def flush() -> object:
        return sync.flush(store)

    return ManualExecutor(
        ManualChannel(args.cmd_dir),
        connect=connect,
        stations=stations,
        capture=make_capture(m1_args),
        append_index=store.append,
        flush=flush,
        room_fields=room_fields,
        log=log,
    )


if __name__ == "__main__":      # pragma: no cover
    import sys

    sys.exit(main())
