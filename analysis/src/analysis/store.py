"""数据访问门面（票 28 第 1 步 expand）：SQLite 与 PostgreSQL 两套实现并存，由开关选择。

**这一步不删 SQLite**：默认后端仍是 SQLite，只有 `ANALYSIS_STORE=postgres` 才切到 PG。
门面只做一件事——**同名同签名地转发**给 `analysis.db`（SQLite）或 `analysis.pg_store`（PG）。
于是第 2 步（migrate）里每个模块的切换是"改一行 import"：把
`from analysis.db import …` 换成 `from analysis.store import …`，一轮一个提交、CI 保持绿；
第 4 步（contract）再删掉 SQLite 实现与这个开关。

三条纪律：

1. **开关值不认识就报错，绝不静默退回 SQLite**。静默退回意味着业务数据写进了容器里的
   本地文件，而运维以为在 PG —— 要等人发现"页面怎么没数据"才暴露。
2. **连接类型不认识也报错**，不去猜调用方想用哪个后端。
3. **回滚就是把开关切回 `sqlite`**（配旧镜像），不需要改代码。

调用方拿到的连接就是这个开关的产物：SQLite 给 `sqlite3.Connection`，PG 给
`analysis.pg_store.PgConnection`；两边的读写函数都在本模块，形状（返回的 dict 键、
排序、默认值）由 `analysis.db` 的字段清单与读侧派生规则定义。
"""

from __future__ import annotations

import os
import sqlite3

from analysis import db as _sqlite
from analysis import pg_store as _pg
from analysis.store_config import StoreConfigError

#: 开关：不设 = SQLite（现状），`postgres` = PG。回滚就是改回这一个变量。
DEFAULT_BACKEND_ENV = "ANALYSIS_STORE"
BACKEND_SQLITE = "sqlite"
BACKEND_POSTGRES = "postgres"
BACKENDS = (BACKEND_SQLITE, BACKEND_POSTGRES)

#: SQLite 后端的库路径口径与 `analysis.api.create_app` 一致（容器里由 `ANALYSIS_DB` 给）。
DEFAULT_DB_PATH_ENV = "ANALYSIS_DB"
DEFAULT_DB_PATH = "mushrooms.db"

#: 四个 ingest CLI（env / audit / batch / batch_map）共用的连接参数说明。
#: 后端由 `ANALYSIS_STORE` 选，所以两个参数各管一个后端：SQLite 认 `--db`、PG 认 `--dsn`。
CLI_DB_HELP = (
    f"SQLite 库路径（缺省取 {DEFAULT_DB_PATH_ENV}，再缺省 {DEFAULT_DB_PATH}）；"
    f"只在 {DEFAULT_BACKEND_ENV} 缺省/{BACKEND_SQLITE} 时参与"
)
CLI_DSN_HELP = (
    f"PG 连接串；缺省走 PGSQL_DSN_FILE（文件优先）/ PGSQL_DSN。"
    f"只在 {DEFAULT_BACKEND_ENV}={BACKEND_POSTGRES} 时参与。**别写进命令行历史/仓库**"
)
CLI_SCHEMA_HELP = (
    f"PG schema（缺省取 {_pg.DEFAULT_SCHEMA_ENV}，再缺省 {_pg.DEFAULT_SCHEMA}）"
)

__all__ = [
    "BACKENDS", "BACKEND_POSTGRES", "BACKEND_SQLITE", "DEFAULT_BACKEND_ENV",
    "StoreConfigError", "backend", "connect", "migrate", "prepare_connection",
    "upsert_environment", "fetch_environment",
    "upsert_operations", "fetch_operations", "fetch_latest_operations_before",
    "upsert_batches", "replace_batches", "fetch_batches", "fetch_batches_state",
    "insert_measurements", "fetch_measurements",
    "insert_images", "fetch_images", "fetch_room_box_ids",
    "insert_rounds", "fetch_rounds",
    "replace_ranges", "fetch_ranges", "fetch_advice_ranges",
    "write_ranges_state", "fetch_ranges_state",
]


def backend() -> str:
    """本次进程用哪套实现。缺省 `sqlite`；值不认识就报错（**不退回**）。"""
    raw = (os.environ.get(DEFAULT_BACKEND_ENV) or "").strip().lower()
    if not raw:
        return BACKEND_SQLITE
    if raw not in BACKENDS:
        raise StoreConfigError(
            f"{DEFAULT_BACKEND_ENV}={raw!r} 不认识：只收 {' / '.join(BACKENDS)}"
            f"（不设即 {BACKEND_SQLITE}）。**不静默退回另一套实现**"
        )
    return raw


def connect(
    db_path: str | None = None, *, check_same_thread: bool = True,
    dsn: str | None = None, schema: str | None = None,
):
    """按开关建连接并保证库结构就位。

    - SQLite：用 `db_path`（缺省取 `ANALYSIS_DB`，再缺省 `mushrooms.db`），建表 + 补列；
    - PG：用 `dsn`/`schema`（缺省走 `PGSQL_DSN_FILE`/`PGSQL_DSN` 与 `ANALYSIS_PG_SCHEMA`），
      建 schema + 建表。**`db_path` 在 PG 后端不参与** —— 迁移期调用方还在按老形状传它，
      传了不该报错（第 4 步会连同 `ANALYSIS_DB` 一起删掉）。
    """
    if backend() == BACKEND_POSTGRES:
        return _pg.connect(dsn=dsn, schema=schema)
    return _sqlite.connect(
        db_path or os.environ.get(DEFAULT_DB_PATH_ENV, DEFAULT_DB_PATH),
        check_same_thread=check_same_thread,
    )


def prepare_connection(conn) -> None:
    """把连接调成后端各自的「共享形态」（API 一份连接给请求线程与作业线程用）。

    调用方说的是「我要共享它」，**怎么做由后端决定**：SQLite 开 WAL（多读一写），
    PG 无事可做（MVCC）。这是 `api.create_app` 里那句 SQLite 专有
    `PRAGMA journal_mode=WAL` 的落点（票 28 第 2 步的挡路点 1）。
    """
    _impl(conn).prepare_connection(conn)


def _impl(conn):
    """按连接类型选实现。**不认识就报错**，不去猜。"""
    if isinstance(conn, _pg.PgConnection):
        return _pg
    if isinstance(conn, sqlite3.Connection):
        return _sqlite
    raise TypeError(
        f"不认识的连接类型：{type(conn).__name__}"
        "（要 sqlite3.Connection 或 analysis.pg_store.PgConnection；"
        "别把裸 psycopg 连接传进来——schema 挂在 PgConnection 上）"
    )


def migrate(conn) -> list[str]:
    """把库结构补到当前形状（幂等）。PG 侧的 schema 在 `connect` 时就建好了。"""
    impl = _impl(conn)
    if impl is _pg:
        return _pg.ensure_schema(conn)
    return _sqlite.migrate(conn)


# ---------- environment ----------


def upsert_environment(conn, rows: list[dict]) -> None:
    _impl(conn).upsert_environment(conn, rows)


def fetch_environment(
    conn, *, zone: str | None = None, start: str | None = None, end: str | None = None
) -> list[dict]:
    return _impl(conn).fetch_environment(conn, zone=zone, start=start, end=end)


# ---------- operations ----------


def upsert_operations(conn, rows: list[dict]) -> None:
    _impl(conn).upsert_operations(conn, rows)


def fetch_operations(
    conn, *, zone: str | None = None, start: str | None = None, end: str | None = None,
    limit: int | None = None,
) -> list[dict]:
    return _impl(conn).fetch_operations(conn, zone=zone, start=start, end=end, limit=limit)


def fetch_latest_operations_before(conn, *, zone: str, before: str) -> list[dict]:
    return _impl(conn).fetch_latest_operations_before(conn, zone=zone, before=before)


# ---------- batches ----------


def upsert_batches(conn, rows: list[dict]) -> None:
    _impl(conn).upsert_batches(conn, rows)


def replace_batches(
    conn, rows: list[dict], *, refreshed_at: str | None = None, detail: dict | None = None
) -> dict:
    return _impl(conn).replace_batches(conn, rows, refreshed_at=refreshed_at, detail=detail)


def fetch_batches(conn, *, room_id: str | None = None) -> list[dict]:
    return _impl(conn).fetch_batches(conn, room_id=room_id)


def fetch_batches_state(conn) -> dict:
    return _impl(conn).fetch_batches_state(conn)


# ---------- measurements ----------


def insert_measurements(conn, rows: list[dict]) -> None:
    _impl(conn).insert_measurements(conn, rows)


def fetch_measurements(
    conn, *, room_id: str | None = None, box_id: str | None = None
) -> list[dict]:
    return _impl(conn).fetch_measurements(conn, room_id=room_id, box_id=box_id)


# ---------- images / rounds ----------


def insert_images(conn, rows: list[dict]) -> None:
    _impl(conn).insert_images(conn, rows)


def fetch_images(
    conn, *, station_id: str | None = None, box_id: str | None = None,
    room_id: str | None = None, round_id: str | None = None, limit: int | None = None,
) -> list[dict]:
    return _impl(conn).fetch_images(
        conn, station_id=station_id, box_id=box_id, room_id=room_id, round_id=round_id,
        limit=limit,
    )


def fetch_room_box_ids(conn, *, room_id: str) -> list[str]:
    return _impl(conn).fetch_room_box_ids(conn, room_id=room_id)


def insert_rounds(conn, rows: list[dict]) -> None:
    _impl(conn).insert_rounds(conn, rows)


def fetch_rounds(
    conn, *, room_id: str | None = None, entry_date: str | None = None,
    batch_no: str | None = None, limit: int | None = None,
) -> list[dict]:
    return _impl(conn).fetch_rounds(
        conn, room_id=room_id, entry_date=entry_date, batch_no=batch_no, limit=limit
    )


# ---------- ranges ----------


def replace_ranges(conn, rows: list[dict], computed_at: str) -> None:
    _impl(conn).replace_ranges(conn, rows, computed_at)


def fetch_ranges(conn) -> list[dict]:
    return _impl(conn).fetch_ranges(conn)


def fetch_advice_ranges(conn) -> list[dict]:
    return _impl(conn).fetch_advice_ranges(conn)


def write_ranges_state(
    conn, *, state: str, state_text: str, computed_at: str | None = None,
    detail: dict | None = None, flags: list[str] | None = None, n_rows: int = 0,
    metric: str | None = None, unit: str | None = None, updated_at: str | None = None,
) -> None:
    _impl(conn).write_ranges_state(
        conn, state=state, state_text=state_text, computed_at=computed_at, detail=detail,
        flags=flags, n_rows=n_rows, metric=metric, unit=unit, updated_at=updated_at,
    )


def fetch_ranges_state(conn) -> dict:
    return _impl(conn).fetch_ranges_state(conn)
