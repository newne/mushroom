"""批次台账同步（pgsql → `analysis.batches`）。

用法::

    python -m analysis.batch_ingest --db mushrooms.db --once
    python -m analysis.batch_ingest --db mushrooms.db --interval 86400
    python -m analysis.batch_ingest --db mushrooms.db --daily-at 05:20
    ANALYSIS_STORE=postgres python -m analysis.batch_ingest --dsn <conninfo> --once

**落库走门面**（票 28 第 2 步）：SQLite 认 `--db`，PG 认 `--dsn`/`--schema`，后端由
`ANALYSIS_STORE` 选（缺省 sqlite）。**取数那条**（pgsql 台账）与落库是两件事，各管各的
连接串来源。

**取数已改走 pgsql**（用户 2026-10-14 裁定）：`tools_mysql` 是业务数据库、**禁止使用**；
旧的 `docker exec tools_mysql` 通路已下线。连接串走 `PGSQL_DSN_FILE`（文件优先）或 `PGSQL_DSN`，
细节见 `analysis/batches.py`。**宿主上那份旧拷贝已停用、待退役**——不要照它排查。

**常驻形态（票 12）**：启动先同步一次，然后按 `--interval` 每 N 秒、或按
`--daily-at HH:MM` 每天一次（容器本地时区）。全量覆盖 upsert（PK
`(room_id, entry_date)`）⇒ 多跑几次无害。单轮失败只打印、不退出循环 ——
**不许拿容器重启当调度**：线上 `mushroom-audit-ingest.service` 就是
`Type=simple` + `--once` + `Restart=always`，把设计的 10 分钟退化成 60 秒一次，
还持续打环控平台的鉴权接口。
"""

from __future__ import annotations

import argparse
import time
from datetime import datetime, timedelta

from analysis import store
from analysis.batches import fetch_batches_with_report

DEFAULT_DB = "mushrooms.db"


def run_once(conn, *, runner=None, map_path: str | None = None) -> int:
    """同步一次台账，返回条数。取不到就抛错——**不退回"按断档切段"**。

    用带报告的那条路径：会打印**来源差异**（只出现在一张表里的批次）与
    **缺 `batch_no` 映射**的计数 —— 差异与缺口都要看得见，不静默。

    **整体替换**（Chief 2026-10-14）：`batches` 是物化缓存，upsert 永不 prune ⇒
    陈旧行会永久留下（同一批次两种编号在页面上出两次）。替换在**一个事务**里做，
    被删的键逐条打印，并写 `batches_state` 供 `GET /batches` 给 `ledger_refreshed_at`/`stale`。
    """
    rows, report = fetch_batches_with_report(runner=runner, map_path=map_path)
    store.replace_batches(conn, rows, detail=report)
    return len(rows)


def seconds_until(hhmm: str, *, now: datetime | None = None) -> float:
    """到下一个 `HH:MM`（本地时区）还有多少秒；今天已过就算到明天。

    常驻循环用它对齐"每天 05:20"，而不是从容器启动时刻起算 24 小时 ——
    后者会让同步时刻随重启漂移。
    """
    hour, minute = (int(part) for part in hhmm.split(":"))
    now = now or datetime.now()
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds()


def loop(
    conn,
    *,
    interval_s: float | None = None,
    daily_at: str | None = None,
    runner=None,
    max_cycles: int | None = None,
    sleep=time.sleep,
) -> None:
    """常驻循环：**先同步一次再睡**（新容器当天也有台账）。`max_cycles` 只给测试用。"""
    if (interval_s is None) == (daily_at is None):
        raise ValueError("常驻模式要给且只给一种周期：--interval 或 --daily-at")
    cycles = 0
    while max_cycles is None or cycles < max_cycles:
        try:
            n = run_once(conn, runner=runner)
            print(f"[batch_ingest] {n} 个批次")
        except Exception as e:  # noqa: BLE001 - 取不到台账不中断循环，下一轮再试
            print(f"[batch_ingest] 同步失败，稍后重试: {e}")
        cycles += 1
        if max_cycles is not None and cycles >= max_cycles:
            break
        sleep(interval_s if interval_s is not None else seconds_until(daily_at))


def _daily_at(value: str) -> str:
    try:
        hour, minute = (int(part) for part in value.split(":"))
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError
    except ValueError:
        raise argparse.ArgumentTypeError("--daily-at 要 HH:MM（24 小时制，本地时区）")
    return value


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description="批次台账同步")
    p.add_argument("--db", default=DEFAULT_DB, help=store.CLI_DB_HELP)
    p.add_argument("--dsn", help=store.CLI_DSN_HELP)
    p.add_argument("--schema", help=store.CLI_SCHEMA_HELP)
    p.add_argument("--once", action="store_true", help="同步一次后退出（与其它 ingest 同名参数）")
    p.add_argument("--interval", type=float, help="常驻：每 N 秒同步一次")
    p.add_argument("--daily-at", type=_daily_at, help="常驻：每天 HH:MM 同步一次")
    args = p.parse_args(argv)

    conn = store.connect(args.db, dsn=args.dsn, schema=args.schema)
    if args.interval or args.daily_at:
        loop(conn, interval_s=args.interval, daily_at=args.daily_at)
        return
    n = run_once(conn)
    print(f"[batch_ingest] 完成，{n} 个批次")


if __name__ == "__main__":
    main()
