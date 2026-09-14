"""库房维度必须活着穿过 `/ingest`，以及老库要能就地补列。

背景（2026-09-13 审计发现）：`patrol.daemon` 给每条 `round` / `image_index` 行都盖了
`room_id`/`entry_date`/`batch_no`，但 `images`/`rounds` 表里没有这三列，
`insert_images` 又只按 `IMAGE_FIELDS` 取值 —— 于是这三个字段在 `/ingest` 这一跳
**被静默丢掉**，没人报错。照片在 MinIO 里、索引在库里，却说不清属于哪间库房。

这类"写入方多给、存储方少要"的字段丢失不会自己暴露，所以每一条都要有用例钉住。
"""

from __future__ import annotations

import sqlite3

import pytest
from analysis.api import create_app
from analysis.db import connect, fetch_images, migrate
from fastapi.testclient import TestClient

ROOM = {"room_id": "611", "entry_date": "2026-03-16", "batch_no": "mogu-100"}


def image_row(**kw) -> dict:
    row = {"kind": "image_index", "ts": "2026-09-13T20:25:42", "ok": True,
           "box_id": "B101", "station_id": "S101", "angle_profile": "top45",
           "camera_ip": "192.168.1.238", "yz": [187.167, -21.2],
           "object_name": "20260913/B101_S101_top45_202542",
           "cloud_url": "http://192.168.1.250:9000/mogu/20260913/B101_S101_top45_202542.jpg",
           "elapsed_s": 13.1, **ROOM}
    row.update(kw)
    return row


def round_row(**kw) -> dict:
    row = {"kind": "round", "ts": "2026-09-13T20:35:15", "ok": False, "n_results": 59,
           "n_failures": 1, "aborted": False, **ROOM}
    row.update(kw)
    return row


def client(tmp_path) -> TestClient:
    return TestClient(create_app(str(tmp_path / "api.db")))


# ---------- 库房维度端到端 ----------


def test_image_rows_keep_the_room_stamp(tmp_path):
    with client(tmp_path) as c:
        assert c.post("/ingest", json={"rows": [image_row()]}).status_code == 200
        rows = c.get("/images").json()
    assert len(rows) == 1
    assert rows[0]["room_id"] == "611"
    assert rows[0]["entry_date"] == "2026-03-16"
    assert rows[0]["batch_no"] == "mogu-100"
    assert rows[0]["object_name"] == "20260913/B101_S101_top45_202542"


def test_round_rows_keep_the_room_stamp(tmp_path):
    with client(tmp_path) as c:
        assert c.post("/ingest", json={"rows": [round_row()]}).status_code == 200
        rows = c.get("/rounds").json()
    assert rows[0]["room_id"] == "611"
    assert rows[0]["entry_date"] == "2026-03-16"
    assert rows[0]["n_failures"] == 1


def test_mixed_batch_ingests_every_kind(tmp_path):
    """真实 outbox 就是混着三种 kind 的一批（sync.py 全批一个请求）。"""
    rows = [round_row(), image_row(),
            image_row(station_id="S102", box_id="B102", ts="2026-09-13T20:26:02",
                      object_name="20260913/B102_S102_top45_202602"),
            {"kind": "measurement", "ts": "2026-09-13T20:35:15", "box_id": "B101",
             "n": 6, "mean_len_mm": 42.5, "mean_cap_mm": 31.0, "p10_len": 38.0,
             "p90_len": 47.0, "quality": ""}]
    with client(tmp_path) as c:
        got = c.post("/ingest", json={"rows": rows}).json()
        assert got == {"inserted_measurements": 1, "inserted_rounds": 1, "inserted_images": 2}
        assert c.get("/images").json()[0]["room_id"] == "611"
        assert c.get("/rounds").json()[0]["room_id"] == "611"
        assert c.get("/measurements").json()[0]["mean_len_mm"] == 42.5


def test_failed_frame_keeps_room_and_position(tmp_path):
    """失败帧没有 object_name，但库房与"停在哪"这两件事必须留下。"""
    bad = image_row(ok=False, object_name=None, cloud_url=None,
                    error="controller did not finish the move")
    bad.pop("kind")
    with client(tmp_path) as c:
        c.post("/ingest", json={"rows": [{"kind": "image_index", **bad}]})
        row = c.get("/images").json()[0]
    assert row["ok"] == 0
    assert row["object_name"] is None
    assert row["room_id"] == "611"
    assert row["y"] == pytest.approx(187.167)


def test_images_can_be_filtered_by_room_and_box(tmp_path):
    rows = [image_row(),
            image_row(room_id="612", station_id="S201", box_id="B201",
                      ts="2026-09-13T20:27:02")]
    with client(tmp_path) as c:
        c.post("/ingest", json={"rows": rows})
        assert len(c.get("/images?room_id=611").json()) == 1
        assert len(c.get("/images?room_id=612").json()) == 1
        assert len(c.get("/images?box_id=B201").json()) == 1
        assert len(c.get("/images?limit=1").json()) == 1
        assert len(c.get("/images").json()) == 2


# ---------- 老库就地升级 ----------


def old_db(path) -> sqlite3.Connection:
    """造一个"改动之前"的库：images/rounds 里没有库房三列。"""
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE images (
          ts TEXT NOT NULL, station_id TEXT, box_id TEXT, angle_profile TEXT,
          camera_ip TEXT, y REAL, z REAL, object_name TEXT, cloud_url TEXT,
          ok INTEGER, error TEXT,
          PRIMARY KEY (ts, station_id, angle_profile));
        CREATE TABLE rounds (ts TEXT PRIMARY KEY, ok INTEGER, n_results INTEGER,
          n_failures INTEGER, aborted INTEGER);
    """)
    conn.execute("INSERT INTO images (ts, station_id, box_id, ok) VALUES (?,?,?,?)",
                 ("2026-09-13T10:00:20", "S101", "B101", 1))
    conn.execute("INSERT INTO rounds (ts, ok, n_results) VALUES (?,?,?)",
                 ("2026-09-13T10:10:31", 1, 60))
    conn.commit()
    return conn


def test_migrate_adds_missing_room_columns(tmp_path):
    """直接开裸连接（不走 `connect`，否则列已经被补上了）看 migrate 做了什么。"""
    path = tmp_path / "old.db"
    old_db(path).close()
    conn = sqlite3.connect(path)

    added = migrate(conn)
    assert set(added) == {"images.room_id", "images.entry_date", "images.batch_no",
                          "rounds.room_id", "rounds.entry_date", "rounds.batch_no"}
    cols = {r[1] for r in conn.execute("PRAGMA table_info(images)")}
    assert {"room_id", "entry_date", "batch_no"} <= cols
    rounds_cols = {r[1] for r in conn.execute("PRAGMA table_info(rounds)")}
    assert {"room_id", "entry_date", "batch_no"} <= rounds_cols


def test_migration_keeps_old_rows_and_leaves_room_unknown(tmp_path):
    """老行补列后是 NULL，而不是编一个默认库房号——"不知道"就该显示为不知道。"""
    path = tmp_path / "old.db"
    old_db(path).close()
    conn = connect(str(path))
    rows = fetch_images(conn)
    assert len(rows) == 1
    assert rows[0]["station_id"] == "S101"
    assert rows[0]["room_id"] is None
    assert rows[0]["entry_date"] is None


def test_migrate_is_idempotent(tmp_path):
    path = tmp_path / "old.db"
    old_db(path).close()
    conn = sqlite3.connect(path)
    assert migrate(conn), "第一次应当真的补了列"
    assert migrate(conn) == [], "第二次什么都不该做"


def test_old_rows_survive_a_new_ingest(tmp_path):
    """升级过的老库继续收新数据：老行还在、新行带库房维度。"""
    path = tmp_path / "old.db"
    old_db(path).close()
    with TestClient(create_app(str(path))) as c:
        c.post("/ingest", json={"rows": [image_row()]})
        rows = c.get("/images").json()
    assert len(rows) == 2
    by_ts = {r["ts"]: r for r in rows}
    assert by_ts["2026-09-13T10:00:20"]["room_id"] is None
    assert by_ts["2026-09-13T20:25:42"]["room_id"] == "611"
