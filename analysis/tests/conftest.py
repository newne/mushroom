"""测试装置：常驻作业的环境变量在每个用例前清空；**整套件跑在 PostgreSQL 上**（票 28 contract）。

票 12 的装配口径是"设了周期变量就跑"（生产靠 compose 给）。测试机上若有人顺手
`$env:ENV_INGEST_INTERVAL=300`，整套 API 测试就会去连 TDengine / 环控平台 ——
本文件把相关变量清掉，让测试保持封闭。

**没有测试 DSN 就整组 skip**（票 28 §4.2）：SQLite 已从服务代码里删除，测试**不回退
临时文件库** —— 能跑的只有 PG。测试专用变量是 `ANALYSIS_TEST_DSN` / `ANALYSIS_TEST_DSN_FILE`
（**故意不复用** `PGSQL_DSN*`：那两个是应用跑生产用的，本机 shell 里挂着生产 DSN 时，
跑一次测试不该往生产库里建 schema）。

**每个用例一个独立 schema**（`analysis_test_<随机>`），跑完 `DROP SCHEMA … CASCADE` 并断言
它不存在；连接串只在本进程里用，不打印、不进仓库。`pg`（连接）与 `client`（API）默认落在
**同一个** schema 上：用例可以一边用 API 写、一边用连接核库表。
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from analysis import store
from analysis.api import create_app

import pg_harness

#: 常驻作业的开关也一并清掉（票 12）：测试不该被开发机上顺手设的变量带跑（那会去连
#: TDengine / 环控平台）。`ANALYSIS_PG_SCHEMA` 也清：schema 由夹具显式给，不靠环境。
INGEST_ENV = (
    "ENV_INGEST_INTERVAL", "AUDIT_INTERVAL", "BATCH_AT", "RANGES_AT", "ANALYSIS_PG_SCHEMA",
)


@pytest.fixture(autouse=True)
def _no_ingest_jobs(monkeypatch):
    for name in INGEST_ENV:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(scope="session")
def pg_dsn():
    """测试专用连接串；两个变量都没设 ⇒ `pytest.skip`（**说明原因，不回退文件库**）。"""
    dsn = pg_harness.test_dsn()
    if dsn is None:
        pytest.skip(
            f"没有 {pg_harness.DSN_ENV} / {pg_harness.DSN_FILE_ENV}："
            "本套件只在 PostgreSQL 上跑（票 28 contract 已删 SQLite 依赖，不回退临时文件库）"
        )
    return dsn


@pytest.fixture(scope="session", autouse=True)
def _suite_is_pg_only(pg_dsn):
    """**整套件**都要 PG：没有测试 DSN 时每条用例都 skip，而不是"一半照跑"。

    挂上 `pg_dsn` 就够：缺 DSN 时它在 setup 阶段 skip，autouse 让每一条用例都先过它这一关。
    """


@pytest.fixture
def pg_schema(pg_dsn):
    """每个用例一个独立 schema，跑完必删并断言不存在（票 28 的 PG 卫生硬门）。"""
    name = pg_harness.new_schema_name()
    yield name
    pg_harness.drop_schema(pg_dsn, name)


@pytest.fixture
def pg(pg_dsn, pg_schema):
    """经**存储层**拿 PG 连接（与 `api.create_app` 同一条路径）；用例直接读写它。"""
    conn = store.connect(dsn=pg_dsn, schema=pg_schema)
    try:
        yield conn
    finally:
        conn.close()


@pytest.fixture
def app(pg_dsn, pg_schema) -> FastAPI:
    """被测 API；连的 schema 与本用例的 `pg` 是**同一个**（夹具链共享 `pg_schema`）。"""
    return create_app(store_dsn=pg_dsn, store_schema=pg_schema)


@pytest.fixture
def client(app) -> Iterator[TestClient]:
    """本用例的 API 客户端。

    **不预先进 lifespan**：多数用例只关心端点，直接 `client.get(…)` 就行；
    要跑常驻作业的用例自己 `with client as c:`（进入/退出由它管）。跨请求共享一份
    连接，行为与容器里一致。
    """
    c = TestClient(app)
    try:
        yield c
    finally:
        c.close()


@pytest.fixture
def catalog(pg_dsn):
    """**独立连接**（不经过被测对象）查目录/核对用，避免"自己验自己"。"""
    with pg_harness.connect(pg_dsn) as conn, conn.cursor() as cur:
        yield cur
