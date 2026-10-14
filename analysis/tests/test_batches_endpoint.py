"""`GET /batches`（只读物化台账）+ 整体替换（prune）+ 身份去重。

背景（Chief 2026-10-14）：A′ 已否 ⇒ 巡检 `fetch_room` 的准入依据要**走 analysis 只读端点**
拿物化批次，不再 import `src/utils`。同时 `batches` 是物化缓存，upsert 永不 prune ⇒
换口径时同一批次会挂两种编号、页面上出两张卡（实测踩到），所以：
① 端点给出"批次 + 台账新鲜度"；② ingest 改**整体替换**（自然 prune + 逐条打印）；
③ 读取侧对同一身份去重并计数可见。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta

from analysis.api import create_app
from analysis.db import (
    connect,
    fetch_batches,
    fetch_batches_state,
    replace_batches,
    upsert_batches,
)
from fastapi.testclient import TestClient


def _db(tmp_path, rows=None, *, name="b.db"):
    path = str(tmp_path / name)
    conn = connect(path)
    if rows:
        upsert_batches(conn, rows)
    return path


def batch(room, entry, *, batch_no=None, batch_id=None):
    return {"room_id": room, "entry_date": entry, "batch_no": batch_no,
            "batch_id": batch_id or f"{room}|{entry}"}


# ---------- 端点形状 ----------


def test_batches_endpoint_shape_for_a_canonical_room(tmp_path):
    path = _db(tmp_path, [
        batch("611", "2026-08-01", batch_no="mogu-103"),
        batch("611", "2026-09-30", batch_no="mogu-107"),
    ])
    got = TestClient(create_app(path)).get("/batches", params={"room_id": "611"}).json()
    assert got["ok"] is True and got["room_id"] == "611"
    assert got["n_batches"] == 2
    assert [b["entry_date"] for b in got["batches"]] == ["2026-09-30", "2026-08-01"], "最新在前"
    newest = got["batches"][0]
    assert newest["batch_id"] == "611|2026-09-30", "身份用 ADR-0020 的 `<room>|<date>`"
    assert newest["batch_no"] == "611_20260930", "派生显示名由端点给，消费方不自己拼"
    assert newest["display_name"] == "mogu-107", "业务名单独一列，不与派生名混用"
    assert newest["until"] is None, "末批没有下一批"
    assert got["batches"][1]["until"] == "2026-09-30", "窗口右端=下一批入库日"


def test_batches_endpoint_resolves_legacy_room_code(tmp_path):
    path = _db(tmp_path, [batch("7", "2026-10-10", batch_no="mogu-110")])
    got = TestClient(create_app(path)).get("/batches", params={"room_id": "607"}).json()
    assert got["ok"] is True and got["n_batches"] == 1
    one = got["batches"][0]
    assert one["room_id"] == "607" and one["room_id_raw"] == "7"
    assert one["room_label"] == "607（原始 7）", "显示规范号就必须说明原始是 7"
    assert one["batch_no"] == "607_20261010", "派生名用规范号"
    assert got["room_alias"]["n_resolved"] == 1


def test_batches_endpoint_validates_input(tmp_path):
    path = _db(tmp_path, [batch("611", "2026-09-30")])
    client = TestClient(create_app(path))
    assert client.get("/batches").status_code == 400
    bad = client.get("/batches", params={"room_id": "999"})
    assert bad.status_code == 400 and "不是规范库房号" in bad.json()["detail"]


def test_batches_endpoint_says_never_refreshed_is_stale(tmp_path):
    """**没人刷过必须长得跟刷过不一样**：`refreshed_at=None` ⇒ `stale=true`。"""
    path = _db(tmp_path, [batch("611", "2026-09-30")])
    got = TestClient(create_app(path)).get("/batches", params={"room_id": "611"}).json()
    assert got["ledger_refreshed_at"] is None
    assert got["stale"] is True, "从没刷过不许假装新鲜"
    assert got["stale_after_h"] == 26.0


def test_batches_endpoint_is_fresh_right_after_a_replace(tmp_path):
    path = _db(tmp_path, [batch("611", "2026-08-01")])
    conn = connect(path)
    replace_batches(conn, [batch("611", "2026-09-30", batch_no="mogu-107")])

    got = TestClient(create_app(path)).get("/batches", params={"room_id": "611"}).json()
    assert got["stale"] is False
    assert got["ledger_refreshed_at"] == fetch_batches_state(conn)["refreshed_at"]
    assert got["ledger_n_rows"] == 1


def test_stale_threshold_is_configurable_and_old_stamps_go_stale(tmp_path, monkeypatch):
    path = _db(tmp_path, [batch("611", "2026-09-30")])
    conn = connect(path)
    old = (datetime.now() - timedelta(hours=30)).isoformat(timespec="seconds")
    replace_batches(conn, [batch("611", "2026-09-30")], refreshed_at=old)

    got = TestClient(create_app(path)).get("/batches", params={"room_id": "611"}).json()
    assert got["stale"] is True, "30h > 26h ⇒ 陈旧"

    monkeypatch.setenv("BATCH_STALE_AFTER_H", "48")
    got = TestClient(create_app(path)).get("/batches", params={"room_id": "611"}).json()
    assert got["stale"] is False and got["stale_after_h"] == 48.0


def test_unresolved_room_code_is_named_not_guessed(tmp_path, monkeypatch):
    path = _db(tmp_path, [batch("9", "2026-09-30")])
    alias = tmp_path / "empty.json"
    alias.write_text(json.dumps({"map": {}}), encoding="utf-8")
    monkeypatch.setenv("ROOM_ALIAS_FILE", str(alias))

    got = TestClient(create_app(path)).get("/batches", params={"room_id": "611"}).json()
    assert got["ok"] is True and got["batches"] == [], "没这个库房的批次是合法结果"
    assert got["room_alias"]["unresolved_values"] == {"9": 1}, "但解不出的编号要点名+计数"


# ---------- 整体替换（prune）+ 身份去重 ----------


def test_replace_batches_prunes_stale_rows_and_records_state(tmp_path):
    """换口径后旧编号行要被**替换掉**（不是永远留着），且删了什么看得见。"""
    path = _db(tmp_path, [batch("7", "2026-10-10", batch_no="mogu-110"),
                          batch("611", "2026-09-30", batch_no="mogu-107")])
    conn = connect(path)
    result = replace_batches(conn, [
        batch("607", "2026-10-10", batch_no="mogu-110"),
        batch("611", "2026-09-30", batch_no="mogu-107"),
    ], detail={"n_missing_batch_no": 0})
    assert result["n_pruned"] == 1 and result["pruned"] == ["7|2026-10-10"]

    left = fetch_batches(conn)
    assert {f"{r['room_id']}|{r['entry_date']}" for r in left} == {
        "607|2026-10-10", "611|2026-09-30"
    }, "旧编号行被 prune 掉，同一批次不再挂两个身份"

    state = fetch_batches_state(conn)
    assert state["n_rows"] == 2 and state["n_pruned"] == 1
    assert state["pruned"] == ["7|2026-10-10"] and state["detail"] == {"n_missing_batch_no": 0}
    assert state["refreshed_at"]


def test_replace_batches_is_atomic_and_idempotent(tmp_path):
    path = _db(tmp_path)
    conn = connect(path)
    rows = [batch("611", "2026-09-30")]
    first = replace_batches(conn, rows)
    second = replace_batches(conn, rows)
    assert (first["n_rows"], second["n_rows"]) == (1, 1)
    assert second["n_pruned"] == 0 and len(fetch_batches(conn)) == 1


def test_duplicate_identity_is_deduped_with_a_visible_count(tmp_path):
    """同一 (规范库房号, 入库日期) 两条 ⇒ 只留一条，且**计数可见**（换口径过渡期）。"""
    path = _db(tmp_path, [batch("7", "2026-10-10", batch_no="mogu-110"),
                          batch("607", "2026-10-10", batch_no="mogu-110")])
    got = TestClient(create_app(path)).get("/batches", params={"room_id": "607"}).json()
    assert got["n_batches"] == 1, "同一身份不该出两张卡"
    assert got["duplicates"]["n_dupes"] == 1
    assert got["duplicates"]["dupe_keys"] == ["607|2026-10-10"]
    assert got["batches"][0]["room_id_raw"] == "607", "保留规范编号那条"


def test_environment_batches_also_dedupes(tmp_path):
    from analysis.db import upsert_environment

    path = str(tmp_path / "env.db")
    conn = connect(path)
    upsert_environment(conn, [{"ts": f"2026-10-12T{h:02d}:00:00", "zone": "607",
                               "temp_c": 20.0, "rh_pct": 85.0, "co2_ppm": 800.0, "n": 60}
                              for h in range(4)])
    upsert_batches(conn, [batch("7", "2026-09-30"), batch("607", "2026-09-30")])

    got = TestClient(create_app(path)).get(
        "/environment/batches", params={"zone": "607"}
    ).json()
    assert got["ok"] is True
    assert len(got["batches"]) == 1, "同一批次在环境对比里也只出现一次"
    assert got["duplicates"]["n_dupes"] == 1
