"""TDengine 环境源的解析与对齐（票 01）。"""

import pytest

from analysis.env.tdengine import (
    TdengineEnvSource,
    light_groups,
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


def test_light_groups_split_blue_and_white():
    """四组灯各自选白/蓝（`Choose1..4`），`OnOff*=0` 的组不参与补光。"""
    blue, white = light_groups(
        onoff=[1, 1, 0, 1],
        choose=[0, 1, 1, 0],     # 1 白 · 2 蓝 · 3 蓝但关闭 · 4 白
    )
    assert blue == "2"
    assert white == "1,4"


def test_light_groups_unknown_is_not_zero():
    """整行没有光源列 ⇒ (None, None)：**没配**不等于"一组都没亮"。"""
    assert light_groups([], []) == (None, None)
    # 有列但四组全是关闭 ⇒ 空清单（现场确实没补光），与上面那条要分得开
    assert light_groups([0, 0, 0, 0], [0, 1, 0, 1]) == ("", "")


def test_merge_room_carries_light_groups():
    light = [("2026-10-12T08:19:55.536Z", 2, 60, 120, 1, 1, 0, 1, 0, 1, 1, 0)]
    rows = merge_room("611", [("2026-10-12T08:19:54.266Z", 19.9, 92.0, 1600)], light)
    assert rows[0].lamp_on_m == 60.0 and rows[0].lamp_off_m == 120.0
    assert rows[0].lamp_blue_groups == "2"      # 3# 那组是关闭的，不参与补光
    assert rows[0].lamp_white_groups == "1,4"


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
