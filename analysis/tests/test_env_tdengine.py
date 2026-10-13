"""TDengine 环境源的解析与对齐（票 01）。"""

import pytest

from analysis.env.tdengine import (
    TdengineEnvSource,
    merge_room,
    parse_rest,
    to_local,
    to_utc,
)


def envelope(rows, columns):
    return {"code": 0, "column_meta": [["ts", "TIMESTAMP", 8]] + [[c, "FLOAT", 4] for c in columns],
            "data": rows}


def test_to_local_is_beijing_time():
    # TDengine REST 给的是 UTC；落库与 rounds/images 一样用本地时间
    assert to_local("2026-10-12T08:19:54.266Z") == "2026-10-12T16:19:54"
    assert to_utc("2026-10-12T16:19:54") == "2026-10-12T08:19:54.000Z"


def test_parse_rest_refuses_non_zero_code():
    with pytest.raises(RuntimeError, match="9730"):
        parse_rest({"code": 9730, "desc": "Invalid column name: running"})


def test_merge_room_forward_fills_light_state():
    env = [("2026-10-12T08:19:54.266Z", 19.9, 92.47, 1639),
           ("2026-10-12T08:20:54.266Z", 20.0, 92.50, 1640)]
    light = [("2026-10-12T08:19:55.536Z", 2)]
    rows = merge_room("611", env, light)
    assert [r.ts for r in rows] == ["2026-10-12T16:19:54", "2026-10-12T16:20:54"]
    assert [r.lamp_state for r in rows] == [2.0, 2.0]
    assert rows[0].zone == "611"
    assert rows[0].co2_ppm == 1639.0


def test_merge_room_leaves_state_unknown_before_first_light_sample():
    rows = merge_room("611", [("2026-10-12T08:00:00.000Z", 19.0, 90.0, 1500)],
                      [("2026-10-12T08:30:00.000Z", 3)])
    assert rows[0].lamp_state is None  # 未知就是未知，不填 0


def test_fetch_since_queries_both_tables_with_backticks():
    calls = []

    def transport(url, sql):
        calls.append(sql)
        if "MDLIGHT01" in sql:
            return envelope([["2026-10-12T08:19:55.536Z", 2]], ["Running"])
        return envelope([["2026-10-12T08:19:54.266Z", 19.9, 92.47, 1639]],
                        ["Tatmosp", "Hatmosp", "Co2"])

    src = TdengineEnvSource("http://127.0.0.1:6041", rooms=["611"], transport=transport)
    rows = src.fetch_since("2026-10-12T16:00:00")

    assert len(calls) == 2
    # 表名/列名大小写敏感：反引号是语法的一部分，缺了会被折成小写而报错
    assert "`TD1_Q1MDTM01`" in calls[0] and "`Tatmosp`" in calls[0]
    assert "`Running`" in calls[1]
    assert "ts >= '2026-10-12T08:00:00.000Z'" in calls[0]   # 闭区间：重算当前小时不漏整点
    assert rows[0].temp_c == 19.9 and rows[0].lamp_state == 2.0


def test_unknown_room_is_ignored():
    src = TdengineEnvSource("http://x", rooms=["611", "999"],
                            transport=lambda url, sql: envelope([], []))
    assert src.rooms == ["611"]


def test_cold_start_uses_lookback():
    seen = []

    def transport(url, sql):
        seen.append(sql)
        return envelope([], [])

    src = TdengineEnvSource("http://x", rooms=["611"], transport=transport, lookback_minutes=60)
    src.fetch_since(None)
    assert "where ts >= '" in seen[0]
