"""环境数据定时入库（票 03）。

用法::

    python -m analysis.env.ingest --db data.db --source csv --path ./env_csv --interval 300
    python -m analysis.env.ingest --db data.db --source csv --path ./env_csv --once
    ANALYSIS_STORE=postgres python -m analysis.env.ingest --dsn <conninfo> --source tdengine --url ...

**存储走门面**（票 28 第 2 步）：连接参数按后端分——SQLite 认 `--db`，PG 认 `--dsn`/`--schema`，
后端由 `ANALYSIS_STORE` 选（缺省 sqlite）。所以 `--db` 不再必填：PG 下它不参与。
"""

from __future__ import annotations

import argparse
import os
import time

from analysis import store
from analysis.env.adapters import CsvEnvSource, EnvSource, HttpEnvSource
from analysis.env.tdengine import TdengineEnvSource
from analysis.environment import aggregate_hourly


def hour_start(ts: str) -> str:
    return ts[:13] + ":00:00"


def run_once(conn, source: EnvSource, since_ts: str | None = None) -> str | None:
    """拉一批采样、折成小时聚合入库，返回下一轮的游标。

    **游标退到整点**：一小时的聚合必须由该小时的全部采样算出来。若只拉"游标之后"的
    新增行，当前这个小时的均值会被算小——每次都从头重算这一小时（最多 60 行）才对。
    """
    rows = source.fetch_since(since_ts)
    if not rows:
        return since_ts
    store.upsert_environment(conn, aggregate_hourly([r.__dict__ for r in rows]))
    return hour_start(max(r.ts for r in rows))


def loop(conn, source: EnvSource, *, interval_s: float = 300.0, max_cycles: int | None = None) -> None:
    since: str | None = None
    cycles = 0
    while max_cycles is None or cycles < max_cycles:
        try:
            since = run_once(conn, source, since)
        except Exception as e:  # noqa: BLE001 - 网络/接口抖动不中断循环
            print(f"[env_ingest] 拉取失败，稍后重试: {e}")
        cycles += 1
        time.sleep(interval_s)


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description="环境数据定时入库（票 03）")
    p.add_argument("--db", help=store.CLI_DB_HELP)
    p.add_argument("--dsn", help=store.CLI_DSN_HELP)
    p.add_argument("--schema", help=store.CLI_SCHEMA_HELP)
    p.add_argument("--source", choices=["csv", "http", "tdengine"], default="csv")
    p.add_argument("--path", help="csv 源目录")
    p.add_argument("--url", help="http 源地址；tdengine 源给 REST 根地址，如 http://127.0.0.1:6041")
    p.add_argument("--database", default="data1", help="tdengine 库名")
    p.add_argument("--rooms", help="tdengine 源要拉的库房号，逗号分隔；默认四个都拉")
    p.add_argument("--user", default=os.environ.get("TDENGINE_USER", "root"))
    p.add_argument("--password", default=os.environ.get("TDENGINE_PASSWORD", "taosdata"))
    p.add_argument("--lookback", type=float, default=120.0, help="无游标时的回溯分钟数")
    p.add_argument("--interval", type=float, default=300.0, help="轮询间隔秒")
    p.add_argument("--once", action="store_true", help="只拉取一次后退出")
    args = p.parse_args(argv)

    source: EnvSource
    if args.source == "csv":
        if not args.path:
            p.error("--source csv 需要 --path")
        source = CsvEnvSource(args.path)
    elif args.source == "http":
        if not args.url:
            p.error("--source http 需要 --url")
        source = HttpEnvSource(args.url)
    else:
        if not args.url:
            p.error("--source tdengine 需要 --url")
        source = TdengineEnvSource(
            args.url,
            database=args.database,
            rooms=[r.strip() for r in args.rooms.split(",")] if args.rooms else None,
            user=args.user,
            password=args.password,
            lookback_minutes=int(args.lookback),
        )

    conn = store.connect(args.db, dsn=args.dsn, schema=args.schema)
    if args.once:
        cursor = run_once(conn, source)
        print(f"[env_ingest] 完成，游标: {cursor}")
    else:
        loop(conn, source, interval_s=args.interval)


if __name__ == "__main__":
    main()
