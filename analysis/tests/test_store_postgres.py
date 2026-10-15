"""存储层（票 28 contract 后唯一的实现）：在与生产同一个实例上，用**独立 schema** 建表、跑完删掉。

**为什么必须独立 schema**：目标库 `mushroom_algorithm` 的 public 里是遗留业务表
（`device_setpoint_changes` / `mushroom_batch_yield` / `decision_analysis_*` …），
同一个实例上还有农场的生产库。用例只在自己的 schema 里建表、只在自己的 schema 里读写，
跑完 `DROP SCHEMA … CASCADE`；**不碰 public、不碰别的 schema、不碰 TDengine**（ADR-0026）。

**连接串从 `ANALYSIS_TEST_DSN` / `ANALYSIS_TEST_DSN_FILE` 读**，**故意不复用
`PGSQL_DSN*`**：那两个是应用跑生产用的，本机 shell 里挂着生产 DSN 时，跑一次测试不该
往生产库里建 schema。两个都没设 ⇒ 整套件 skip（`tests/conftest.py`）。

DSN 解析与 schema 建删只有一处实现（`tests/pg_harness.py`），夹具（`pg_dsn` / `pg_schema` /
`pg` / `catalog`）在 `tests/conftest.py`，多个测试文件共用它们。

本文件钉住：schema 隔离、**列与 `analysis.schema` 的字段清单一一对应**、索引真的建出来、
以及各表的读写形状（布尔/整数按声明类型落库、列表型字段读回来还是列表）。
"""

from __future__ import annotations

import pytest

from analysis import store
from analysis.schema import (
    BATCH_FIELDS,
    ENVIRONMENT_FIELDS,
    IMAGE_FIELDS,
    MEASUREMENT_FIELDS,
    OPERATION_FIELDS,
    ROUND_FIELDS,
)

import pg_harness

#: 目标表的清单（与 `analysis.schema` 的字段清单一对应）。
TABLES = (
    "batches", "batches_state", "environment", "images", "measurements", "operations",
    "ranges", "ranges_state", "rounds",
)

ENV_ROWS = [
    {"ts": "2026-08-16T10:00:00", "zone": "611", "temp_c": 21.5, "temp_min": 20.1,
     "temp_max": 22.8, "rh_pct": 88.0, "rh_min": 85.0, "rh_max": 91.0, "co2_ppm": 900.0,
     "co2_min": 800.0, "co2_max": 1100.0, "lamp_state": 0.0, "lamp_on_s": 30.0,
     "lamp_off_s": 90.0, "lamp_on_m": 0.5, "lamp_off_m": 1.5, "lamp_on_min": 15.0, "n": 60},
    {"ts": "2026-08-16T11:00:00", "zone": "611", "temp_c": 22.5, "temp_min": 21.1,
     "temp_max": 23.8, "rh_pct": 87.0, "rh_min": 84.0, "rh_max": 90.0, "co2_ppm": 950.0,
     "co2_min": 850.0, "co2_max": 1150.0, "lamp_state": 0.0, "lamp_on_s": 30.0,
     "lamp_off_s": 90.0, "lamp_on_m": 0.5, "lamp_off_m": 1.5, "lamp_on_min": 15.0, "n": 60},
    {"ts": "2026-08-16T10:00:00", "zone": "612", "temp_c": 20.5, "temp_min": 19.1,
     "temp_max": 21.8, "rh_pct": 89.0, "rh_min": 86.0, "rh_max": 92.0, "co2_ppm": 880.0,
     "co2_min": 780.0, "co2_max": 1080.0, "lamp_state": 0.0, "lamp_on_s": 30.0,
     "lamp_off_s": 90.0, "lamp_on_m": 0.5, "lamp_off_m": 1.5, "lamp_on_min": 15.0, "n": 59},
]

MEASUREMENT_ROWS = [
    {"ts": "2026-08-16T10:00:00", "room_id": "611", "box_id": "B105", "station_id": "S105",
     "angle_profile": "level0", "n": 3, "mean_len_mm": None, "mean_cap_mm": None,
     "mean_cluster_w_mm": 120.0, "mean_cluster_h_mm": 90.0, "mean_area_mm2": 8400.0,
     "mean_projected_d_mm": 130.5, "p10_len": None, "p90_len": None, "quality": "verified",
     "estimated_fields": ["mean_cluster_w_mm", "mean_area_mm2"], "scale_source": "bag_diameter",
     "mm_per_px": 0.42, "scale_ref_mm": 240.0, "scale_ref_px": 571.4},
    {"ts": "2026-08-16T11:00:00", "room_id": "612", "box_id": "B201", "station_id": "S201",
     "angle_profile": "level0", "n": 1, "mean_cluster_w_mm": None, "mean_cluster_h_mm": None,
     "mean_area_mm2": None, "mean_projected_d_mm": None, "quality": "low_confidence",
     "estimated_fields": None, "scale_source": "none"},
]

ROUND_ROWS = [
    {"ts": "2026-08-16T10:00:20", "round_id": "r-1", "ok": True, "n_results": 48,
     "n_failures": 0, "aborted": False, "room_id": 611, "entry_date": "2026-08-16",
     "batch_no": "mogu-1", "suspect_duplicate": False,
     "dup_groups": [{"sha256_12": "abc123def456", "n": 4, "stations": ["S211", "S212"]}],
     "content_check_error": None, "n_duplicate": 4, "n_frames_recorded": 48},
]

IMAGE_ROWS = [
    {"ts": "2026-08-16T10:00:20", "round_id": "r-1", "station_id": "S105", "box_id": "B105",
     "angle_profile": "level0", "imaging_profile": "snap-3", "frame_dup": False,
     "dup_group": None, "content_sha256": "d" * 64, "same_as_previous": False,
     "duplicate_streak": 0, "camera_ip": "10.0.0.9", "yz": [-40, -90],
     "object_name": "2026/08/16/a.jpg", "object_name_source": "service", "cloud_url": None,
     "ok": True, "error": None, "elapsed_s": 1.25, "room_id": "611",
     "entry_date": "2026-08-16", "batch_no": "mogu-1"},
    {"ts": "2026-08-16T10:01:20", "round_id": "r-1", "station_id": "S106", "box_id": "B106",
     "angle_profile": "level0", "yz": [-40, -145], "object_name": None,
     "object_name_source": "request", "ok": False, "error": "timeout", "elapsed_s": 8.0,
     "room_id": "611", "entry_date": "2026-08-16", "batch_no": "mogu-1"},
]

OPERATION_ROWS = [
    {"ts": "2026-08-16T08:00:00", "device_code": "D1", "point_code": "P1", "zone": "611",
     "device_name": "加湿", "point_name": "设定", "value": "5", "operator": "现场",
     "source": "local", "content": "加湿开启设定[5]"},
    {"ts": "2026-08-16T09:00:00", "device_code": "D1", "point_code": "P1", "zone": "611",
     "device_name": "加湿", "point_name": "设定", "value": "6", "operator": "远程",
     "source": "remote", "content": "加湿开启设定[6]"},
    {"ts": "2026-08-16T09:00:00", "device_code": "D2", "point_code": "P2", "zone": "611",
     "device_name": "风机", "point_name": "开关", "value": "1", "operator": "现场",
     "source": "local", "content": "风机开启[1]"},
    {"ts": "2026-08-17T08:00:00", "device_code": "D1", "point_code": "P1", "zone": "611",
     "device_name": "加湿", "point_name": "设定", "value": "7", "operator": "现场",
     "source": "local", "content": "加湿开启设定[7]"},
]

BATCH_ROWS = [
    {"room_id": "611", "entry_date": "2026-08-16", "batch_no": "mogu-1", "batch_id": "611|2026-08-16"},
    {"room_id": "612", "entry_date": "2026-08-16", "batch_no": "mogu-2", "batch_id": "612|2026-08-16"},
]

RANGE_ROWS = [
    {"stage": "D1-D7", "param": "temp_c", "lo": 18.0, "hi": 22.0, "metric": "mean_area_mm2",
     "unit": "mm2/h", "rate_per_h": 12.5, "n": 6, "n_verified": 6, "n_estimate": 0,
     "tier": "verified"},
    {"stage": "D1-D7", "param": "rh_pct", "lo": 85.0, "hi": 92.0, "metric": "mean_projected_d_mm",
     "unit": "mm/h", "rate_per_h": 0.8, "n": 4, "n_verified": 0, "n_estimate": 4,
     "tier": "estimate"},
]


def _tables(cur, schema: str) -> list[str]:
    cur.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = %s "
        "ORDER BY table_name", (schema,),
    )
    return [row[0] for row in cur.fetchall()]


def _columns(cur, schema: str, table: str) -> list[tuple[str, str]]:
    cur.execute(
        "SELECT column_name, data_type FROM information_schema.columns "
        "WHERE table_schema = %s AND table_name = %s ORDER BY ordinal_position",
        (schema, table),
    )
    return list(cur.fetchall())


#: 表 → 该表的字段清单（唯一实现处是 `analysis.schema`）。这张对应表钉的是"哪张表归哪份清单"：
#: 建表 DDL 少一列 → 写入报错；字段清单多一列 → 同样报错。两边都不许漂。
_CONTRACT = {
    "environment": ENVIRONMENT_FIELDS,
    "measurements": MEASUREMENT_FIELDS,
    "rounds": ROUND_FIELDS,
    "images": IMAGE_FIELDS,
    "operations": OPERATION_FIELDS,
    "batches": BATCH_FIELDS,
}


# ---------- schema 与列 ----------


def test_tables_are_created_in_the_given_schema(pg, catalog, pg_schema):
    assert _tables(catalog, pg_schema) == list(TABLES)


def test_columns_match_the_field_contract(pg, catalog, pg_schema):
    """每张表**实际建出来的列** == `analysis.schema` 的字段清单（比对集合，不比顺序：
    写入 SQL 是具名列的，顺序不参与行为；顺序另有 `MEASUREMENT_FIELDS` 的用例钉住）。"""
    for table, fields in _CONTRACT.items():
        actual = {name for name, _ in _columns(catalog, pg_schema, table)}
        assert actual == set(fields), f"{table}: 建表 DDL 与字段清单不一致"


def test_the_query_indexes_are_created(pg, catalog, pg_schema):
    """票 28 §4.4：现场 0 个索引 ⇒ 按真实读法补上；少一条就是每次全表扫。"""
    catalog.execute(
        "SELECT indexname, indexdef FROM pg_indexes WHERE schemaname = %s", (pg_schema,)
    )
    indexes = {name: ddl for name, ddl in catalog.fetchall()}
    for name in ("environment_zone_ts", "environment_ts", "images_ts", "images_room_ts",
                 "images_box_ts", "measurements_room_ts", "measurements_box_ts",
                 "operations_zone_ts"):
        assert name in indexes, f"{name} 没建出来"
    assert "(zone, ts)" in indexes["environment_zone_ts"], "库房 + 时间窗"
    assert "(ts)" in indexes["images_ts"], "历史列表按 ts 倒序取最近 N 条"
    assert "(room_id, ts)" in indexes["images_room_ts"]


def test_schema_name_must_be_a_plain_identifier(monkeypatch):
    """schema 名要拼进 DDL，所以只收"朴素标识符"；别的形态一律报错（不做转义）。"""
    monkeypatch.setenv("ANALYSIS_PG_SCHEMA", "public; drop table environment")
    with pytest.raises(store.StoreConfigError):
        store.schema_name()
    monkeypatch.setenv("ANALYSIS_PG_SCHEMA", "analysis")
    assert store.schema_name() == "analysis"
    monkeypatch.delenv("ANALYSIS_PG_SCHEMA", raising=False)
    assert store.schema_name() == store.DEFAULT_SCHEMA


def test_writes_stay_inside_the_given_schema(pg, pg_dsn, pg_schema, catalog):
    """另一个 schema 里同样的表必须是空的：schema 名真的在给写入路由。"""
    store.upsert_environment(pg, ENV_ROWS)
    other_schema = pg_schema + "_other"
    other = store.connect(dsn=pg_dsn, schema=other_schema)
    try:
        assert _tables(catalog, other_schema) == list(TABLES), "第二套表建在另一个 schema"
        assert store.fetch_environment(other) == [], "第一套 schema 里的行不该串过来"
    finally:
        other.close()
        pg_harness.drop_schema(pg_dsn, other_schema)


# ---------- environment ----------


def test_environment_round_trip_is_idempotent_and_windowed(pg):
    store.upsert_environment(pg, ENV_ROWS)
    store.upsert_environment(pg, [ENV_ROWS[0] | {"temp_c": 25.0}])       # 同一 (ts, zone) 覆盖

    all_rows = store.fetch_environment(pg)
    assert [r["ts"] for r in all_rows] == ["2026-08-16T10:00:00", "2026-08-16T10:00:00",
                                          "2026-08-16T11:00:00"]
    room_611 = store.fetch_environment(pg, zone="611")
    assert [r["ts"] for r in room_611] == ["2026-08-16T10:00:00", "2026-08-16T11:00:00"]
    assert room_611[0]["temp_c"] == 25.0, "覆盖写：同一 (ts, zone) 只留一行"
    assert room_611[0]["n"] == 60 and room_611[0]["lamp_on_min"] == 15.0
    assert store.fetch_environment(pg, zone="611", start="2026-08-16T10:30:00") == [
        r for r in room_611 if r["ts"] >= "2026-08-16T10:30:00"]


# ---------- measurements ----------


def test_measurements_round_trip_normalizes_evidence_and_filters(pg):
    store.insert_measurements(pg, MEASUREMENT_ROWS)
    store.insert_measurements(pg, [MEASUREMENT_ROWS[0] | {"quality": "verified+low_light"}])

    rows = store.fetch_measurements(pg)
    assert [r["room_id"] for r in rows] == ["611", "612"]
    first = rows[0]
    assert first["quality"] == "verified+low_light", "主键覆盖写"
    assert first["estimated_fields"] == ["mean_cluster_w_mm", "mean_area_mm2"], "读回来是 list"
    assert first["scale_source"] == "bag_standard", "旧值 bag_diameter 归一"
    assert rows[1]["estimated_fields"] == [] and rows[1]["scale_source"] == "none"
    assert store.fetch_measurements(pg, room_id="612") == [rows[1]]
    assert store.fetch_measurements(pg, box_id="B105") == [first]


# ---------- rounds / images ----------


def test_rounds_round_trip_keeps_dup_groups_as_a_list(pg):
    store.insert_rounds(pg, ROUND_ROWS)
    rows = store.fetch_rounds(pg)
    assert len(rows) == 1
    row = rows[0]
    assert row["dup_groups"] == ROUND_ROWS[0]["dup_groups"], "写进去是列表，读出来还得是列表"
    assert row["ok"] == 1 and row["aborted"] == 0, "布尔按 SQLite 亲和性落成 0/1"
    assert row["room_id"] == "611", "整数库房号按 TEXT 列落成文本"
    assert store.fetch_rounds(pg, room_id="611") == rows
    assert store.fetch_rounds(pg, room_id="612") == []
    assert store.fetch_rounds(pg, entry_date="2026-08-16", batch_no="mogu-1") == rows


def test_images_flatten_yz_and_come_back_newest_first(pg):
    store.insert_images(pg, IMAGE_ROWS)
    rows = store.fetch_images(pg)
    assert [r["station_id"] for r in rows] == ["S106", "S105"], "倒序"
    latest = rows[0]
    assert latest["y"] == -40.0 and latest["z"] == -145.0, "yz 摊平成 y/z"
    assert latest["ok"] == 0 and latest["error"] == "timeout"
    kept = rows[1]
    assert kept["y"] == -40.0 and kept["z"] == -90.0
    assert kept["ok"] == 1 and kept["frame_dup"] == 0 and kept["elapsed_s"] == 1.25
    assert store.fetch_images(pg, station_id="S105") == [kept]
    assert store.fetch_images(pg, room_id="611", limit=1) == [latest]


# ---------- operations ----------


def test_operations_upsert_is_idempotent_and_latest_before_ignores_later_rows(pg):
    store.upsert_operations(pg, OPERATION_ROWS)
    store.upsert_operations(pg, [OPERATION_ROWS[0] | {"value": "9"}])     # 同一 (ts, 设备, 点位)

    same_point = [r for r in store.fetch_operations(pg, zone="611") if r["point_code"] == "P1"]
    assert len(same_point) == 3, "重发同一窗口不翻倍（8-16 08:00 仍是覆盖，不是新增）"
    assert [r["value"] for r in same_point] == ["7", "6", "9"], "最新在前"

    assert store.fetch_operations(pg, zone="612") == []
    assert [r["value"] for r in store.fetch_operations(pg, limit=1)] == ["7"]

    latest = store.fetch_latest_operations_before(pg, zone="611", before="2026-08-17T00:00:00")
    assert [(r["device_code"], r["point_code"], r["value"]) for r in latest] == [
        ("D1", "P1", "6"), ("D2", "P2", "1"),
    ], "窗口起点前每个点位的最后一条（8-17 那条不许进来）"


# ---------- batches ----------


def test_batches_replace_prunes_and_records_state(pg):
    store.upsert_batches(pg, BATCH_ROWS)
    assert [r["batch_id"] for r in store.fetch_batches(pg)] == ["611|2026-08-16", "612|2026-08-16"]
    assert store.fetch_batches(pg, room_id="612") == [BATCH_ROWS[1]]

    report = store.replace_batches(
        pg, [BATCH_ROWS[0]], refreshed_at="2026-08-16T05:20:00", detail={"source": "pgsql"},
    )
    assert report == {
        "n_rows": 1, "n_pruned": 1, "pruned": ["612|2026-08-16"],
        "refreshed_at": "2026-08-16T05:20:00",
    }
    assert store.fetch_batches(pg) == [BATCH_ROWS[0]], "替换是整体替换（陈旧行被 prune）"
    state = store.fetch_batches_state(pg)
    assert state["refreshed_at"] == "2026-08-16T05:20:00"
    assert state["n_rows"] == 1 and state["n_pruned"] == 1
    assert state["pruned"] == ["612|2026-08-16"] and state["detail"] == {"source": "pgsql"}


def test_batches_state_is_a_fixed_shape_before_any_refresh(pg):
    """从没刷过也要有形状：`refreshed_at = None` 让 `GET /batches` 能判"陈旧"。"""
    assert store.fetch_batches_state(pg) == {
        "refreshed_at": None, "n_rows": 0, "n_pruned": 0,
        "pruned": [], "detail": {}, "updated_at": None,
    }


# ---------- ranges ----------


def test_ranges_replace_keeps_the_unit_rule_and_read_side_marks_unverified(pg):
    store.replace_ranges(pg, RANGE_ROWS, computed_at="2026-08-16T02:00:00")

    rows = store.fetch_ranges(pg)
    assert [r["param"] for r in rows] == ["rh_pct", "temp_c"], "按 stage, param 排序"
    speed = {r["param"]: r for r in rows}
    assert speed["temp_c"]["rate_mm_per_h"] is None, "unit=mm2/h 一律不许写 mm 字段"
    assert speed["rh_pct"]["rate_mm_per_h"] == 0.8
    assert speed["temp_c"]["unverified"] is False
    assert speed["rh_pct"]["unverified"] is True, "估算档读侧标未验证"
    assert speed["temp_c"]["computed_at"] == "2026-08-16T02:00:00"
    assert [r["param"] for r in store.fetch_advice_ranges(pg)] == ["temp_c"], "建议层只吃已验证"

    store.replace_ranges(pg, [], computed_at="2026-08-16T03:00:00")
    assert store.fetch_ranges(pg) == [], "整体替换：空表就是空表"


def test_ranges_state_defaults_to_never_computed_and_round_trips(pg):
    assert store.fetch_ranges_state(pg)["state"] == "never_computed"
    store.write_ranges_state(
        pg, state="estimate_only", state_text="只有估算档（未验证）",
        computed_at="2026-08-16T02:00:00", detail={"n_rows": 2}, flags=["unverified"],
        n_rows=2, metric="mean_projected_d_mm", unit="mm/h", updated_at="2026-08-16T02:00:01",
    )
    assert store.fetch_ranges_state(pg) == {
        "computed_at": "2026-08-16T02:00:00", "state": "estimate_only",
        "state_text": "只有估算档（未验证）", "detail": {"n_rows": 2}, "flags": ["unverified"],
        "n_rows": 2, "metric": "mean_projected_d_mm", "unit": "mm/h",
        "updated_at": "2026-08-16T02:00:01",
    }
