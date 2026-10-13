"""票 13 的排除规则：复制帧 / 可疑轮 / 没查成的轮，一律不许进聚合统计。

真事件：2026-10-09 18:20–18:27 那一轮 48 帧里 35 帧字节完全相同（陈旧近黑帧被复制到
S211→S401），轮行却写着 `ok=1 / 48 帧 / 0 失败`。这种数据进了长势曲线比缺数据更坏 ——
它看起来"正常"。
"""

from __future__ import annotations

import pytest
from analysis.integrity import (
    REASON_CHECK_ERROR,
    REASON_SUSPECT_ROUND,
    SUSPECT_MAX_SPAN_H,
    dup_boxes,
    exclusion_reason,
    partition,
    suspect_intervals,
)


def rnd(ts, *, room="611", suspect=None, check_error=None) -> dict:
    return {"ts": ts, "room_id": room, "suspect_duplicate": suspect,
            "content_check_error": check_error}


def measure(ts, *, room="611", box="B105") -> dict:
    return {"ts": ts, "room_id": room, "box_id": box}


# ---------- 窗口 ----------


def test_suspect_round_window_ends_at_the_next_round():
    rounds = [rnd("2026-10-09T18:20:00", suspect=1),
              rnd("2026-10-09T19:10:00"),
              rnd("2026-10-09T20:00:00")]
    intervals = suspect_intervals(rounds)
    assert len(intervals["611"]) == 1
    got = intervals["611"][0]
    assert got.reason == REASON_SUSPECT_ROUND
    assert (got.start, got.end) == ("2026-10-09T18:20:00", "2026-10-09T19:10:00")


def test_check_error_round_is_excluded_too():
    """**没查成 ≠ 已确认无重复**：同样排除，但要能说清原因。"""
    intervals = suspect_intervals([rnd("2026-10-09T18:20:00", check_error="服务超时")])
    assert intervals["611"][0].reason == REASON_CHECK_ERROR


def test_checked_and_clean_rounds_exclude_nothing():
    assert suspect_intervals([rnd("2026-10-09T18:20:00"), rnd("2026-10-09T19:00:00")]) == {}


def test_last_suspect_round_does_not_exclude_forever():
    """可疑轮没有后继时，窗口必须封顶 —— 否则该库房之后的数据全被永久排除。"""
    intervals = suspect_intervals([rnd("2026-10-09T18:20:00", suspect=1)])
    item = intervals["611"][0]
    assert item.end == "2026-10-09T19:20:00"
    assert SUSPECT_MAX_SPAN_H == 1.0


def test_rounds_without_the_new_columns_stay_clean():
    """老数据没有这些列 ⇒ 一律照常参与统计（不因"缺标记"而误排）。"""
    assert suspect_intervals([{"ts": "2026-10-09T18:20:00", "room_id": "611"}]) == {}


# ---------- 排除 ----------


def test_measurements_inside_the_window_are_dropped_with_reasons():
    rounds = [rnd("2026-10-09T18:20:00", suspect=1), rnd("2026-10-09T19:10:00")]
    kept, dropped = partition(
        [measure("2026-10-09T18:25:00"), measure("2026-10-09T19:30:00"),
         measure("2026-10-09T18:25:00", room="612")],
        rounds,
    )
    assert [row["ts"] for row in kept] == ["2026-10-09T19:30:00", "2026-10-09T18:25:00"]
    assert len(dropped) == 1, "只丢可疑轮里的那一行；别的库房不受影响"
    assert dropped[0].reason == REASON_SUSPECT_ROUND
    assert dropped[0].zone == "611"
    assert dropped[0].window.startswith("2026-10-09T18:20:00")


def test_exclusion_reason_is_none_outside_windows():
    intervals = suspect_intervals([rnd("2026-10-09T18:20:00", suspect=1),
                                   rnd("2026-10-09T19:10:00")])
    assert exclusion_reason(intervals, "611", "2026-10-09T18:00:00") is None
    assert exclusion_reason(intervals, "611", "2026-10-09T19:10:00") is None
    assert exclusion_reason(intervals, "612", "2026-10-09T18:30:00") is None


def test_frame_dup_boxes_helper():
    images = [{"room_id": "611", "box_id": "B105", "frame_dup": 1},
              {"room_id": "611", "box_id": "B106", "frame_dup": 0},
              {"room_id": "612", "box_id": "B205", "frame_dup": 1}]
    assert dup_boxes(images, zone="611") == {"B105"}
    assert dup_boxes(images, zone="612") == {"B205"}
