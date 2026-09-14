"""触发请求的消费方：**持有硬件的那一个进程**。

`console` 只负责收请求（写文件），不碰控制器；真正跑轮的是这里。两件事必须同时成立：

1. **单实例**。FMC4030 是单会话控制器：两个消费方同时跑，第二个连接会把第一个踢掉，
   那一轮 60 张图就废了。所以这个进程只允许一份（compose 里 `replicas: 1` + 不重复起）。
2. **空闲时几乎不耗资源**。轮询触发目录（默认 5 秒一次），没有请求就睡——不是常驻
   跑巡检，巡检由算法侧每 3 小时投一次请求驱动（ADR-0012 §6）。

为什么不做成"每轮一个容器"：那样每轮都要重新加载厂商 SDK、重建连接，而且两个容器
重叠的瞬间就可能双连接。一个常驻进程 + 单飞，是这台设备上最省心的形状。
"""

from __future__ import annotations

import os
import signal
import time
from collections.abc import Callable
from datetime import datetime

from deploy.patrol_trigger import RunRequest, TriggerStore

DEFAULT_POLL_S = 5.0


def serve_forever(
    *,
    store: TriggerStore,
    run_once: Callable[[], int],
    poll_s: float = DEFAULT_POLL_S,
    max_rounds: int | None = None,
    log: Callable[[str], None] = print,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    """轮询触发目录；领到请求就跑一轮。返回跑过的轮数（`max_rounds` 用完即退）。"""
    stop = {"flag": False}

    def _on_signal(signum, _frame):        # pragma: no cover - 信号路径
        stop["flag"] = True
        log(f"收到信号 {signum}：本轮结束后退出")

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, _on_signal)
        except ValueError:                 # 非主线程（测试里）
            pass

    log(f"巡检执行方就绪：轮询 {store.dir_path}（每 {poll_s:.0f} 秒），单实例")
    rounds = 0
    while not stop["flag"]:
        req: RunRequest | None = store.consume()
        if req is None:
            sleep(poll_s)
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
                                 description="触发请求的执行方（单实例，持有控制器）")
    ap.add_argument("--trigger-dir", default=os.environ.get("PATROL_TRIGGER_DIR",
                                                            "/app/data/trigger"))
    ap.add_argument("--poll", type=float, default=float(os.environ.get("PATROL_POLL_S",
                                                                      DEFAULT_POLL_S)))
    ap.add_argument("--max-rounds", type=int, default=None, help="跑够几轮就退出（调试用）")
    # 其余参数原样透给 deploy.m1（--room/--stations/--outbox/--log/--capture-host/--ingest …）
    ap.add_argument("m1_args", nargs="*", help="透传给 deploy.m1 的参数")
    args = ap.parse_args(argv)

    store = TriggerStore(dir_path=args.trigger_dir)
    rounds = serve_forever(
        store=store,
        run_once=lambda: m1_main([*args.m1_args, "--once"]),
        poll_s=args.poll,
        max_rounds=args.max_rounds,
    )
    return 0 if rounds >= 0 else 1


if __name__ == "__main__":      # pragma: no cover
    import sys

    sys.exit(main())
