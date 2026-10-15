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


# ---------- batch_no 映射（三步走第 1、2 步） ----------


def test_batch_no_comes_from_the_map_and_missing_stays_null():
    rows = rows_to_batches(ROWS, batch_no_map={"611|2026-09-30": "mogu-107"})
    by_key = {r["batch_id"]: r for r in rows}
    assert by_key["611|2026-09-30"]["batch_no"] == "mogu-107"
    assert by_key["611|2026-08-16"]["batch_no"] is None, "没映射就 NULL，不编值"
    assert by_key["611|2026-08-16"]["batch_id"] == "611|2026-08-16", "派生 id 顶显示位"


def test_batch_no_lookup_normalises_the_room_code():
    """映射键是旧编号（`7|…`）、取数是 3 位（`607`）时**必须命中**。

    真缺口（2026-10-14 发现）：映射文件是从我们库里既有行反推的，对 607/608 用的是旧编号；
    而 pgsql 取数回来是 3 位。直接按原文查表 ⇒ **11 个历史批次的 `batch_no` 全变 NULL**，
    看起来跟"还没生成映射"一模一样（正是最难发现的那种静默）。
    """
    rows = rows_to_batches([("607", date(2026, 10, 10))],
                           batch_no_map={"7|2026-10-10": "mogu-110"})
    assert rows[0]["batch_no"] == "mogu-110"
    assert rows[0]["room_id"] == "607", "原始列照旧"
    assert rows[0]["batch_id"] == "607|2026-10-10", "派生 id 用规范号（跨域身份）"

    # 反向也要能命中：映射写成规范号、取数回来是旧编号（我们库里既有行就是这种）
    back = rows_to_batches([("7", date(2026, 10, 10))],
                           batch_no_map={"607|2026-10-10": "mogu-110"})
    assert back[0]["batch_no"] == "mogu-110"
    assert back[0]["room_id"] == "7"

    # 两边都没有 ⇒ NULL（不编）
    none = rows_to_batches([("607", date(2025, 12, 4))], batch_no_map={"7|2026-10-10": "mogu-110"})
    assert none[0]["batch_no"] is None


def test_missing_map_file_warns_and_returns_empty(tmp_path, capsys):
    from analysis.batches import load_batch_no_map

    assert load_batch_no_map(str(tmp_path / "nope.json")) == {}
    assert "没有映射文件" in capsys.readouterr().out, "缺文件要看得见，不静默"


def test_map_file_round_trips(tmp_path):
    from analysis.batches import load_batch_no_map
    from analysis.batch_map import write_map

    target = write_map({"611|2026-09-30": "mogu-107"}, str(tmp_path / "m.json"))
    loaded = load_batch_no_map(str(target))
    assert loaded == {"611|2026-09-30": "mogu-107"}


def test_corrupt_map_file_warns_and_returns_empty(tmp_path, capsys):
    from analysis.batches import load_batch_no_map

    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert load_batch_no_map(str(bad)) == {}
    assert "读不了" in capsys.readouterr().out


def test_batch_map_is_built_from_our_own_db(pg, pg_dsn, pg_schema):
    """映射来源是**我们自己的库**里既有行，不碰业务库。"""
    from analysis.batch_map import build_map
    from analysis.batches import fetch_batches as _unused  # noqa: F401  (名字不同的两条路径)
    from analysis.store import upsert_batches

    upsert_batches(pg, [
        {"room_id": "611", "entry_date": "2026-09-30", "batch_no": "mogu-107",
         "batch_id": "107"},
        {"room_id": "612", "entry_date": "2026-09-30", "batch_no": None, "batch_id": None},
    ])
    assert build_map(dsn=pg_dsn, schema=pg_schema) == {"611|2026-09-30": "mogu-107"}


# ---------- 来源差异可观测（并集口径 + 差异看得见） ----------


def sources_row(room, date, *, y, t):
    return (room, date, y, t)


def test_report_splits_one_sided_batches_and_prints(capsys, monkeypatch):
    from datetime import date as _date

    from analysis.batches import SOURCES_SQL, fetch_batches_with_report

    def runner(sql):
        if sql == SOURCES_SQL:
            return [sources_row(611, _date(2026, 9, 30), y=True, t=True),
                    sources_row(612, _date(2026, 9, 30), y=True, t=False),
                    sources_row(607, _date(2026, 10, 10), y=False, t=True)]
        return ROWS

    batches, report = fetch_batches_with_report(
        runner=runner, map_path=str(__import__("pathlib").Path(".") / "nope.json"))
    out = capsys.readouterr().out
    assert report["n_total"] == 3 and report["n_both"] == 1
    assert report["only_in_batch_yield"] == ["612|2026-09-30"]
    assert report["only_in_text_quality"] == ["607|2026-10-10"]
    assert "只出现在 mushroom_batch_yield：612|2026-09-30" in out
    assert "缺 batch_no 映射 3" in out, "缺映射要有可见计数"
    assert len(batches) == 3
