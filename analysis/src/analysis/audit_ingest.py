"""操作日志入库（环控平台命令审计 → analysis.operations）。

用法::

    python -m analysis.audit_ingest --db mushrooms.db --url http://10.77.77.39 \
        --account 18912345678 --password-md5 <md5> --once
    python -m analysis.audit_ingest --db mushrooms.db --loop --interval 300
    ANALYSIS_STORE=postgres python -m analysis.audit_ingest --dsn <conninfo> --once

**存储走门面**（票 28 第 2 步）：SQLite 认 `--db`，PG 认 `--dsn`/`--schema`，后端由
`ANALYSIS_STORE` 选（缺省 sqlite）—— 所以 `--db` 不再必填（PG 下它不参与）。

口令只接受 **MD5**（平台口径，明文会回 400200）。凭据从参数或环境变量取：
`EMS_URL` / `EMS_ACCOUNT` / `EMS_PASSWORD_MD5`——**不进仓库**。
"""

from __future__ import annotations

import argparse
import os
import time

from analysis import store
from analysis.audit import fetch_operations, login, window

DEFAULT_URL = "http://10.77.77.39"


def run_once(
    conn,
    *,
    base_url: str,
    account: str,
    password_md5: str,
    lookback_minutes: float = 90.0,
    transport=None,
) -> int:
    """拉一个窗口的操作日志并入库，返回条数。"""
    start, end = window(int(lookback_minutes))
    token = login(base_url, account, password_md5, transport=transport)
    rows = fetch_operations(base_url, token, start, end, transport=transport)
    if rows:
        store.upsert_operations(conn, rows)
    return len(rows)


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description="操作日志入库（环控平台命令审计）")
    p.add_argument("--db", help=store.CLI_DB_HELP)
    p.add_argument("--dsn", help=store.CLI_DSN_HELP)
    p.add_argument("--schema", help=store.CLI_SCHEMA_HELP)
    p.add_argument("--url", default=os.environ.get("EMS_URL", DEFAULT_URL))
    p.add_argument("--account", default=os.environ.get("EMS_ACCOUNT", ""))
    p.add_argument("--password-md5", default=os.environ.get("EMS_PASSWORD_MD5", ""))
    p.add_argument("--lookback", type=float, default=90.0, help="回溯分钟数")
    p.add_argument("--interval", type=float, default=300.0, help="轮询间隔秒")
    p.add_argument("--once", action="store_true", help="只拉一次后退出")
    args = p.parse_args(argv)

    if not args.account or not args.password_md5:
        p.error("需要 --account 与 --password-md5（或环境变量 EMS_ACCOUNT / EMS_PASSWORD_MD5）")

    conn = store.connect(args.db, dsn=args.dsn, schema=args.schema)
    if args.once:
        n = run_once(conn, base_url=args.url, account=args.account,
                     password_md5=args.password_md5, lookback_minutes=args.lookback)
        print(f"[audit_ingest] 完成，{n} 条")
        return
    while True:
        try:
            n = run_once(conn, base_url=args.url, account=args.account,
                         password_md5=args.password_md5, lookback_minutes=args.lookback)
            print(f"[audit_ingest] {n} 条")
        except Exception as e:  # noqa: BLE001 - 平台抖动不中断循环
            print(f"[audit_ingest] 拉取失败，稍后重试: {e}")
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
