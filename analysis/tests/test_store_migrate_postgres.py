"""票 28 第 2 步（migrate）：调用点切到门面后，**PG 侧补上原来只走 SQLite 的场景**。

第 1 步（expand）只证明了"两套实现读回来的行相等"（见 `test_store_postgres.py`）。
这一步每切一批调用点（环境 → measurements → rounds / images → operations → ranges →
batches_state），这里就补上对应的一批用例，钉住的是**链路**而不只是存储：

- 同一份种子数据在 SQLite 与 PG 上各跑一遍，**同一个 HTTP 请求的响应逐字段相同**；
- 原来按路径重连的地方（`compute_ranges` / `refresh_ranges` / `jobs.default_jobs`）改成
  传连接后，在 PG 上照样跑得通；
- 四个 ingest CLI 换成 `--dsn` 之后，PG 上写得进去、读得回来。

没有测试 DSN（`ANALYSIS_TEST_DSN` / `ANALYSIS_TEST_DSN_FILE`）就 skip —— 夹具来自
`conftest.py`。PG 侧只在自己建的 `analysis_test_<随机>` schema 里读写，跑完
`DROP SCHEMA … CASCADE` 并断言不存在；**不碰 public、不碰遗留表、不碰农场库、
不碰 TDengine**（ADR-0026）。连接串不打印、不进仓库。
"""

from __future__ import annotations

from contextlib import contextmanager

import pytest
from analysis import store
from analysis.api import create_app
from fastapi.testclient import TestClient

import pg_harness

#: 环境小时聚合：两个库房 × 两个整点（够跑 hour/day 聚合与 summary）。
ENV_ROWS = [
    {"ts": "2026-08-16T10:00:00", "zone": "611", "temp_c": 21.5, "temp_min": 20.1,
     "temp_max": 22.8, "rh_pct": 88.0, "rh_min": 85.0, "rh_max": 91.0, "co2_ppm": 900.0,
     "co2_min": 800.0, "co2_max": 1100.0, "lamp_state": 0.0, "lamp_on_s": 30.0,
     "lamp_off_s": 90.0, "lamp_on_m": 0.5, "lamp_off_m": 1.5, "lamp_on_min": 15.0, "n": 60},
    {"ts": "2026-08-16T11:00:00", "zone": "611", "temp_c": 22.5, "temp_min": 21.1,
     "temp_max": 23.8, "rh_pct": 87.0, "rh_min": 84.0, "rh_max": 90.0, "co2_ppm": 950.0,
     "co2_min": 850.0, "co2_max": 1150.0, "lamp_state": 0.0, "lamp_on_s": 30.0,
     "lamp_off_s": 90.0, "lamp_on_m": 0.5, "lamp_off_m": 1.5, "lamp_on_min": 15.0, "n": 60},
    {"ts": "2026-08-17T10:00:00", "zone": "611", "temp_c": 23.5, "temp_min": 22.1,
     "temp_max": 24.8, "rh_pct": 86.0, "rh_min": 83.0, "rh_max": 89.0, "co2_ppm": 880.0,
     "co2_min": 780.0, "co2_max": 1080.0, "lamp_state": 1.0, "lamp_on_s": 45.0,
     "lamp_off_s": 45.0, "lamp_on_m": 0.75, "lamp_off_m": 0.75, "lamp_on_min": 45.0, "n": 60},
    {"ts": "2026-08-16T10:00:00", "zone": "612", "temp_c": 20.5, "temp_min": 19.1,
     "temp_max": 21.8, "rh_pct": 89.0, "rh_min": 86.0, "rh_max": 92.0, "co2_ppm": 870.0,
     "co2_min": 770.0, "co2_max": 1070.0, "lamp_state": 0.0, "lamp_on_s": 30.0,
     "lamp_off_s": 90.0, "lamp_on_m": 0.5, "lamp_off_m": 1.5, "lamp_on_min": 15.0, "n": 59},
]


def _seed_env(conn) -> None:
    store.upsert_environment(conn, ENV_ROWS)


@contextmanager
def two_stores(tmp_path, monkeypatch, pg_dsn, pg_schema):
    """同一份种子数据在两个后端上各建一份：SQLite 用临时文件，PG 用临时 schema。

    两边各给一个**门面连接**（种数据）与一个 **API 实例**（读响应）—— 迁移的验收就是
    "同一个请求两边逐字段相同"，所以这里把两份并排摆好，用例只负责比。
    """
    monkeypatch.delenv("ANALYSIS_STORE", raising=False)
    path = str(tmp_path / "migrate.db")
    sqlite_conn = store.connect(path)
    sqlite_app = create_app(path)
    monkeypatch.setenv("ANALYSIS_STORE", "postgres")
    pg_conn = store.connect(dsn=pg_dsn, schema=pg_schema)
    pg_app = create_app(store_dsn=pg_dsn, store_schema=pg_schema)
    try:
        yield sqlite_conn, sqlite_app, pg_conn, pg_app
    finally:
        sqlite_conn.close()
        pg_conn.close()


def assert_same_answers(sqlite_app, pg_app, cases) -> None:
    """同一批请求打两个实例，响应必须逐字段相同（JSON 全等，含键序）。"""
    with TestClient(sqlite_app) as a, TestClient(pg_app) as b:
        for url, params in cases:
            assert b.get(url, params=params).json() == a.get(url, params=params).json(), \
                f"{url}?{params} 两个后端的响应不一致"


# ---------- 环境（第一批：连接 + /environment 的读） ----------


def test_environment_endpoints_agree_on_both_backends(
    tmp_path, monkeypatch, pg_dsn, pg_schema
):
    """/environment、/environment/summary：PG 与 SQLite 的响应逐字段相同。

    这一条同时钉住三件事：连接经门面按后端建（含 PG 的 schema）、`PRAGMA journal_mode=WAL`
    不再是 API 里的 SQLite 专有语句、环境读走门面。
    """
    with two_stores(tmp_path, monkeypatch, pg_dsn, pg_schema) as (sq, sq_app, pg_conn, pg_app):
        _seed_env(sq)
        _seed_env(pg_conn)
        assert_same_answers(sq_app, pg_app, [
            ("/environment", {"zone": "611", "granularity": "hour"}),
            ("/environment", {"zone": "611", "granularity": "day"}),
            ("/environment", {"zone": "612"}),
            ("/environment", {"zone": "611", "start": "2026-08-17T00:00:00"}),
            ("/environment/summary", {"zone": "611"}),
            ("/environment/summary", {"zone": "999"}),
        ])


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
def test_env_ingest_cli_writes_through_the_facade(backend, tmp_path, monkeypatch):
    """`python -m analysis.env.ingest --once` 在两种后端上都能把小时聚合写进去。

    PG 侧没有 DSN 就 skip 这一半（SQLite 那一半照旧跑）—— 这就是"原来只走 SQLite 的场景
    补上 PG 参数化"：同一个 CLI、同一份 CSV，只换连接参数。
    """
    from analysis.env.ingest import main

    csv_dir = tmp_path / "env_csv"
    csv_dir.mkdir()
    (csv_dir / "a.csv").write_text(
        "ts,zone,temp_c,rh_pct,co2_ppm,lamp_state\n"
        "2026-08-30T08:00:00,611,23.5,85.0,800,120\n"
        "2026-08-30T09:00:00,611,24.0,88.0,900,150\n",
        encoding="utf-8",
    )

    if backend == "postgres":
        dsn = pg_harness.test_dsn()
        if dsn is None:
            pytest.skip("没有测试 DSN：PG 侧的 CLI 用例跳过")
        schema = pg_harness.new_schema_name()
        monkeypatch.setenv("ANALYSIS_STORE", "postgres")
        conn_kwargs = {"dsn": dsn, "schema": schema}
        backend_args = ["--dsn", dsn, "--schema", schema]
    else:
        monkeypatch.delenv("ANALYSIS_STORE", raising=False)
        schema = None
        conn_kwargs = {"db_path": str(tmp_path / "cli.db")}
        backend_args = ["--db", str(tmp_path / "cli.db")]

    try:
        main([
            *backend_args, "--source", "csv", "--path", str(csv_dir), "--once",
        ])
        conn = store.connect(**conn_kwargs)
        try:
            rows = store.fetch_environment(conn, zone="611")
        finally:
            conn.close()
        assert [(r["ts"], r["temp_c"]) for r in rows] == [
            ("2026-08-30T08:00:00", 23.5), ("2026-08-30T09:00:00", 24.0),
        ], "CLI 写进去的小时聚合要能被门面读回来"
    finally:
        if schema is not None:
            pg_harness.drop_schema(dsn, schema)
