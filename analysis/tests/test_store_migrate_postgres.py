"""票 28 第 2 步（migrate）：调用点切到门面后，**PG 侧补上原来只走 SQLite 的场景**。

第 1 步（expand）只证明了"两套实现读回来的行相等"（见 `test_store_postgres.py`）。
这一步每切一批调用点（环境 → measurements → rounds / images → operations → ranges →
batches_state），这里就补上对应的一批用例，钉住的是**链路**而不只是存储：

- 同一份种子数据在 SQLite 与 PG 上各跑一遍，**同一个 HTTP 请求的响应逐字段相同**；
- 原来按路径重连的地方（`compute_ranges` / `refresh_ranges` / `jobs.default_jobs`）改成
  传连接后，在 PG 上照样跑得通；
- 四个 ingest CLI 换成 `--dsn` 之后，PG 上写得进去、读得回来。

没有测试 DSN（`ANALYSIS_TEST_DSN` / `ANALYSIS_TEST_DSN_FILE`）就 skip —— 夹具来自
`conftest.py`。PG 侧只在自己建的 `analysis_test_<随机>` schema 里读写，跑完
`DROP SCHEMA … CASCADE` 并断言不存在；**不碰 public、不碰遗留表、不碰农场库、
不碰 TDengine**（ADR-0026）。连接串不打印、不进仓库。
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass

import pytest
from analysis import store
from analysis.align import refresh_ranges
from analysis.api import create_app
from fastapi.testclient import TestClient

import pg_harness

#: `two_stores` 建的 SQLite 临时库名（用例要按"老形状"传路径时用它）。
SQLITE_NAME = "migrate.db"

#: 环境小时聚合：两个库房 × 两个整点（够跑 hour/day 聚合与 summary）。
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


def _seed_env(conn) -> None:
    store.upsert_environment(conn, ENV_ROWS)


@contextmanager
def two_stores(tmp_path, monkeypatch, pg_dsn, pg_schema):
    """同一份种子数据在两个后端上各建一份：SQLite 用临时文件，PG 用临时 schema。

    两边各给一个**门面连接**（种数据）与一个 **API 实例**（读响应）—— 迁移的验收就是
    "同一个请求两边逐字段相同"，所以这里把两份并排摆好，用例只负责比。
    """
    monkeypatch.delenv("ANALYSIS_STORE", raising=False)
    path = str(tmp_path / SQLITE_NAME)
    sqlite_conn = store.connect(path)
    sqlite_app = create_app(path)
    monkeypatch.setenv("ANALYSIS_STORE", "postgres")
    pg_conn = store.connect(dsn=pg_dsn, schema=pg_schema)
    pg_app = create_app(store_dsn=pg_dsn, store_schema=pg_schema)
    try:
        yield sqlite_conn, sqlite_app, pg_conn, pg_app
    finally:
        sqlite_conn.close()
        pg_conn.close()


def assert_same_answers(sqlite_app, pg_app, cases) -> None:
    """同一批请求打两个实例，响应必须逐字段相同（JSON 全等，含键序）。"""
    with TestClient(sqlite_app) as a, TestClient(pg_app) as b:
        for url, params in cases:
            assert b.get(url, params=params).json() == a.get(url, params=params).json(), \
                f"{url}?{params} 两个后端的响应不一致"


# ---------- 环境（第一批：连接 + /environment 的读） ----------


def test_environment_endpoints_agree_on_both_backends(
    tmp_path, monkeypatch, pg_dsn, pg_schema
):
    """/environment、/environment/summary：PG 与 SQLite 的响应逐字段相同。

    这一条同时钉住三件事：连接经门面按后端建（含 PG 的 schema）、`PRAGMA journal_mode=WAL`
    不再是 API 里的 SQLite 专有语句、环境读走门面。
    """
    with two_stores(tmp_path, monkeypatch, pg_dsn, pg_schema) as (sq, sq_app, pg_conn, pg_app):
        _seed_env(sq)
        _seed_env(pg_conn)
        assert len(store.fetch_environment(pg_conn)) == len(ENV_ROWS), "先确认 PG 侧真有行"
        assert_same_answers(sq_app, pg_app, [
            ("/environment", {"zone": "611", "granularity": "hour"}),
            ("/environment", {"zone": "611", "granularity": "day"}),
            ("/environment", {"zone": "612"}),
            ("/environment", {"zone": "611", "start": "2026-08-17T00:00:00"}),
            ("/environment/summary", {"zone": "611"}),
            ("/environment/summary", {"zone": "999"}),
        ])


#: 测量行：B101 连续两个整点（跑 /growth 的速率），B201 只有一行（单点/空态路径）。
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


def test_ingest_and_measurement_reads_agree_on_both_backends(
    tmp_path, monkeypatch, pg_dsn, pg_schema
):
    """POST /ingest 写 measurements + /measurements、/growth、/growth/room 的读。

    写侧与读侧走的是同一条链（api → 门面 → 后端），所以这一条同时钉住
    `insert_measurements` 与 `fetch_measurements` 两处调用点切到门面后的行为。
    """
    with two_stores(tmp_path, monkeypatch, pg_dsn, pg_schema) as (sq, sq_app, pg_conn, pg_app):
        body = {"rows": [{"kind": "measurement", **row} for row in MEASUREMENT_ROWS]}
        with TestClient(sq_app) as a, TestClient(pg_app) as b:
            posted_a = a.post("/ingest", json=body).json()
            posted_b = b.post("/ingest", json=body).json()
            assert posted_b == posted_a, "两边入库计数与告警要一致"
            assert len(store.fetch_measurements(pg_conn)) == len(MEASUREMENT_ROWS), "PG 侧真有行"
            for url, params in [
                ("/measurements", {}),
                ("/measurements", {"room_id": "611"}),
                ("/measurements", {"box_id": "B101"}),
                ("/growth", {"box_id": "B101"}),
                ("/growth", {"box_id": "B999"}),
                ("/growth/room", {"boxes": "B101,B201"}),
            ]:
                assert b.get(url, params=params).json() == a.get(url, params=params).json(), \
                    f"{url}?{params} 两个后端的响应不一致"


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

#: B101 两个整点且值相等 —— /growth/room 里应当是 stalled（配 no_prev 的 B102）。
ROOM_MEASUREMENT_ROWS = [
    {"ts": "2026-08-16T08:00:00", "room_id": "611", "box_id": "B101", "station_id": "S101",
     "angle_profile": "level0", "n": 6, "mean_len_mm": 40.0, "mean_cap_mm": None,
     "p10_len": None, "p90_len": None, "quality": "verified"},
    {"ts": "2026-08-16T20:00:00", "room_id": "611", "box_id": "B101", "station_id": "S101",
     "angle_profile": "level0", "n": 6, "mean_len_mm": 40.2, "mean_cap_mm": None,
     "p10_len": None, "p90_len": None, "quality": "verified"},
]


def test_ingest_and_rounds_images_reads_agree_on_both_backends(
    tmp_path, monkeypatch, pg_dsn, pg_schema
):
    """/rounds、/images 与 /growth/room 的框清单（原来那条裸 SQL）在两个后端上逐字段相同。

    `/growth/room` 的框清单必须来自**图像索引**（拍了没测量的框不许消失），所以这一条同时
    钉住 `fetch_room_box_ids` 的门面落点：SQLite 的 `?` 占位与直写表名不能再出现在 api.py。
    """
    with two_stores(tmp_path, monkeypatch, pg_dsn, pg_schema) as (sq, sq_app, pg_conn, pg_app):
        rows = (
            [{"kind": "round", **row} for row in ROUND_ROWS]
            + [{"kind": "image_index", **row} for row in IMAGE_ROWS]
            + [{"kind": "measurement", **row} for row in ROOM_MEASUREMENT_ROWS]
        )
        with TestClient(sq_app) as a, TestClient(pg_app) as b:
            posted_a = a.post("/ingest", json={"rows": rows}).json()
            posted_b = b.post("/ingest", json={"rows": rows}).json()
            assert posted_b == posted_a, "两边入库计数与告警要一致"
            assert len(store.fetch_images(pg_conn)) == len(IMAGE_ROWS), "PG 侧真有行"
            for url, params in [
                ("/rounds", {}),
                ("/rounds", {"room_id": "611"}),
                ("/rounds", {"entry_date": "2026-08-16", "batch_no": "mogu-1"}),
                ("/images", {}),
                ("/images", {"room_id": "611"}),
                ("/images", {"box_id": "B101", "limit": 1}),
                ("/growth/room", {"room_id": "611"}),
                ("/growth/room", {"boxes": "B101,B102"}),
            ]:
                assert b.get(url, params=params).json() == a.get(url, params=params).json(), \
                    f"{url}?{params} 两个后端的响应不一致"
        boxes = store.fetch_room_box_ids(pg_conn, room_id="611")
        assert boxes == ["B101", "B102"], "PG 侧框清单去重升序（换成 %s 占位后行为不变）"


#: 台账物化缓存的两批（同一库房跨两个入库日）：/batches 的 until 与 /environment/batches 的窗口都要跑到。
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


def test_batches_endpoints_agree_on_both_backends(tmp_path, monkeypatch, pg_dsn, pg_schema):
    """/batches 与 /environment/batches：台账读 + 窗口切分在两个后端上逐字段相同。

    `replace_batches` 是**写侧**（batch_ingest 用），`fetch_batches`/`fetch_batches_state`
    是读侧（API 用）—— 两侧都在这一条里跑到。
    """
    with two_stores(tmp_path, monkeypatch, pg_dsn, pg_schema) as (sq, sq_app, pg_conn, pg_app):
        _seed_env(sq)
        _seed_env(pg_conn)
        _seed_batches(sq)
        _seed_batches(pg_conn)
        assert len(store.fetch_batches(pg_conn)) == len(BATCH_ROWS)
        assert store.fetch_batches_state(pg_conn)["refreshed_at"] == "2026-08-16T05:20:00"
        assert_same_answers(sq_app, pg_app, [
            ("/batches", {"room_id": "611"}),
            ("/batches", {"room_id": "612"}),
            ("/batches", {"room_id": "607"}),
            ("/environment/batches", {"zone": "611"}),
            ("/environment/batches", {"zone": "608"}),
        ])


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
def test_batch_map_cli_reverses_the_ledger_on_both_backends(backend, tmp_path, monkeypatch):
    """`python -m analysis.batch_map` 从本库既有行反推映射：两种后端上结果一样。

    映射文件是本地 JSON（不落库），这里只钉"读本库"那一半换了后端还通。
    """
    import json

    from analysis.batch_map import main

    out = tmp_path / "batch_no_map.json"
    with one_store(backend, tmp_path, monkeypatch) as target:
        conn = target.connect()
        try:
            _seed_batches(conn)
        finally:
            conn.close()

        main([*target.cli_args, "--out", str(out)])

    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["map"] == {
        "611|2026-08-16": "mogu-1", "611|2026-09-30": "mogu-2",
    }, "只有带 batch_no 的行进映射；缺的（612）不猜"


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
def test_batch_ingest_run_once_materializes_the_ledger_on_both_backends(
    backend, tmp_path, monkeypatch
):
    """`analysis.batch_ingest.run_once`（取数用假 runner 顶掉）在两后端上落同一份台账。"""
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

    with one_store(backend, tmp_path, monkeypatch) as target:
        conn = target.connect()
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


def test_ranges_pipeline_agrees_on_both_backends(tmp_path, monkeypatch, pg_dsn, pg_schema):
    """区间重算与 /ranges：PG 侧与 SQLite 侧产出**同一份区间行与状态**。

    这一条钉住票 28 第 2 步的三个挡路点：`pd.read_sql` 换成"经门面取行再拼 DataFrame"、
    `compute_ranges`/`refresh_ranges` 改成吃连接、`/ranges` 的读走门面。
    """
    with two_stores(tmp_path, monkeypatch, pg_dsn, pg_schema) as (sq, sq_app, pg_conn, pg_app):
        _seed_ranges(sq)
        _seed_ranges(pg_conn)
        stamp = "2026-08-16T02:00:00"
        n_pg = refresh_ranges(conn=pg_conn, computed_at=stamp)
        n_sq = refresh_ranges(conn=sq, computed_at=stamp)
        assert n_pg == n_sq > 0, "两边都算出区间行（不是「空表对空表」）"
        assert store.fetch_ranges(pg_conn) == store.fetch_ranges(sq)
        pg_state, sq_state = store.fetch_ranges_state(pg_conn), store.fetch_ranges_state(sq)
        assert pg_state["state"] == sq_state["state"] == "ok"
        # `updated_at` 是各自写库那一刻的墙钟（两边不可能同秒），其余字段必须一致。
        assert {k: v for k, v in pg_state.items() if k != "updated_at"} == {
            k: v for k, v in sq_state.items() if k != "updated_at"
        }
        assert_same_answers(sq_app, pg_app, [("/ranges", {})])


def test_the_path_shaped_entry_points_still_work(tmp_path, monkeypatch):
    """**老形状（按路径建连接）还在**：CLI 与老测试还这么传，第 4 步 contract 才删。

    这一条故意只跑 SQLite（不需要 DSN）：PG 下的调用方必须传连接，路径在 PG 下没有意义。
    """
    from analysis.align import compute_ranges

    monkeypatch.delenv("ANALYSIS_STORE", raising=False)
    path = str(tmp_path / "path-shaped.db")
    conn = store.connect(path)
    _seed_ranges(conn)
    conn.close()

    run = compute_ranges(path)
    assert run.state == "ok" and run.rows
    assert refresh_ranges(path, computed_at="2026-08-16T02:00:00") == len(run.rows)
    assert store.fetch_ranges_state(store.connect(path))["state"] == "ok"


def test_ranges_job_recomputes_on_pg_through_the_shared_connection(pg, monkeypatch):
    """`RANGES_AT` 装出来的 ranges 作业**拿 API 那一份连接**重算（PG 上不再按路径重连）。

    作业原来调 `refresh_ranges(db_path)`：PG 下没有"路径"这回事，那种重连要么连错库、
    要么丢掉 schema。这里跑一轮真实的重算，确认区间行与状态都落在同一个 schema 里。
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


#: 操作日志：同一设备点位跨日的变更（/setpoints/daily 的 changed/carried 都要跑到）。
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


def test_operations_and_daily_setpoints_agree_on_both_backends(
    tmp_path, monkeypatch, pg_dsn, pg_schema
):
    """/operations 与 /setpoints/daily：逐条不聚合的一侧与"当天末态"的一侧，两边逐字段相同。

    `/setpoints/daily` 走 `setpoints.daily_states_from_db`（事件 + 窗口前基线两条查询），
    所以这一条同时钉住 api 与 setpoints 两处调用点。
    """
    with two_stores(tmp_path, monkeypatch, pg_dsn, pg_schema) as (sq, sq_app, pg_conn, pg_app):
        store.upsert_operations(sq, OPERATION_ROWS)
        store.upsert_operations(pg_conn, OPERATION_ROWS)
        assert len(store.fetch_operations(pg_conn, zone="611")) == len(OPERATION_ROWS)
        assert_same_answers(sq_app, pg_app, [
            ("/operations", {"zone": "611"}),
            ("/operations", {"zone": "611", "limit": 2}),
            ("/operations", {"zone": "612"}),
            ("/setpoints/daily", {"zone": "611", "start": "2026-08-16", "end": "2026-08-17"}),
            ("/setpoints/daily", {"zone": "611", "start": "2026-08-17", "end": "2026-08-17",
                                  "entry_date": "2026-08-16"}),
        ])


@contextmanager
def one_store(backend, tmp_path, monkeypatch):
    """**单后端**的一个临时库：SQLite 用临时文件，PG 用临时 schema（跑完删）。

    给"不进 HTTP"的用例（CLI、批处理入口）用：`connect_kwargs` 与 `cli_args` 按后端给，
    用例只换后端名 —— 这就是"原来只走 SQLite 的场景补上 PG 参数化"的落点。
    """
    if backend == "postgres":
        dsn = pg_harness.test_dsn()
        if dsn is None:
            pytest.skip("没有测试 DSN：PG 侧这一半跳过")
        schema = pg_harness.new_schema_name()
        monkeypatch.setenv("ANALYSIS_STORE", "postgres")
        try:
            yield _Target({"dsn": dsn, "schema": schema}, ["--dsn", dsn, "--schema", schema])
        finally:
            pg_harness.drop_schema(dsn, schema)
    else:
        monkeypatch.delenv("ANALYSIS_STORE", raising=False)
        path = str(tmp_path / "store.db")
        yield _Target({"db_path": path}, ["--db", path])


@dataclass(frozen=True)
class _Target:
    """一次用例要打的那个库：门面连接参数 + CLI 的同义参数。"""

    connect_kwargs: dict
    cli_args: list[str]

    def connect(self):
        return store.connect(**self.connect_kwargs)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
def test_env_ingest_cli_writes_through_the_facade(backend, tmp_path, monkeypatch):
    """`python -m analysis.env.ingest --once` 在两种后端上都能把小时聚合写进去。

    PG 侧没有 DSN 就 skip 这一半（SQLite 那一半照旧跑）—— 同一个 CLI、同一份 CSV，
    只换连接参数。
    """
    from analysis.env.ingest import main

    csv_dir = tmp_path / "env_csv"
    csv_dir.mkdir()
    (csv_dir / "a.csv").write_text(
        "ts,zone,temp_c,rh_pct,co2_ppm,lamp_state\n"
        "2026-08-30T08:00:00,611,23.5,85.0,800,120\n"
        "2026-08-30T09:00:00,611,24.0,88.0,900,150\n",
        encoding="utf-8",
    )

    with one_store(backend, tmp_path, monkeypatch) as target:
        main([*target.cli_args, "--source", "csv", "--path", str(csv_dir), "--once"])
        conn = target.connect()
        try:
            rows = store.fetch_environment(conn, zone="611")
        finally:
            conn.close()
        assert [(r["ts"], r["temp_c"]) for r in rows] == [
            ("2026-08-30T08:00:00", 23.5), ("2026-08-30T09:00:00", 24.0),
        ], "CLI 写进去的小时聚合要能被门面读回来"


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
def test_audit_ingest_run_once_writes_operations_on_both_backends(
    backend, tmp_path, monkeypatch
):
    """`analysis.audit_ingest.run_once`（操作日志入库）在两种后端上写入同样多的行。

    环控平台的调用用假 transport 顶掉：这里要证的是"写侧换成门面后 PG 也写得进去"，
    不是再测一次接口解析（那在 `test_audit.py`）。
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

    with one_store(backend, tmp_path, monkeypatch) as target:
        conn = target.connect()
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
