"""存储门面（票 28 第 1 步 expand）：开关、同名同签名的转发、连接类型的守卫。

本步**不删 SQLite**：默认仍是 SQLite，`ANALYSIS_STORE=postgres` 才切到 PG。
两份实现并存的意义是"切开关不改调用方"，所以这里钉住三件事：

1. 缺省后端是 SQLite，且门面的读写**落到与 `analysis.db` 同一份存储**；
2. 开关值不认识就**报错**，绝不静默退回 SQLite —— 静默退回意味着业务数据写进容器里的
   本地文件，而运维以为在 PG，要等人发现"页面怎么没数据"才暴露；
3. 连接类型不认识也**报错**，不去猜调用方想用哪个后端。
"""

from __future__ import annotations

import sqlite3

import pytest
from analysis import db, pg_store, store, store_config

ENV_ROW = {
    "ts": "2026-08-16T10:00:00", "zone": "611",
    "temp_c": 21.5, "temp_min": 20.1, "temp_max": 22.8,
    "rh_pct": 88.0, "rh_min": 85.0, "rh_max": 91.0,
    "co2_ppm": 900.0, "co2_min": 800.0, "co2_max": 1100.0,
    "lamp_state": 0.0, "lamp_on_s": 30.0, "lamp_off_s": 90.0,
    "lamp_on_m": 0.5, "lamp_off_m": 1.5, "lamp_on_min": 15.0, "n": 60,
}


def _without_dsn(monkeypatch):
    """把连接串来源全清掉：本文件里的用例都**不该**去连任何库。"""
    for name in ("PGSQL_DSN", "PGSQL_DSN_FILE"):
        monkeypatch.delenv(name, raising=False)


# ---------- 开关 ----------


def test_default_backend_is_sqlite(tmp_path, monkeypatch):
    _without_dsn(monkeypatch)
    monkeypatch.delenv("ANALYSIS_STORE", raising=False)
    assert store.backend() == "sqlite"
    conn = store.connect(str(tmp_path / "t.db"))
    try:
        assert isinstance(conn, sqlite3.Connection)
    finally:
        conn.close()


def test_switch_value_selects_the_backend_explicitly(tmp_path, monkeypatch):
    _without_dsn(monkeypatch)
    monkeypatch.setenv("ANALYSIS_STORE", "sqlite")
    assert store.backend() == "sqlite"
    conn = store.connect(str(tmp_path / "t.db"))
    conn.close()


def test_unknown_switch_value_is_an_error_not_a_silent_fallback(tmp_path, monkeypatch):
    """`postgresql` 是最容易写出来的笔误：认不出来就必须报错。"""
    _without_dsn(monkeypatch)
    monkeypatch.setenv("ANALYSIS_STORE", "postgresql")
    with pytest.raises(store.StoreConfigError, match="ANALYSIS_STORE"):
        store.backend()
    with pytest.raises(store.StoreConfigError):
        store.connect(str(tmp_path / "t.db"))


def test_postgres_without_a_dsn_fails_loudly_instead_of_falling_back(tmp_path, monkeypatch):
    """切到 PG 却没给连接串 ⇒ 报错。**不许**退回 SQLite 本地文件。"""
    _without_dsn(monkeypatch)
    monkeypatch.setenv("ANALYSIS_STORE", "postgres")
    with pytest.raises(store.StoreConfigError, match="PGSQL_DSN"):
        store.connect(str(tmp_path / "t.db"))


def test_db_path_defaults_to_the_analysis_db_env(tmp_path, monkeypatch):
    """容器里靠 `ANALYSIS_DB` 给路径（与 `analysis.api.create_app` 同一口径）。"""
    _without_dsn(monkeypatch)
    target = tmp_path / "from-env.db"
    monkeypatch.setenv("ANALYSIS_DB", str(target))
    conn = store.connect()
    try:
        assert target.exists()
    finally:
        conn.close()


# ---------- 同名同签名的转发（SQLite 侧） ----------


def test_facade_writes_through_to_the_storage_the_db_module_reads(tmp_path, monkeypatch):
    """门面不是第二份存储：经它写的数据，`analysis.db` 用同一条 SQL 读得到。"""
    _without_dsn(monkeypatch)
    path = str(tmp_path / "t.db")
    conn = store.connect(path)
    try:
        store.upsert_environment(conn, [ENV_ROW])
    finally:
        conn.close()
    fresh = db.connect(path)
    try:
        assert db.fetch_environment(fresh, zone="611") == [ENV_ROW]
    finally:
        fresh.close()


def test_facade_read_matches_the_db_module_read(tmp_path, monkeypatch):
    _without_dsn(monkeypatch)
    path = str(tmp_path / "t.db")
    conn = db.connect(path)
    try:
        db.upsert_environment(conn, [ENV_ROW])
        assert store.fetch_environment(conn, zone="611", start="2026-08-16T00:00:00") == (
            db.fetch_environment(conn, zone="611", start="2026-08-16T00:00:00"))
    finally:
        conn.close()


def test_facade_migrate_delegates_to_sqlite(tmp_path, monkeypatch):
    _without_dsn(monkeypatch)
    conn = db.connect(str(tmp_path / "t.db"))
    try:
        assert store.migrate(conn) == []
    finally:
        conn.close()


# ---------- 守卫 ----------


def test_unknown_connection_type_is_rejected():
    """传进来一个既不是 sqlite3.Connection 也不是 PgConnection 的东西 ⇒ 报错。"""
    with pytest.raises(TypeError, match="连接类型"):
        store.fetch_environment(object())


# ---------- 连接串（两套后端共用一份实现） ----------


def test_dsn_comes_from_the_file_and_the_file_wins(monkeypatch, tmp_path):
    """优先级：显式参数 → `PGSQL_DSN_FILE` 文件 → `PGSQL_DSN` 环境变量。

    同一份实现给批次台账（票 13）与 PG 存储（票 28）用；两处各写一遍就会漂移。
    """
    secret = tmp_path / "pgsql.dsn"
    secret.write_text("postgresql://ro@postgres_db/mushroom_algorithm\n", encoding="utf-8")
    monkeypatch.setenv("PGSQL_DSN_FILE", str(secret))
    monkeypatch.setenv("PGSQL_DSN", "postgresql://env-value/db")
    assert store_config.load_dsn() == "postgresql://ro@postgres_db/mushroom_algorithm"
    assert store_config.load_dsn("postgresql://explicit/db") == "postgresql://explicit/db"


def test_a_dsn_file_that_cannot_be_read_is_an_error_not_a_fallback(monkeypatch, tmp_path):
    monkeypatch.setenv("PGSQL_DSN_FILE", str(tmp_path / "missing.dsn"))
    monkeypatch.setenv("PGSQL_DSN", "postgresql://env-value/db")
    with pytest.raises(store.StoreConfigError, match="PGSQL_DSN_FILE"):
        store_config.load_dsn()


def test_pg_schema_name_must_be_a_plain_identifier(monkeypatch):
    """schema 名要拼进 DDL，所以只收"朴素标识符"；别的形态一律报错。"""
    monkeypatch.setenv("ANALYSIS_PG_SCHEMA", "public; drop table environment")
    with pytest.raises(store.StoreConfigError):
        pg_store.schema_name()
    monkeypatch.setenv("ANALYSIS_PG_SCHEMA", "analysis")
    assert pg_store.schema_name() == "analysis"
    monkeypatch.delenv("ANALYSIS_PG_SCHEMA", raising=False)
    assert pg_store.schema_name() == pg_store.DEFAULT_SCHEMA
