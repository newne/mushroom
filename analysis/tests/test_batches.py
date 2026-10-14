"""批次台账：pgsql 取数、解析与窗口切分。

2026-10-14 用户裁定：**`tools_mysql` 是业务数据库，禁止使用**；算法侧取数一律走 pgsql。
所以这里的用例同时钉住两件事：① 取数走 pgsql 的并集查询；② **旧通路不许复活**
（`docker exec` / `mysql` / `MYSQL_ROOT_PASSWORD` 一个字都不许再出现）。
"""

from datetime import date, datetime

import pytest
from analysis.batches import (
    SQL,
    BatchFetchError,
    fetch_batches,
    rows_to_batches,
    window_of,
)

ROWS = [(611, date(2026, 8, 16)), (611, date(2026, 9, 30)), (612, date(2026, 9, 30))]


def test_rows_to_batches_shapes_rows_and_keeps_no_invented_display_name():
    rows = rows_to_batches(ROWS)
    assert [r["room_id"] for r in rows] == ["611", "611", "612"]
    assert rows[1]["entry_date"] == "2026-09-30"
    assert rows[1]["batch_id"] == "611|2026-09-30"
    assert rows[1]["batch_no"] is None, "`mogu-<id>` 来自业务库，这里没有就不许编"


def test_rows_to_batches_accepts_datetime_and_string_dates():
    rows = rows_to_batches([(611, datetime(2026, 9, 30, 8, 0)), ("612", "2026-09-27")])
    assert [r["entry_date"] for r in rows] == ["2026-09-30", "2026-09-27"]
    assert [r["room_id"] for r in rows] == ["611", "612"]


def test_rows_to_batches_rejects_wrong_shapes():
    with pytest.raises(BatchFetchError, match="2 列"):
        rows_to_batches([(611,)])
    with pytest.raises(BatchFetchError, match="入库日期不合法"):
        rows_to_batches([(611, "not-a-date")])
    with pytest.raises(BatchFetchError, match="库房号为空"):
        rows_to_batches([(None, date(2026, 9, 30))])


# ---------- 取数通路 ----------


def test_fetch_batches_queries_pgsql_union():
    seen = {}

    def runner(sql):
        seen["sql"] = sql
        return ROWS

    rows = fetch_batches(runner=runner)
    assert len(rows) == 3
    sql = seen["sql"]
    assert "mushroom_batch_yield" in sql and "image_text_quality" in sql
    assert "UNION" in sql, "两张表各可能漏批，取并集"
    assert "ORDER BY room_id, in_date" in sql


def test_the_forbidden_business_db_route_is_gone():
    """旧通路不许复活：禁用 `tools_mysql`、`docker exec`、mysql 口令。"""
    forbidden = ("docker", "tools_mysql", "MYSQL_ROOT_PASSWORD", "mysql ")
    text = SQL.lower()
    for token in forbidden:
        assert token not in text
    assert "psycopg" in __import__("analysis.batches", fromlist=["__doc__"]).__doc__.lower()


def test_missing_dsn_fails_loudly_instead_of_returning_nothing(monkeypatch):
    monkeypatch.delenv("PGSQL_DSN", raising=False)
    monkeypatch.delenv("PGSQL_DSN_FILE", raising=False)
    with pytest.raises(BatchFetchError, match="PGSQL_DSN"):
        fetch_batches(runner=None)


def test_dsn_can_come_from_a_file_and_the_file_wins(monkeypatch, tmp_path):
    """推荐形态：DSN 放文件、进程运行时读 —— 值不进 `docker inspect` 的 `Config.Env`。"""
    from analysis.batches import _load_dsn

    secret = tmp_path / "pgsql.dsn"
    secret.write_text("postgresql://ro@postgres_db/mushroom_algorithm\n", encoding="utf-8")
    monkeypatch.setenv("PGSQL_DSN_FILE", str(secret))
    monkeypatch.setenv("PGSQL_DSN", "postgresql://env-value/db")
    assert _load_dsn() == "postgresql://ro@postgres_db/mushroom_algorithm"
    assert _load_dsn("postgresql://explicit/db") == "postgresql://explicit/db"


def test_dsn_file_that_cannot_be_read_is_an_error_not_a_fallback(monkeypatch, tmp_path):
    from analysis.batches import _load_dsn

    monkeypatch.setenv("PGSQL_DSN_FILE", str(tmp_path / "missing.dsn"))
    monkeypatch.setenv("PGSQL_DSN", "postgresql://env-value/db")
    with pytest.raises(BatchFetchError, match="PGSQL_DSN_FILE"):
        _load_dsn()


def test_window_of_uses_next_batch_as_upper_bound():
    rows = rows_to_batches(ROWS)
    windows = window_of(rows, room="611", limit=2)
    assert windows[0]["entry_date"] == "2026-09-30" and windows[0]["until"] is None
    assert windows[1]["entry_date"] == "2026-08-16" and windows[1]["until"] == "2026-09-30"
