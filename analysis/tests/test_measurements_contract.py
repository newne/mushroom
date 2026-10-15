"""票 11 的存储侧契约：`measurements` 的库房维度、幂等主键与缺测列。

背景：`measurements` 原先只有 `(ts, box_id)`，没有任何库房维度。而
`analysis.align` 按小时聚合环境 —— 缺库房维度会让**两个库房同一个框号互相覆盖**，
区间表算出来是错的，不是空的。这个坑不会自己报错，所以每一条都要用例钉住。
"""

from __future__ import annotations

from analysis.schema import MEASUREMENT_FIELDS


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


# ---------- 库房维度：同框号不许跨库房互相覆盖 ----------


def test_same_box_in_two_rooms_are_two_rows(client):
    rows = [measurement(room_id="611"), measurement(room_id="612")]
    with client as c:
        got = c.post("/ingest", json={"rows": rows}).json()
        all_rows = c.get("/measurements").json()
        room_611 = c.get("/measurements", params={"room_id": "611"}).json()
    assert got["inserted_measurements"] == 2
    assert len(all_rows) == 2
    assert [r["room_id"] for r in room_611] == ["611"]
    assert {r["box_id"] for r in all_rows} == {"B105"}


def test_resend_same_key_overwrites_not_duplicates(client):
    with client as c:
        c.post("/ingest", json={"rows": [measurement(mean_len_mm=10.0)]})
        c.post("/ingest", json={"rows": [measurement(mean_len_mm=11.0)]})
        rows = c.get("/measurements").json()
    assert len(rows) == 1
    assert rows[0]["mean_len_mm"] == 11.0


def test_measurement_without_room_never_reaches_the_table(client):
    with client as c:
        got = c.post("/ingest", json={"rows": [
            measurement(room_id=None),
            measurement(room_id=""),
        ]}).json()
        assert c.get("/measurements").json() == []
    assert got["inserted_measurements"] == 0
    assert got["skipped_measurements"] == 2


# ---------- 缺测：NULL 不是 0 ----------


def test_missing_lengths_are_null_and_quality_marks_why(client):
    """平视单档下盖径/杆长明确缺测（票 11 §9）：NULL + `no_detection`，绝不写 0。"""
    row = measurement(mean_len_mm=None, mean_cap_mm=None, p10_len=None, p90_len=None,
                      n=0, quality="no_detection")
    with client as c:
        c.post("/ingest", json={"rows": [row]})
        got = c.get("/measurements").json()[0]
    assert got["mean_len_mm"] is None
    assert got["mean_cap_mm"] is None
    assert got["quality"] == "no_detection"
    assert got["room_id"] == "611" and got["box_id"] == "B105"


def test_station_and_angle_are_kept_for_future_joins(client):
    with client as c:
        c.post("/ingest", json={"rows": [measurement()]})
        got = c.get("/measurements").json()[0]
    assert got["station_id"] == "S105"
    assert got["angle_profile"] == "level0"


def test_field_list_matches_the_wire_contract():
    """`MEASUREMENT_FIELDS` 与 measure 侧 `RECORD_FIELDS` **一字不差（含顺序）**。

    票 20 追加五个估算证据列；测量侧已同步（`measure/record.py`）。顺带把**顺序**也对齐了：
    以前这边把菌簇四列排在 `quality` 之后，两侧顺序其实一直不同 —— 只有集合相同，
    `写入方多给、存储方少要` 那类错就会藏在"集合一样"背后。现在严格相等。
    """
    from measure.record import RECORD_FIELDS

    assert MEASUREMENT_FIELDS == RECORD_FIELDS, (
        "两侧字段清单必须一字不差（含顺序）：加/删/改名/改顺序都要先改票，两侧同时改"
    )
    assert MEASUREMENT_FIELDS[-5:] == (
        "estimated_fields", "scale_source", "mm_per_px", "scale_ref_mm", "scale_ref_px"
    )


def test_cluster_range_is_kept_because_it_is_the_only_growth_signal_left(client):
    """平视单档下盖径/杆长缺测 ⇒ 长势只能从菌簇范围算（票 11 §9）。"""
    row = measurement(mean_len_mm=None, mean_cap_mm=None, quality="verified+no_angle_profile",
                      mean_cluster_w_mm=61.5, mean_cluster_h_mm=44.0,
                      mean_area_mm2=2123.0, mean_projected_d_mm=52.0)
    with client as c:
        c.post("/ingest", json={"rows": [row]})
        got = c.get("/measurements").json()[0]
    assert got["mean_cluster_w_mm"] == 61.5
    assert got["mean_area_mm2"] == 2123.0
    assert got["mean_projected_d_mm"] == 52.0
    assert got["mean_cap_mm"] is None, "投影径是投影径，不许冒充菌盖直径"
