"""票 11 的存储侧契约：`measurements` 的库房维度、幂等主键、缺测列与老库迁移。

背景：`measurements` 原先只有 `(ts, box_id)`，没有任何库房维度。而
`analysis.align` 按小时聚合环境 —— 缺库房维度会让**两个库房同一个框号互相覆盖**，
区间表算出来是错的，不是空的。这个坑不会自己报错，所以每一条都要用例钉住。
"""

from __future__ import annotations

import sqlite3

from analysis.api import create_app
from analysis.db import (
    MEASUREMENT_FIELDS,
    connect,
    fetch_measurements,
    migrate,
)
from fastapi.testclient import TestClient


def measurement(**kw) -> dict:
    row = {
        "kind": "measurement",
        "ts": "2026-10-13T10:00:00",
        "room_id": "611",
        "box_id": "B105",
        "station_id": "S105",
        "angle_profile": "level0",
        "n": 6,
        "mean_len_mm": 42.5,
        "mean_cap_mm": 31.0,
        "p10_len": 38.0,
        "p90_len": 47.0,
        "quality": "verified",
    }
    row.update(kw)
    return row


def client(tmp_path) -> TestClient:
    return TestClient(create_app(str(tmp_path / "m.db")))


# ---------- 库房维度：同框号不许跨库房互相覆盖 ----------


def test_same_box_in_two_rooms_are_two_rows(tmp_path):
    rows = [measurement(room_id="611"), measurement(room_id="612")]
    with client(tmp_path) as c:
        got = c.post("/ingest", json={"rows": rows}).json()
        all_rows = c.get("/measurements").json()
        room_611 = c.get("/measurements", params={"room_id": "611"}).json()
    assert got["inserted_measurements"] == 2
    assert len(all_rows) == 2
    assert [r["room_id"] for r in room_611] == ["611"]
    assert {r["box_id"] for r in all_rows} == {"B105"}


def test_resend_same_key_overwrites_not_duplicates(tmp_path):
    with client(tmp_path) as c:
        c.post("/ingest", json={"rows": [measurement(mean_len_mm=10.0)]})
        c.post("/ingest", json={"rows": [measurement(mean_len_mm=11.0)]})
        rows = c.get("/measurements").json()
    assert len(rows) == 1
    assert rows[0]["mean_len_mm"] == 11.0


def test_measurement_without_room_never_reaches_the_table(tmp_path):
    with client(tmp_path) as c:
        got = c.post("/ingest", json={"rows": [
            measurement(room_id=None),
            measurement(room_id=""),
        ]}).json()
        assert c.get("/measurements").json() == []
    assert got["inserted_measurements"] == 0
    assert got["skipped_measurements"] == 2


# ---------- 缺测：NULL 不是 0 ----------


def test_missing_lengths_are_null_and_quality_marks_why(tmp_path):
    """平视单档下盖径/杆长明确缺测（票 11 §9）：NULL + `no_detection`，绝不写 0。"""
    row = measurement(mean_len_mm=None, mean_cap_mm=None, p10_len=None, p90_len=None,
                      n=0, quality="no_detection")
    with client(tmp_path) as c:
        c.post("/ingest", json={"rows": [row]})
        got = c.get("/measurements").json()[0]
    assert got["mean_len_mm"] is None
    assert got["mean_cap_mm"] is None
    assert got["quality"] == "no_detection"
    assert got["room_id"] == "611" and got["box_id"] == "B105"


def test_station_and_angle_are_kept_for_future_joins(tmp_path):
    with client(tmp_path) as c:
        c.post("/ingest", json={"rows": [measurement()]})
        got = c.get("/measurements").json()[0]
    assert got["station_id"] == "S105"
    assert got["angle_profile"] == "level0"


def test_field_list_matches_the_wire_contract():
    """`MEASUREMENT_FIELDS` 与 measure 侧 `RECORD_FIELDS` 一字不差（票 11 §1）。"""
    assert MEASUREMENT_FIELDS == (
        "ts", "room_id", "box_id", "station_id", "angle_profile",
        "n", "mean_len_mm", "mean_cap_mm", "p10_len", "p90_len", "quality",
    )


# ---------- 老库迁移：不编库房号，也不丢行 ----------


def old_measurements_db(path, *, n_rows: int = 2) -> None:
    """造一个"票 11 之前"的库：`measurements` 没有 room_id，主键是 (ts, box_id)。"""
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE measurements (
          ts          TEXT NOT NULL,
          box_id      TEXT NOT NULL,
          n           INTEGER,
          mean_len_mm REAL,
          mean_cap_mm REAL,
          p10_len     REAL,
          p90_len     REAL,
          quality     TEXT,
          PRIMARY KEY (ts, box_id)
        );
    """)
    for i in range(n_rows):
        conn.execute(
            "INSERT INTO measurements (ts, box_id, n, mean_len_mm) VALUES (?,?,?,?)",
            (f"2026-04-0{i + 1}T10:00:00", f"B00{i + 1}", 3, 40.0 + i),
        )
    conn.commit()
    conn.close()


def test_migration_moves_roomless_rows_aside_instead_of_inventing_a_room(tmp_path):
    path = tmp_path / "old.db"
    old_measurements_db(path)
    # 走裸连接：`connect()` 自己就会跑迁移，拿它测"迁移做了什么"永远看不到 diff
    conn = sqlite3.connect(str(path))

    added = migrate(conn)
    assert any(a.startswith("measurements(重建") for a in added)

    cols = {r[1] for r in conn.execute("PRAGMA table_info(measurements)")}
    assert {"room_id", "station_id", "angle_profile"} <= cols
    assert fetch_measurements(conn) == [], "新表是干净的，老行不该带着假库房号进来"

    legacy = conn.execute(
        "SELECT ts, box_id, mean_len_mm FROM measurements_legacy ORDER BY ts"
    ).fetchall()
    assert len(legacy) == 2, "老行原样保留，谁要考古去查 legacy 表"
    assert legacy[0][1] == "B001"


def test_migration_of_empty_old_table_leaves_no_legacy(tmp_path):
    path = tmp_path / "old.db"
    old_measurements_db(path, n_rows=0)
    conn = connect(str(path))
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert "measurements_legacy" not in tables


def test_migration_is_idempotent_for_measurements(tmp_path):
    path = tmp_path / "old.db"
    old_measurements_db(path)
    conn = sqlite3.connect(path)
    assert any(a.startswith("measurements(重建") for a in migrate(conn))
    assert migrate(conn) == [], "第二次什么都不该做"


def test_new_rows_land_in_the_rebuilt_table(tmp_path):
    path = tmp_path / "old.db"
    old_measurements_db(path)
    with TestClient(create_app(str(path))) as c:
        c.post("/ingest", json={"rows": [measurement()]})
        rows = c.get("/measurements").json()
    assert len(rows) == 1
    assert rows[0]["room_id"] == "611"
