"""票 28 的**链路**用例（contract 后只剩一个后端）：API / CLI / 作业都在 PG 上真跑一遍。

第 2 步（migrate）时这里钉的是"同一份种子数据在 SQLite 与 PG 上响应逐字段相同"；
contract 删掉 SQLite 之后，那套对比装置（`two_stores` / `assert_same_answers` / `one_store` /
`_Target`）没有对比对象了，于是换成本文件现在的形状：**同一个 PG schema 上，写入 → 各条读路径
按显式期望核对**。端点各自的信封形状由各端点的测试文件（`test_api.py` / `test_env_api.py` /
`test_batches_endpoint.py` / `test_setpoints.py` / `test_growth_api.py`，都已改跑 PG）负责。

夹具来自 `conftest.py`；PG 侧只在自己建的 `analysis_test_<随机>` schema 里读写，跑完
`DROP SCHEMA … CASCADE` 并断言不存在；**不碰 public、不碰遗留表、不碰农场库、
不碰 TDengine**（ADR-0026）。连接串不打印、不进仓库。
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from analysis import store
from analysis.align import refresh_ranges
from analysis.api import create_app

#: 环境小时聚合：两个库房 × 三个整点（跨日，够跑 hour/day 的窗口与过滤）。
ENV_ROWS = [
    {"ts": "2026-08-16T10:00:00", "zone": "611", "temp_c": 21.5, "temp_min": 20.1,
     "temp_max": 22.8, "rh_pct": 88.0, "rh_min": 85.0, "rh_max": 91.0, "co2_ppm": 900.0,
     "co2_min": 800.0, "co2_max": 1100.0, "lamp_state": 0.0, "lamp_on_s": 30.0,
     "lamp_off_s": 90.0, "lamp_on_m": 0.5, "lamp_off_m": 1.5, "lamp_on_min": 15.0, "n": 60},
    {"ts": "2026-08-16T11:00:00", "zone": "611", "temp_c": 22.5, "temp_min": 21.1,
     "temp_max": 23.8, "rh_pct": 87.0, "rh_min": 84.0, "rh_max": 90.0, "co2_ppm": 950.0,
     "co2_min": 850.0, "co2_max": 1150.0, "lamp_state": 0.0, "lamp_on_s": 30.0,
     "lamp_off_s": 90.0, "lamp_on_m": 0.5, "lamp_off_m": 1.5, "lamp_on_min": 15.0, "n": 60},
    {"ts": "2026-08-17T10:00:00", "zone": "611", "temp_c": 23.5, "temp_min": 22.1,
     "temp_max": 24.8, "rh_pct": 86.0, "rh_min": 83.0, "rh_max": 89.0, "co2_ppm": 880.0,
     "co2_min": 780.0, "co2_max": 1080.0, "lamp_state": 1.0, "lamp_on_s": 45.0,
     "lamp_off_s": 45.0, "lamp_on_m": 0.75, "lamp_off_m": 0.75, "lamp_on_min": 45.0, "n": 60},
    {"ts": "2026-08-16T10:00:00", "zone": "612", "temp_c": 20.5, "temp_min": 19.1,
     "temp_max": 21.8, "rh_pct": 89.0, "rh_min": 86.0, "rh_max": 92.0, "co2_ppm": 870.0,
     "co2_min": 770.0, "co2_max": 1070.0, "lamp_state": 0.0, "lamp_on_s": 30.0,
     "lamp_off_s": 90.0, "lamp_on_m": 0.5, "lamp_off_m": 1.5, "lamp_on_min": 15.0, "n": 59},
]


def _connect(pg_dsn, pg_schema):
    """与 API 同一个 schema 的连接（API 自己那份连接在 app 里）。"""
    return store.connect(dsn=pg_dsn, schema=pg_schema)


def _app(pg_dsn, pg_schema, **kwargs):
    return create_app(store_dsn=pg_dsn, store_schema=pg_schema, **kwargs)


# ---------- 环境：写入 → /environment 的读 ----------


def test_environment_endpoints_read_the_store(pg_dsn, pg_schema):
    """/environment 的 hour/day 与窗口过滤读的确实是刚写进去的那几行。"""
    conn = _connect(pg_dsn, pg_schema)
    try:
        store.upsert_environment(conn, ENV_ROWS)
        assert [r["ts"] for r in store.fetch_environment(conn, zone="611")] == [
            "2026-08-16T10:00:00", "2026-08-16T11:00:00", "2026-08-17T10:00:00"]
    finally:
        conn.close()

    with TestClient(_app(pg_dsn, pg_schema)) as client:
        hour = client.get("/environment", params={"zone": "611", "granularity": "hour"}).json()
        assert [r["ts"] for r in hour["rows"]] == [
            "2026-08-16T10:00:00", "2026-08-16T11:00:00", "2026-08-17T10:00:00"]
        assert hour["rows"][0]["temp_c"] == 21.5
        windowed = client.get("/environment", params={
            "zone": "611", "start": "2026-08-17T00:00:00"}).json()
        assert [r["ts"] for r in windowed["rows"]] == ["2026-08-17T10:00:00"]
        other_room = client.get("/environment", params={"zone": "612"}).json()
        assert [r["ts"] for r in other_room["rows"]] == ["2026-08-16T10:00:00"]


# ---------- 测量：POST /ingest → measurements / growth 的读 ----------

#: B101 连续两个整点（跑 /growth 的速率），B201 只有一行（单点路径）。
MEASUREMENT_ROWS = [
    {"ts": "2026-08-16T10:00:00", "room_id": "611", "box_id": "B101", "station_id": "S101",
     "angle_profile": "level0", "n": 3, "mean_len_mm": 40.0, "mean_cap_mm": 12.0,
     "mean_cluster_w_mm": 120.0, "mean_cluster_h_mm": 90.0, "mean_area_mm2": 8400.0,
     "mean_projected_d_mm": 130.5, "p10_len": 38.0, "p90_len": 42.0, "quality": "verified",
     "estimated_fields": ["mean_cluster_w_mm", "mean_area_mm2"], "scale_source": "bag_diameter",
     "mm_per_px": 0.42, "scale_ref_mm": 240.0, "scale_ref_px": 571.4},
    {"ts": "2026-08-16T11:00:00", "room_id": "611", "box_id": "B101", "station_id": "S101",
     "angle_profile": "level0", "n": 3, "mean_len_mm": 44.0, "mean_cap_mm": 13.0,
     "mean_cluster_w_mm": 124.0, "mean_cluster_h_mm": 93.0, "mean_area_mm2": 8600.0,
     "mean_projected_d_mm": 134.5, "p10_len": 42.0, "p90_len": 46.0, "quality": "verified",
     "estimated_fields": ["mean_cluster_w_mm", "mean_area_mm2"], "scale_source": "bag_standard",
     "mm_per_px": 0.42, "scale_ref_mm": 240.0, "scale_ref_px": 571.4},
    {"ts": "2026-08-16T10:00:00", "room_id": "612", "box_id": "B201", "station_id": "S201",
     "angle_profile": "level0", "n": 1, "mean_cluster_w_mm": None, "mean_cluster_h_mm": None,
     "mean_area_mm2": None, "mean_projected_d_mm": None, "quality": "low_confidence",
     "estimated_fields": None, "scale_source": "none"},
]


def test_ingest_measurements_then_reads(pg_dsn, pg_schema):
    """POST /ingest（写）→ /measurements、/growth（读）：计数与判词都对得上。"""
    body = {"rows": [{"kind": "measurement", **row} for row in MEASUREMENT_ROWS]}
    with TestClient(_app(pg_dsn, pg_schema)) as client:
        posted = client.post("/ingest", json=body).json()
        assert posted["inserted_measurements"] == 3
        assert posted["skipped_measurements"] == 0

        assert len(client.get("/measurements").json()) == 3
        room_611 = client.get("/measurements", params={"room_id": "611"}).json()
        assert [r["box_id"] for r in room_611] == ["B101", "B101"]
        assert client.get("/measurements", params={"box_id": "B201"}).json()[0][
            "scale_source"] == "none"

        growth = client.get("/growth", params={"box_id": "B101"}).json()
        assert [p["verdict"] for p in growth["points"]] == ["no_prev", "growing"]
        assert growth["latest"]["rate_len_mm_per_h"] == 4.0

    conn = _connect(pg_dsn, pg_schema)
    try:
        assert len(store.fetch_measurements(conn)) == 3, "写侧经存储层读得回来"
    finally:
        conn.close()


# ---------- 轮次与图像：POST /ingest → /rounds、/images、/growth/room ----------

#: 一轮 + 两帧（B101 / B102 同库房）：/growth/room 的框清单只能从图像索引来。
ROUND_ROWS = [
    {"ts": "2026-08-16T10:00:20", "round_id": "r-1", "ok": True, "n_results": 48,
     "n_failures": 0, "aborted": False, "room_id": "611", "entry_date": "2026-08-16",
     "batch_no": "mogu-1", "suspect_duplicate": False,
     "dup_groups": [{"sha256_12": "abc123def456", "n": 4, "stations": ["S211", "S212"]}],
     "content_check_error": None, "n_duplicate": 4, "n_frames_recorded": 48},
]

IMAGE_ROWS = [
    {"ts": "2026-08-16T10:00:20", "round_id": "r-1", "station_id": "S101", "box_id": "B101",
     "angle_profile": "level0", "imaging_profile": "snap-3", "frame_dup": False,
     "dup_group": None, "content_sha256": "d" * 64, "same_as_previous": False,
     "duplicate_streak": 0, "camera_ip": "10.0.0.9", "yz": [-40, -90],
     "object_name": "2026/08/16/a.jpg", "object_name_source": "service", "cloud_url": None,
     "ok": True, "error": None, "elapsed_s": 1.25, "room_id": "611",
     "entry_date": "2026-08-16", "batch_no": "mogu-1"},
    {"ts": "2026-08-16T10:01:20", "round_id": "r-1", "station_id": "S102", "box_id": "B102",
     "angle_profile": "level0", "yz": [-40, -145], "object_name": None,
     "object_name_source": "request", "ok": False, "error": "timeout", "elapsed_s": 8.0,
     "room_id": "611", "entry_date": "2026-08-16", "batch_no": "mogu-1"},
]

#: B101 两个整点且值几乎相等 —— /growth/room 里应当是 stalled（配 no_prev 的 B102）。
ROOM_MEASUREMENT_ROWS = [
    {"ts": "2026-08-16T08:00:00", "room_id": "611", "box_id": "B101", "station_id": "S101",
     "angle_profile": "level0", "n": 6, "mean_len_mm": 40.0, "mean_cap_mm": None,
     "p10_len": None, "p90_len": None, "quality": "verified"},
    {"ts": "2026-08-16T20:00:00", "room_id": "611", "box_id": "B101", "station_id": "S101",
     "angle_profile": "level0", "n": 6, "mean_len_mm": 40.2, "mean_cap_mm": None,
     "p10_len": None, "p90_len": None, "quality": "verified"},
]


def test_ingest_rounds_images_and_growth_room(pg_dsn, pg_schema):
    """/rounds、/images 与 /growth/room 的框清单：写了什么就读出什么。"""
    rows = (
        [{"kind": "round", **row} for row in ROUND_ROWS]
        + [{"kind": "image_index", **row} for row in IMAGE_ROWS]
        + [{"kind": "measurement", **row} for row in ROOM_MEASUREMENT_ROWS]
    )
    with TestClient(_app(pg_dsn, pg_schema)) as client:
        posted = client.post("/ingest", json={"rows": rows}).json()
        assert posted["inserted_rounds"] == 1 and posted["inserted_images"] == 2
        assert posted["inserted_measurements"] == 2

        assert [r["round_id"] for r in client.get("/rounds").json()] == ["r-1"]
        assert [r["round_id"] for r in client.get(
            "/rounds", params={"entry_date": "2026-08-16", "batch_no": "mogu-1"}).json()] == ["r-1"]
        images = client.get("/images", params={"room_id": "611"}).json()
        assert [r["station_id"] for r in images] == ["S102", "S101"], "按 ts 倒序"
        assert [r["box_id"] for r in client.get("/images", params={"box_id": "B101"}).json()] == [
            "B101"]

        room = client.get("/growth/room", params={"room_id": "611"}).json()
        assert set(room["boxes"]) == {"B101", "B102"}, "拍了没测量的框不许从清单里消失"
        assert room["boxes"]["B101"]["verdict"] == "stalled"
        assert room["boxes"]["B102"]["verdict"] == "no_prev"

    conn = _connect(pg_dsn, pg_schema)
    try:
        assert store.fetch_room_box_ids(conn, room_id="611") == ["B101", "B102"], "去重升序"
    finally:
        conn.close()


# ---------- 批次台账：物化行 → /batches 与 /environment/batches ----------

#: 台账物化缓存（同一库房跨两个入库日）：until 与窗口切分都要跑到。
BATCH_ROWS = [
    {"room_id": "611", "entry_date": "2026-08-16", "batch_no": "mogu-1", "batch_id": "611|2026-08-16"},
    {"room_id": "611", "entry_date": "2026-09-30", "batch_no": "mogu-2", "batch_id": "611|2026-09-30"},
    {"room_id": "612", "entry_date": "2026-08-16", "batch_no": None, "batch_id": "612|2026-08-16"},
]


def _seed_batches(conn) -> None:
    """物化台账：整体替换一次（带 refreshed_at 与来源明细，/batches 的新鲜度判据靠它）。"""
    store.replace_batches(
        conn, BATCH_ROWS, refreshed_at="2026-08-16T05:20:00", detail={"source": "pgsql"},
    )


def test_batches_endpoints_read_the_store(pg_dsn, pg_schema):
    """`replace_batches`（写侧）与 /batches、/environment/batches（读侧）都对得上。"""
    conn = _connect(pg_dsn, pg_schema)
    try:
        store.upsert_environment(conn, ENV_ROWS)
        _seed_batches(conn)
        assert len(store.fetch_batches(conn)) == len(BATCH_ROWS)
        assert store.fetch_batches_state(conn)["refreshed_at"] == "2026-08-16T05:20:00"
    finally:
        conn.close()

    with TestClient(_app(pg_dsn, pg_schema)) as client:
        got = client.get("/batches", params={"room_id": "611"}).json()
        assert got["ok"] is True and got["n_batches"] == 2
        assert [b["entry_date"] for b in got["batches"]] == ["2026-09-30", "2026-08-16"], "最新在前"
        assert got["batches"][1]["until"] == "2026-09-30", "窗口右端 = 下一批入库日"
        assert client.get("/batches", params={"room_id": "612"}).json()["n_batches"] == 1

        env_batches = client.get("/environment/batches", params={"zone": "611"}).json()
        assert env_batches["ok"] is True
        assert [b["entry_date"] for b in env_batches["batches"]] == ["2026-08-16"]


# ---------- CLI：`--dsn` / `--schema` 真的写进了这个 schema ----------


def test_batch_map_cli_reverses_the_ledger(pg_dsn, pg_schema, tmp_path):
    """`python -m analysis.batch_map` 从本库既有行反推映射（映射文件是本地 JSON，不落库）。"""
    import json

    from analysis.batch_map import main

    conn = _connect(pg_dsn, pg_schema)
    try:
        _seed_batches(conn)
    finally:
        conn.close()

    out = tmp_path / "batch_no_map.json"
    main(["--dsn", pg_dsn, "--schema", pg_schema, "--out", str(out)])

    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["map"] == {
        "611|2026-08-16": "mogu-1", "611|2026-09-30": "mogu-2",
    }, "只有带 batch_no 的行进映射；缺的（612）不猜"


def test_batch_ingest_run_once_materializes_the_ledger(pg_dsn, pg_schema, tmp_path):
    """`analysis.batch_ingest.run_once`（取数用假 runner 顶掉）落一份台账并写状态。"""
    from datetime import date

    from analysis.batch_ingest import run_once
    from analysis.batches import SOURCES_SQL

    rows = [(611, date(2026, 8, 16)), (611, date(2026, 9, 30)), (612, date(2026, 9, 30))]

    def runner(sql):
        if sql == SOURCES_SQL:
            return [(611, date(2026, 8, 16), True, True),
                    (611, date(2026, 9, 30), True, True),
                    (612, date(2026, 9, 30), True, False)]
        return rows

    conn = _connect(pg_dsn, pg_schema)
    try:
        n = run_once(conn, runner=runner, map_path=str(tmp_path / "nope.json"))
        batches = store.fetch_batches(conn)
        state = store.fetch_batches_state(conn)
    finally:
        conn.close()

    assert n == 3
    assert [b["batch_id"] for b in batches] == [
        "611|2026-08-16", "611|2026-09-30", "612|2026-09-30"]
    assert state["refreshed_at"], "整体替换要写 batches_state（/batches 的新鲜度靠它）"
    assert state["n_rows"] == 3


# ---------- 区间流水线：重算 → 状态 → ranges 作业 ----------

#: 24 小时环境（温度两档）+ 每小时 +1mm 的已验证测量：区间流水线能跑出 STATE_OK。
def _seed_ranges(conn) -> None:
    store.upsert_environment(conn, [
        {"ts": f"2026-08-01T{i:02d}:00:00", "zone": "611", "temp_c": 22.0 + (i % 2) * 4.0,
         "rh_pct": 85.0, "co2_ppm": 800.0, "lamp_state": 2.0}
        for i in range(24)
    ])
    store.insert_measurements(conn, [
        {"ts": f"2026-08-01T{i:02d}:00:00", "room_id": "611", "box_id": "B01",
         "mean_len_mm": 10.0 + i, "quality": "verified"}
        for i in range(24)
    ])


def test_ranges_pipeline_and_endpoint(pg_dsn, pg_schema):
    """`refresh_ranges(conn)` 真跑一轮，`/ranges` 读到的就是它写的那张表。"""
    conn = _connect(pg_dsn, pg_schema)
    try:
        _seed_ranges(conn)
        n = refresh_ranges(conn, computed_at="2026-08-16T02:00:00")
        assert n > 0, "算出区间行（不是「空表对空表」）"
        state = store.fetch_ranges_state(conn)
        assert state["state"] == "ok" and state["n_rows"] == n
        assert state["computed_at"] == "2026-08-16T02:00:00"
        rows = store.fetch_ranges(conn)
    finally:
        conn.close()

    with TestClient(_app(pg_dsn, pg_schema)) as client:
        body = client.get("/ranges").json()
        assert body["state"] == "ok" and body["count"] == n
        assert body["rows"] == rows, "端点只读那张物化表"


def test_ranges_job_recomputes_through_the_shared_connection(pg, monkeypatch):
    """`RANGES_AT` 装出来的 ranges 作业**拿 API 那一份连接**重算（不按路径重连）。

    按路径重连在 PG 下要么连错库、要么丢掉 schema；这里跑一轮真实的重算，
    确认区间行与状态都落在同一个 schema 里。
    """
    from analysis.jobs import default_jobs

    monkeypatch.setenv("RANGES_AT", "05:40")
    _seed_ranges(pg)
    jobs = {job.name: job for job in default_jobs(pg)}
    assert set(jobs) == {"ranges"}

    jobs["ranges"].run()

    state = store.fetch_ranges_state(pg)
    assert state["state"] == "ok" and state["n_rows"] > 0
    assert len(store.fetch_ranges(pg)) == state["n_rows"]


# ---------- CLI：两个 ingest 的 `--dsn` / `--schema` ----------


def test_env_ingest_cli_writes_hours(pg_dsn, pg_schema, tmp_path):
    """`python -m analysis.env.ingest --once` 把 CSV 折成小时聚合写进这个 schema。"""
    from analysis.env.ingest import main

    csv_dir = tmp_path / "env_csv"
    csv_dir.mkdir()
    (csv_dir / "a.csv").write_text(
        "ts,zone,temp_c,rh_pct,co2_ppm,lamp_state\n"
        "2026-08-30T08:00:00,611,23.5,85.0,800,120\n"
        "2026-08-30T09:00:00,611,24.0,88.0,900,150\n",
        encoding="utf-8",
    )

    main(["--dsn", pg_dsn, "--schema", pg_schema,
          "--source", "csv", "--path", str(csv_dir), "--once"])

    conn = _connect(pg_dsn, pg_schema)
    try:
        rows = store.fetch_environment(conn, zone="611")
    finally:
        conn.close()
    assert [(r["ts"], r["temp_c"]) for r in rows] == [
        ("2026-08-30T08:00:00", 23.5), ("2026-08-30T09:00:00", 24.0),
    ], "CLI 写进去的小时聚合要能被存储层读回来"


def test_audit_ingest_run_once_writes_operations(pg_dsn, pg_schema):
    """`analysis.audit_ingest.run_once`（操作日志入库）写得进去、读得回来。

    环控平台的调用用假 transport 顶掉：这里要证的是写侧通了，不是再测一遍接口解析
    （那在 `test_audit.py`）。
    """
    from analysis.audit import md5_hex
    from analysis.audit_ingest import run_once

    raw = {
        "total": 1,
        "list": [{
            "deviceCode": "TD1_Q3MDHUM02", "pointCode": "On", "pointValue": "5",
            "actionSource": "现场", "actionState": True, "actionModel": "现场操作员",
            "operator": "现场操作员", "remark": None, "operateTime": "2026-10-12 10:10:00",
            "deviceName": "607库-右加湿器", "pointName": "加湿开启设定",
            "actionType": None, "actionContent": "加湿开启设定[5]",
        }],
    }

    def transport(url, body, headers=None):
        if url.endswith("/service/security/auth/login"):
            return {"code": 200, "data": {"accessToken": "tok"}}
        assert headers and headers["X-AUTH-TOKEN"] == "tok"
        return {"code": 200, "data": raw}

    conn = _connect(pg_dsn, pg_schema)
    try:
        n = run_once(
            conn, base_url="http://ems", account="u", password_md5=md5_hex("p"),
            transport=transport,
        )
        rows = store.fetch_operations(conn, zone="607")
    finally:
        conn.close()
    assert n == 1
    assert [(r["ts"], r["value"]) for r in rows] == [("2026-10-12T10:10:00", "5")]
