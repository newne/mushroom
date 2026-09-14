"""环境数据定时入库（票 03）。

用法::

    python -m analysis.env_ingest --db data.db --source csv --path ./env_csv --interval 300
    python -m analysis.env_ingest --db data.db --source csv --path ./env_csv --once
"""

from __future__ import annotations

import argparse
import time

from analysis.db import connect, upsert_environment
from analysis.env.adapters import CsvEnvSource, EnvSource, HttpEnvSource


def run_once(conn, source: EnvSource, since_ts: str | None = None) -> str | None:
    """拉取一批环境数据入库，返回本批最大 ts（作为增量游标）。"""
    rows = source.fetch_since(since_ts)
    if not rows:
        return since_ts
    upsert_environment(conn, [r.__dict__ for r in rows])
    return max(r.ts for r in rows)


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
    p.add_argument("--db", required=True, help="SQLite 路径")
    p.add_argument("--source", choices=["csv", "http"], default="csv")
    p.add_argument("--path", help="csv 源目录")
    p.add_argument("--url", help="http 源地址")
    p.add_argument("--interval", type=float, default=300.0, help="轮询间隔秒")
    p.add_argument("--once", action="store_true", help="只拉取一次后退出")
    args = p.parse_args(argv)

    source: EnvSource
    if args.source == "csv":
        if not args.path:
            p.error("--source csv 需要 --path")
        source = CsvEnvSource(args.path)
    else:
        if not args.url:
            p.error("--source http 需要 --url")
        source = HttpEnvSource(args.url)

    conn = connect(args.db)
    if args.once:
        cursor = run_once(conn, source)
        print(f"[env_ingest] 完成，游标: {cursor}")
    else:
        loop(conn, source, interval_s=args.interval)


if __name__ == "__main__":
    main()
