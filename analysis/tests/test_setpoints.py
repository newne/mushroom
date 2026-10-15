"""票 09：每日设定末态 —— `changed` / `carried` / `unknown` 三态必须分得开。

票面验收原话：要覆盖"一天多改 / 跨日未改 / 批次首日"三种情况。
最容易错的一条是把**沿用**（当天没改、值还在）和**未知**（从没设过）混成一个"没有值"：
前者是"一直在用某个设定"，后者是"不知道"——混起来下游会以为这段时间没有设定。
"""

from __future__ import annotations

import pytest

from analysis.setpoints import SetpointError, daily_states, day_range
from analysis.store import upsert_operations


def op(ts: str, *, device="TD1_Q3MDHUM02", point="1", value="5", **extra) -> dict:
    return {
        "ts": ts, "zone": extra.pop("zone", "607"), "device_code": device,
        "device_name": extra.pop("device_name", "607库-右加湿器"),
        "point_code": point, "point_name": extra.pop("point_name", "加湿开启设定"),
        "value": value, "operator": extra.pop("operator", "张三"), **extra,
    }


def test_day_range_rejects_bad_windows():
    assert day_range("2026-09-30", "2026-10-02") == [
        "2026-09-30", "2026-10-01", "2026-10-02"
    ]
    with pytest.raises(SetpointError):
        day_range("2026-10-02", "2026-09-30")
    with pytest.raises(SetpointError):
        day_range("2026-01-01", "2027-01-01"), "超长窗口要报错，不静默截断"
    with pytest.raises(SetpointError):
        day_range("2026-13-01", "2026-13-02")


def test_one_day_with_several_changes_takes_the_last():
    events = [op("2026-10-01T08:10:00", value="3"), op("2026-10-01T20:40:00", value="7")]
    got = daily_states(events, start="2026-10-01", end="2026-10-01")
    row = got["rows"][0]
    assert row["state"] == "changed"
    assert row["value"] == "7", "当天最后一条为准，不是第一条、也不是平均"
    assert row["since"] == "2026-10-01T20:40:00"
    assert row["n_changes_today"] == 2


def test_carried_forward_is_not_unknown():
    """跨日未改 ⇒ `carried`，并且能说出是**从哪天**沿用来的。"""
    events = [op("2026-10-01T08:10:00", value="5")]
    got = daily_states(events, start="2026-10-01", end="2026-10-03")
    states = [(r["day"], r["state"], r["value"]) for r in got["rows"]]
    assert states == [
        ("2026-10-01", "changed", "5"),
        ("2026-10-02", "carried", "5"),
        ("2026-10-03", "carried", "5"),
    ]
    assert got["rows"][1]["since_day"] == "2026-10-01"
    assert got["summary"] == {
        "n_rows": 3, "n_changed": 1, "n_carried": 2, "n_unknown": 0,
        "n_days": 3, "n_points": 1, "n_events": 1, "n_baseline": 0,
        "first_day_rows": 0,
    }


def test_baseline_before_the_window_decides_carried_vs_unknown():
    """窗口**起点之前**改过一次 ⇒ 窗口内第一天是"沿用"，不是"未知"。"""
    baseline = [op("2026-09-20T09:00:00", value="4")]
    got = daily_states([], start="2026-10-01", end="2026-10-01", baseline=baseline)
    row = got["rows"][0]
    assert row["state"] == "carried" and row["value"] == "4"
    assert row["since_day"] == "2026-09-20"


def test_never_seen_point_is_unknown_with_null_not_zero():
    """从没设过 ⇒ `unknown` + **NULL**。写 0/默认值就是编。"""
    got = daily_states([], start="2026-10-01", end="2026-10-01")
    assert got["rows"] == [], "没有任何点位（连基线都没有）⇒ 空表，不是造一行"

    got = daily_states(
        [op("2026-10-02T10:00:00", value="9")], start="2026-10-01", end="2026-10-02"
    )
    first, second = got["rows"]
    assert (first["day"], first["state"], first["value"]) == ("2026-10-01", "unknown", None)
    assert (second["day"], second["state"], second["value"]) == ("2026-10-02", "changed", "9")
    assert got["summary"]["n_unknown"] == 1


def test_batch_first_day_and_two_growth_day_flavours():
    """票 19：入库当天 = 0（我们）/ = `01`（生产口径），并列 + 首日标记，绝不合并。"""
    events = [op("2026-09-30T07:00:00", value="2")]
    got = daily_states(
        events, start="2026-09-29", end="2026-10-02", entry_date="2026-09-30"
    )
    rows = {r["day"]: r for r in got["rows"]}
    assert rows["2026-09-29"]["first_day"] is False
    assert rows["2026-09-29"]["growth_day"] is None or rows["2026-09-29"]["growth_day"] == -1
    assert (rows["2026-09-30"]["first_day"], rows["2026-09-30"]["growth_day"],
            rows["2026-09-30"]["growth_day_prod"]) == (True, 0, "01")
    assert rows["2026-10-02"]["growth_day"] == 2 and rows["2026-10-02"]["growth_day_prod"] == "03"
    assert got["summary"]["first_day_rows"] == 1
    assert got["summary"]["n_rows"] == 4, "首日行**不删**（剔除只发生在统计/学习侧）"


def test_multiple_points_are_kept_separate():
    events = [op("2026-10-01T08:00:00", point="1", value="5"),
              op("2026-10-01T09:00:00", point="2", value="12",
                 point_name="加湿停止设定", device="TD1_Q3MDHUM01")]
    got = daily_states(events, start="2026-10-01", end="2026-10-01")
    assert {(r["device_code"], r["point_code"], r["value"]) for r in got["rows"]} == {
        ("TD1_Q3MDHUM02", "1", "5"), ("TD1_Q3MDHUM01", "2", "12"),
    }


# ---------- 接口层（含窗口起点之前的基线，走真库） ----------


def test_api_daily_endpoint_reads_operations_only(client, pg):
    upsert_operations(pg, [
        op("2026-09-20T09:00:00", value="4"),      # 窗口外：作基线
        op("2026-10-01T08:10:00", value="3"),
        op("2026-10-01T20:40:00", value="7"),
    ])
    got = client.get(
        "/setpoints/daily", params={"zone": "607", "start": "2026-10-01", "end": "2026-10-02"}
    ).json()
    assert got["ok"] is True and got["days"] == ["2026-10-01", "2026-10-02"]
    first, second = got["rows"]
    assert (first["state"], first["value"]) == ("changed", "7")
    assert (second["state"], second["value"]) == ("carried", "7")
    assert got["summary"]["n_baseline"] == 1, "基线取到了，所以第一天不是未知"


def test_api_daily_endpoint_validates_inputs(client):
    assert client.get("/setpoints/daily", params={"start": "2026-10-01", "end": "2026-10-02"}).status_code == 400
    assert client.get("/setpoints/daily", params={"zone": "607"}).status_code == 400
    bad = client.get("/setpoints/daily",
                     params={"zone": "607", "start": "2026-10-02", "end": "2026-10-01"})
    assert bad.status_code == 400 and "早于" in bad.json()["detail"]


def test_api_daily_endpoint_does_not_touch_device_setpoint_changes(client):
    """ADR-0022：事件源只有 `operations`。产物里不许出现另一套来源的字段。"""
    body = client.get(
        "/setpoints/daily", params={"zone": "607", "start": "2026-10-01", "end": "2026-10-01"}
    ).json()
    text = str(body)
    assert "device_setpoint_changes" not in text
    assert "current_value" not in text, "那是旧表的列名，不许混进来"
