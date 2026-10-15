"""测试卫生：常驻作业的环境变量在每个用例前清空；PG 用例的夹具（票 28）。

票 12 的装配口径是"设了周期变量就跑"（生产靠 compose 给）。测试机上若有人顺手
`$env:ENV_INGEST_INTERVAL=300`，整套 API 测试就会去连 TDengine / 环控平台 ——
本文件把相关变量清掉，让测试保持封闭。
"""

from __future__ import annotations

import pytest

from analysis import pg_store, store

import pg_harness

#: 存储后端开关也一并清掉（票 28）：缺省必须是 SQLite，测试不该被开发机上顺手设的
#: `ANALYSIS_STORE=postgres` 带跑（那会去找 pgsql）。PG 用例**显式**传 DSN 与 schema，
#: 不依赖这两个变量。
INGEST_ENV = (
    "ENV_INGEST_INTERVAL", "AUDIT_INTERVAL", "BATCH_AT", "RANGES_AT", "ANALYSIS_DB",
    "ANALYSIS_STORE", "ANALYSIS_PG_SCHEMA",
)


@pytest.fixture(autouse=True)
def _no_ingest_jobs(monkeypatch):
    for name in INGEST_ENV:
        monkeypatch.delenv(name, raising=False)


# ---------- 票 28：PG 用例的夹具 ----------
#
# 需要 DSN 的用例都挂在 `pg_dsn` 上：没设测试专用变量 ⇒ **skip**（缺省的 SQLite 套件照旧全绿）。
# 连接串的解析、schema 的建/删只有一处实现（`pg_harness`），用例不各写一遍。


@pytest.fixture(scope="session")
def pg_dsn():
    """测试专用连接串（`ANALYSIS_TEST_DSN` / `ANALYSIS_TEST_DSN_FILE`）；没设就跳过。"""
    dsn = pg_harness.test_dsn()
    if dsn is None:
        pytest.skip(
            f"没有 {pg_harness.DSN_ENV} / {pg_harness.DSN_FILE_ENV}：PG 用例整组跳过"
        )
    return dsn


@pytest.fixture
def pg_schema(pg_dsn):
    """每个用例一个独立 schema，跑完必删并断言不存在（票 28 的 PG 卫生硬门）。"""
    name = pg_harness.new_schema_name()
    yield name
    pg_harness.drop_schema(pg_dsn, name)


@pytest.fixture
def pg(pg_dsn, pg_schema, monkeypatch):
    """经**门面**拿 PG 连接（开关打开），与生产调用路径一致。"""
    monkeypatch.setenv("ANALYSIS_STORE", "postgres")
    assert store.backend() == "postgres"
    conn = store.connect(dsn=pg_dsn, schema=pg_schema)
    try:
        yield conn
    finally:
        conn.close()


@pytest.fixture
def catalog(pg_dsn):
    """**独立连接**（不经过被测对象）查目录/核对用，避免"自己验自己"。"""
    with pg_harness.connect(pg_dsn) as conn, conn.cursor() as cur:
        yield cur
