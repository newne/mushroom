"""环境序列的聚合、读接口与"原始不落库"（票 01/02）。"""

import json

import pytest
from fastapi.testclient import TestClient

from analysis.api import create_app
from analysis.db import connect, upsert_batches, upsert_environment
from analysis.env.adapters import EnvRow
from analysis.environment import aggregate_hourly, batches, series, summary


def _hours(count=4, zone="611", lamp=2.0, n=60):
    """库里存的是**小时聚合行**，所以 fixture 直接给聚合形状。"""
    return [
        {
            "ts": f"2026-10-12T{h:02d}:00:00",
            "zone": zone,
            "temp_c": 19.0 + h,
            "temp_min": 18.5 + h,
            "temp_max": 19.6 + h,
            "rh_pct": 90.0,
            "rh_min": 89.0,
            "rh_max": 91.0,
            "co2_ppm": 1500.0 + h,
            "co2_min": 1480.0 + h,
            "co2_max": 1520.0 + h,
            "lamp_state": lamp,
            "n": n,
        }
        for h in range(count)
    ]


@pytest.fixture
def client(tmp_path):
    db = str(tmp_path / "t.db")
    conn = connect(db)
    upsert_environment(conn, _hours())
    conn.close()
    return TestClient(create_app(db))


# ---------- 入库前的聚合 ----------


def test_aggregate_hourly_mean_min_max_n_and_mode():
    minutes = [
        {"ts": f"2026-10-12T15:{m:02d}:00", "zone": "611", "temp_c": 19.0 + m / 10.0,
         "rh_pct": 92.0, "co2_ppm": 1600.0 + m, "lamp_state": 2.0}
        for m in range(60)
    ]
    rows = aggregate_hourly(minutes)
    assert len(rows) == 1
    row = rows[0]
    assert row["ts"] == "2026-10-12T15:00:00" and row["zone"] == "611"
    assert row["n"] == 60
    assert row["temp_min"] == 19.0 and row["temp_max"] == 24.9
    assert row["temp_c"] == round(sum(19.0 + m / 10 for m in range(60)) / 60, 2)
    assert row["co2_min"] == 1600.0 and row["co2_max"] == 1659.0
    assert row["lamp_state"] == 2.0


def test_aggregate_hourly_flags_partial_hour():
    minutes = [{"ts": f"2026-10-12T17:{m:02d}:00", "zone": "611", "temp_c": 19.0}
               for m in range(11)]
    row = aggregate_hourly(minutes)[0]
    assert row["n"] == 11            # 不足 60：断档在小数上看得见，不补
    assert row["rh_pct"] is None


def test_lamp_minutes_follow_on_off_cycle():
    # 开 20 秒 / 停 40 秒 → 占空比 1/3；60 个采样点 ⇒ 20 分钟
    rows = aggregate_hourly([
        {"ts": f"2026-10-12T15:{m:02d}:00", "zone": "611", "temp_c": 19.0,
         "lamp_state": 0.0, "lamp_on_s": 20.0, "lamp_off_s": 40.0}
        for m in range(60)
    ])
    assert rows[0]["lamp_on_min"] == 20.0
    assert rows[0]["lamp_on_s"] == 20.0 and rows[0]["lamp_off_s"] == 40.0


def test_lamp_minutes_use_observed_samples_not_a_full_hour():
    # 断档小时：只有 15 个采样点，按采样点数折算而不是按 60 分钟虚报
    rows = aggregate_hourly([
        {"ts": f"2026-10-12T17:{m:02d}:00", "zone": "611", "lamp_on_s": 0.0, "lamp_off_s": 60.0}
        for m in range(15)
    ])
    assert rows[0]["n"] == 15
    assert rows[0]["lamp_on_min"] == 0.0


def test_lamp_minutes_fall_back_per_side_to_minutes():
    # 现场实测组合：OnSSet=2 秒、OffSSet=0、OffMSet=120 分（=2 小时）
    # ⇒ 开 2 秒 / 停 7200 秒，一小时 60 个采样点折算约 0.0167 分钟
    rows = aggregate_hourly([
        {"ts": f"2026-10-12T15:{m:02d}:00", "zone": "611",
         "lamp_on_s": 2.0, "lamp_off_s": 0.0, "lamp_on_m": 0.0, "lamp_off_m": 120.0}
        for m in range(60)
    ])
    assert rows[0]["lamp_on_min"] == round(60 * (2.0 / 7202.0), 4)
    assert rows[0]["lamp_off_m"] == 120.0        # 四个原值都要留着给页面看


def test_lamp_minutes_are_unknown_when_cycle_is_unset():
    # 0/0 是"没配循环"，不是"补光为零"
    rows = aggregate_hourly([{"ts": "2026-10-12T15:00:00", "zone": "611",
                              "lamp_on_s": 0.0, "lamp_off_s": 0.0}])
    assert rows[0]["lamp_on_min"] is None
    rows = aggregate_hourly([{"ts": "2026-10-12T15:00:00", "zone": "611"}])
    assert rows[0]["lamp_on_min"] is None


def test_day_lamp_minutes_are_summed_not_averaged():
    rows = series([
        {"ts": "2026-10-12T10:00:00", "zone": "611", "n": 60, "lamp_on_min": 20.0},
        {"ts": "2026-10-12T11:00:00", "zone": "611", "n": 60, "lamp_on_min": 35.0},
    ], granularity="day")
    assert rows[0]["lamp_on_min"] == 55.0      # 时长跨小时相加


def test_summary_reports_lamp_duty_and_totals():
    got = summary([
        {"ts": "2026-10-12T10:00:00", "zone": "611", "n": 60,
         "lamp_on_min": 20.0, "lamp_on_s": 20.0, "lamp_off_s": 40.0},
        {"ts": "2026-10-12T11:00:00", "zone": "611", "n": 60,
         "lamp_on_min": 20.0, "lamp_on_s": 20.0, "lamp_off_s": 40.0},
    ])
    assert got["lamp"]["on_min_total"] == 40.0
    assert got["lamp"]["on_min_per_hour"] == 20.0
    assert abs(got["lamp"]["duty"] - 20 / 60) < 1e-6


def test_batches_uses_ledger_windows_not_data_gaps():
    """数据是连续的也要按台账切——"按断档切段"在真机上被证否过（跨两批会切成一段）。"""
    rows = [
        {"ts": "2026-09-20T00:00:00", "zone": "611", "temp_c": 18.0, "n": 60},
        {"ts": "2026-09-21T00:00:00", "zone": "611", "temp_c": 18.5, "n": 60},
        {"ts": "2026-10-01T00:00:00", "zone": "611", "temp_c": 20.0, "n": 60},
        {"ts": "2026-10-02T00:00:00", "zone": "611", "temp_c": 20.5, "n": 60},
    ]
    windows = [
        {"entry_date": "2026-09-30", "until": None, "batch_no": "mogu-107"},
        {"entry_date": "2026-08-16", "until": "2026-09-30", "batch_no": "mogu-101"},
    ]
    got = batches(rows, windows, zone="611")
    assert len(got) == 2
    assert got[0]["batch_no"] == "mogu-107"
    assert got[0]["params"]["temp_c"]["mean"] == 20.25
    assert got[1]["params"]["temp_c"]["mean"] == 18.25
    assert got[1]["start"] == "2026-09-20T00:00:00"


def test_batches_skips_windows_without_data():
    got = batches([{"ts": "2026-10-01T00:00:00", "zone": "611", "n": 60}],
                  [{"entry_date": "2026-01-01", "until": "2026-02-01", "batch_no": "old"}],
                  zone="611")
    assert got == []


def test_api_batches_needs_ledger(tmp_path):
    db = str(tmp_path / "t.db")
    conn = connect(db)
    upsert_environment(conn, _hours(count=4))
    conn.close()
    got = TestClient(create_app(db)).get("/environment/batches", params={"zone": "611"}).json()
    # 没同步过台账就直说，不退回"按断档切段"编一个批次出来
    assert got["ok"] is False and "batch_ingest" in got["error"] and got["batches"] == []


def test_api_batches_with_ledger(tmp_path):
    db = str(tmp_path / "t.db")
    conn = connect(db)
    upsert_environment(conn, _hours(count=4))          # 2026-10-12 的 4 个小时
    upsert_batches(conn, [{"room_id": "611", "entry_date": "2026-09-30",
                           "batch_no": "mogu-107", "batch_id": "107"}])
    conn.close()
    got = TestClient(create_app(db)).get("/environment/batches", params={"zone": "611"}).json()
    assert got["ok"] is True and len(got["batches"]) == 1
    assert got["batches"][0]["batch_no"] == "mogu-107" and got["batches"][0]["hours"] == 4
    assert got["batches"][0]["room_label"] == "611", "规范号不洗白成别的写法"
    assert got["room_alias"]["n_unresolved"] == 0


def _legacy_room_fixture(tmp_path):
    """台账里 607 那间挂着旧编号 `7`，环境侧用 `607` —— 跨域 join 的真实形状。"""
    db = str(tmp_path / "legacy.db")
    conn = connect(db)
    upsert_environment(conn, _hours(count=4, zone="607"))
    upsert_batches(conn, [{"room_id": "7", "entry_date": "2026-09-30",
                           "batch_no": None, "batch_id": "7|2026-09-30"}])
    conn.close()
    return db


def test_api_batches_resolves_the_legacy_room_code(tmp_path):
    """默认别名表已确证 `7→607`（发版官 2026-10-14）⇒ join 直接成立，标签明写"原始 7"。"""
    db = _legacy_room_fixture(tmp_path)
    got = TestClient(create_app(db)).get("/environment/batches", params={"zone": "607"}).json()
    assert got["ok"] is True and len(got["batches"]) == 1
    assert got["batches"][0]["hours"] == 4
    assert got["batches"][0]["room_label"] == "607（原始 7）", "显示了规范号就必须说明原始是 7"
    assert got["batches"][0]["room_id"] == "7", "原始列不许洗白"
    assert got["room_alias"]["n_resolved"] == 1 and got["room_alias"]["n_unresolved"] == 0


def test_api_batches_reports_unresolved_room_code_instead_of_saying_no_ledger(tmp_path, monkeypatch, capsys):
    """别名表里没有的编号 ⇒ 报"解不出"，**不许**说成"还没有批次台账"。

    用一份空 map 的表来模拟"还没确证"的状态（默认表已确证，不能拿它测这条路径）。
    """
    db = _legacy_room_fixture(tmp_path)
    alias = tmp_path / "empty_alias.json"
    alias.write_text(json.dumps({"map": {}}), encoding="utf-8")
    monkeypatch.setenv("ROOM_ALIAS_FILE", str(alias))

    got = TestClient(create_app(db)).get("/environment/batches", params={"zone": "607"}).json()
    assert got["ok"] is False
    assert "batch_ingest" not in got["error"], "台账明明在，只是挂着旧编号"
    assert "'7'" in got["error"] and "不猜" in got["error"]
    assert got["room_alias"]["unresolved_values"] == {"7": 1}
    assert got["room_alias"]["unresolved_samples"] == [{"index": 0, "raw": "7"}]
    printed = capsys.readouterr().out
    assert "库房号解不出" in printed, "解不出要打样例，不许静默 NULL"


def test_api_batches_uses_an_explicit_alias_file(tmp_path, monkeypatch):
    """换一份别名表也能生效（口径只从文件读，不写死在代码里）。"""
    db = _legacy_room_fixture(tmp_path)
    alias = tmp_path / "room_alias.json"
    alias.write_text(json.dumps({"map": {
        "7": {"to": "607", "evidence": "现场库房对照表 2026-10-15", "confirmed_by": "现场"}
    }}), encoding="utf-8")
    monkeypatch.setenv("ROOM_ALIAS_FILE", str(alias))

    got = TestClient(create_app(db)).get("/environment/batches", params={"zone": "607"}).json()
    assert got["ok"] is True and got["batches"][0]["room_label"] == "607（原始 7）"
    assert got["room_alias"]["n_resolved"] == 1


def test_api_batches_rejects_a_non_room_zone(tmp_path):
    db = _legacy_room_fixture(tmp_path)
    got = TestClient(create_app(db)).get("/environment/batches", params={"zone": "999"})
    assert got.status_code == 400 and "不是规范库房号" in got.json()["detail"]


def test_aggregate_hourly_splits_by_zone():
    rows = aggregate_hourly([
        {"ts": "2026-10-12T15:00:00", "zone": "611", "temp_c": 19.0},
        {"ts": "2026-10-12T15:30:00", "zone": "612", "temp_c": 20.0},
    ])
    assert {r["zone"] for r in rows} == {"611", "612"}


# ---------- 读：hour / day ----------


def test_hour_series_passes_through_stored_aggregate(client):
    got = series(_hours(count=2), granularity="hour")
    assert len(got) == 2
    assert got[0]["ts"] == "2026-10-12T00:00:00"
    assert got[0]["temp_c"] == 19.0 and got[0]["temp_min"] == 18.5
    assert got[0]["n"] == 60


def test_day_series_weights_hours_by_sample_count():
    rows = [
        {"ts": "2026-10-12T10:00:00", "zone": "611", "temp_c": 20.0, "n": 60, "lamp_state": 2.0},
        {"ts": "2026-10-12T11:00:00", "zone": "611", "temp_c": 30.0, "n": 60, "lamp_state": 3.0},
        {"ts": "2026-10-12T12:00:00", "zone": "611", "temp_c": 30.0, "n": 12, "lamp_state": 3.0},
    ]
    got = series(rows, granularity="day")
    assert len(got) == 1
    # 按采样点数加权：(20*60 + 30*60 + 30*12) / 132；等权会算成 26.67，那是断档小时在拽偏
    assert got[0]["temp_c"] == round((20 * 60 + 30 * 60 + 30 * 12) / 132, 2)
    assert got[0]["n"] == 132 and got[0]["hours"] == 3
    assert got[0]["lamp_state"] == 3.0


def test_raw_is_not_in_the_store():
    with pytest.raises(ValueError, match="raw"):
        series(_hours(), granularity="raw")


def test_bad_granularity_rejected():
    with pytest.raises(ValueError):
        series(_hours(), granularity="week")


# ---------- 读：统计 ----------


def test_summary_reports_extremes_quantiles_and_gaps():
    data = [
        {"ts": "2026-10-12T00:00:00", "zone": "611", "temp_c": 19.0, "temp_min": 18.5,
         "temp_max": 19.5, "rh_pct": 90.0, "co2_ppm": 1500.0, "lamp_state": 2.0, "n": 60},
        {"ts": "2026-10-12T03:00:00", "zone": "611", "temp_c": 22.0, "temp_min": 21.5,
         "temp_max": 22.5, "rh_pct": 93.0, "co2_ppm": 1600.0, "lamp_state": 2.0, "n": 60},
    ]
    got = summary(data, zone="611")
    assert got["n"] == 120 and got["hours"] == 2
    assert got["params"]["temp_c"]["median"] == 20.5     # 每小时一个点的分位数
    assert got["params"]["temp_c"]["min"] == 18.5        # 极值来自采样点级别
    assert got["params"]["temp_c"]["max"] == 22.5
    assert got["gaps"] == ["2026-10-12T01:00:00", "2026-10-12T02:00:00"]
    assert got["lamp_states"] == {"2": 120}


def test_summary_lists_incomplete_hours():
    got = summary([{"ts": "2026-10-12T17:00:00", "zone": "611", "temp_c": 19.0, "n": 11}])
    assert got["incomplete"] == [{"ts": "2026-10-12T17:00:00", "n": 11}]


def test_summary_flags_missing_source_without_faking_zero():
    data = [{"ts": "2026-10-12T00:00:00", "zone": "611", "temp_c": 19.0,
             "rh_pct": None, "co2_ppm": None, "lamp_state": None, "n": 60}]
    got = summary(data, zone="611")
    assert "lamp_state" in got["sources_missing"]
    assert "co2_ppm" in got["sources_missing"]
    assert got["params"]["temp_c"]["max"] == 19.0
    assert got["params"]["co2_ppm"] is None


def test_empty_window_is_empty_not_zero():
    got = summary([], zone="611")
    assert got["n"] == 0 and got["params"] == {} and got["sources_missing"]
    assert series([], granularity="hour") == []


# ---------- HTTP ----------


def test_api_environment_hour(client):
    got = client.get("/environment", params={"zone": "611", "granularity": "hour"}).json()
    assert got["granularity"] == "hour" and got["source"] == "aggregate"
    assert len(got["rows"]) == 4
    assert got["rows"][0]["temp_c"] == 19.0


def test_api_environment_summary(client):
    got = client.get("/environment/summary", params={"zone": "611"}).json()
    assert got["n"] == 240 and got["hours"] == 4
    assert got["params"]["temp_c"]["min"] == 18.5
    assert got["lamp_states"] == {"2": 240}


def test_api_environment_window_filter(client):
    got = client.get(
        "/environment", params={"zone": "611", "start": "2026-10-12T02:00:00"}
    ).json()
    assert [r["ts"] for r in got["rows"]] == ["2026-10-12T02:00:00", "2026-10-12T03:00:00"]


def test_api_environment_empty_is_empty_list(tmp_path):
    db = str(tmp_path / "empty.db")
    connect(db).close()
    got = TestClient(create_app(db)).get("/environment", params={"zone": "611"}).json()
    assert got["rows"] == []


# ---------- 原始序列：不落库，按窗口现取 ----------


class _FakeRawSource:
    def __init__(self, zone, seen):
        self.zone = zone
        self.seen = seen

    def fetch_since(self, since_ts):
        self.seen["zone"] = self.zone
        self.seen["since"] = since_ts
        return [EnvRow(ts="2026-10-12T15:03:00", zone=self.zone, temp_c=19.9,
                       rh_pct=92.4, co2_ppm=1639.0, lamp_state=2.0)]


def _raw_client(tmp_path, seen):
    db = str(tmp_path / "raw.db")
    connect(db).close()
    return TestClient(
        create_app(db, raw_source_factory=lambda zone: _FakeRawSource(zone, seen))
    )


def test_raw_is_fetched_live_not_from_store(tmp_path):
    seen = {}
    client = _raw_client(tmp_path, seen)
    got = client.get(
        "/environment",
        params={"zone": "611", "granularity": "raw", "start": "2026-10-12T14:00:00",
                "end": "2026-10-12T16:00:00"},
    ).json()
    assert got["source"] == "tdengine" and got["granularity"] == "raw"
    assert got["rows"][0]["temp_c"] == 19.9
    assert seen["zone"] == "611" and seen["since"] == "2026-10-12T14:00:00"


def test_raw_needs_a_window(tmp_path):
    client = _raw_client(tmp_path, {})
    assert client.get("/environment", params={"granularity": "raw"}).status_code == 400


def test_raw_window_is_capped(tmp_path):
    client = _raw_client(tmp_path, {})
    got = client.get("/environment", params={
        "zone": "611", "granularity": "raw",
        "start": "2026-10-01T00:00:00", "end": "2026-10-12T00:00:00",
    })
    assert got.status_code == 400 and "hour" in got.json()["detail"]
