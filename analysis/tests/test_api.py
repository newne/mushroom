import pytest
from analysis.api import create_app
from fastapi.testclient import TestClient


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "api.db")


@pytest.fixture
def client(db_path):
    app = create_app(db_path)
    return TestClient(app)


def test_healthz(client):
    assert client.get("/healthz").json() == {"ok": True}


def test_ingest_measurements_and_rounds(client):
    body = {"rows": [
        {"kind": "measurement", "ts": "2026-08-30T08:00:00", "box_id": "B01",
         "n": 3, "mean_len_mm": 52.0, "mean_cap_mm": 31.0,
         "p10_len": 48.0, "p90_len": 56.0, "quality": ""},
        {"kind": "round", "ts": "2026-08-30T08:00:00", "ok": True,
         "n_results": 4, "n_failures": 0, "aborted": False},
        {"kind": "image_index", "ts": "2026-08-30T08:00:05", "station_id": "S101",
         "box_id": "B101", "angle_profile": "top45", "camera_ip": "192.168.1.238",
         "yz": [187.17, -21.2], "object_name": "20260830/B101_S101_top45_080005.jpg",
         "cloud_url": "http://minio/mogu/x.jpg", "ok": True},
        {"kind": "junk", "ts": "x"},  # 未知类型忽略
    ]}
    resp = client.post("/ingest", json=body)
    assert resp.status_code == 200
    assert resp.json() == {
        "inserted_measurements": 1, "inserted_rounds": 1, "inserted_images": 1,
    }

    ms = client.get("/measurements").json()
    assert len(ms) == 1
    assert ms[0]["box_id"] == "B01"
    rounds = client.get("/rounds").json()
    assert rounds[0]["ok"] in (True, 1)

    # ADR-0005：图像索引一帧一行，且 patrol 的 yz 列表被摊平成 y / z 两列
    imgs = client.get("/images").json()
    assert len(imgs) == 1
    assert imgs[0]["station_id"] == "S101"
    assert (imgs[0]["y"], imgs[0]["z"]) == (187.17, -21.2)
    assert imgs[0]["object_name"].endswith(".jpg")


def test_ingest_failed_station_index_keeps_null_object_name(client):
    """采图失败的站位也要留一行索引（object_name 为空）——"这一帧没拍成"同样是事实。"""
    rows = [{"kind": "image_index", "ts": "2026-08-30T08:00:05", "station_id": "S102",
             "box_id": "B102", "ok": False, "error": "采图链路失败: refused"}]
    assert client.post("/ingest", json={"rows": rows}).json()["inserted_images"] == 1
    imgs = client.get("/images", params={"station_id": "S102"}).json()
    assert len(imgs) == 1
    assert imgs[0]["object_name"] is None
    assert imgs[0]["ok"] in (False, 0)
    assert "refused" in imgs[0]["error"]


def test_images_endpoint_filters_by_station(client):
    rows = [
        {"kind": "image_index", "ts": "2026-08-30T08:00:05", "station_id": "S101", "ok": True},
        {"kind": "image_index", "ts": "2026-08-30T08:00:10", "station_id": "S102", "ok": True},
    ]
    client.post("/ingest", json={"rows": rows})
    assert len(client.get("/images").json()) == 2
    only = client.get("/images", params={"station_id": "S102"}).json()
    assert [i["station_id"] for i in only] == ["S102"]


def test_ingest_missing_required_fields_ignored(client):
    resp = client.post("/ingest", json={"rows": [
        {"kind": "measurement", "box_id": "B01"},  # 缺 ts
    ]})
    assert resp.json()["inserted_measurements"] == 0


def test_ingest_idempotent(client):
    row = {"kind": "measurement", "ts": "t1", "box_id": "B01", "mean_len_mm": 1.0}
    client.post("/ingest", json={"rows": [row]})
    client.post("/ingest", json={"rows": [row]})
    assert len(client.get("/measurements").json()) == 1


def test_ranges_empty_db(client):
    # 批处理未运行时 ranges 表为空，端点只读不重算
    assert client.get("/ranges").json() == []


def test_ranges_reads_materialized_table(client, db_path):
    """评审 #6：批处理写入 ranges 表，API 只读该表（不再每请求重算）。"""
    from analysis.align import refresh_ranges
    from analysis.db import connect, fetch_ranges, upsert_environment

    # 灌 24h 合成数据（温度在 22/26 间交替，保证分箱可分）
    conn = connect(db_path)
    rows = []
    for i in range(24):
        ts = f"2026-08-01T{i:02d}:00:00"
        rows.append({"ts": ts, "box_id": "B01", "mean_len_mm": 10.0 + i})
        upsert_environment(conn, [{"ts": ts, "zone": "room1",
                                   "temp_c": 22.0 + (i % 2) * 4.0,
                                   "rh_pct": 85.0, "co2_ppm": 800.0, "light_lux": 100.0}])
    from analysis.db import insert_measurements

    insert_measurements(conn, rows)

    n = refresh_ranges(db_path, computed_at="2026-08-02T00:00:00")
    assert n >= 1

    ranges = client.get("/ranges").json()
    assert ranges == fetch_ranges(connect(db_path))
    assert ranges[0]["computed_at"] == "2026-08-02T00:00:00"
