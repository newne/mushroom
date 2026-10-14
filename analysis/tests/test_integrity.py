"""票 13 的排除规则：复制帧 / 可疑轮 / 没查成的轮，一律不许进聚合统计。

真事件：2026-10-09 18:20–18:27 那一轮 48 帧里 35 帧字节完全相同（陈旧近黑帧被复制到
S211→S401），轮行却写着 `ok=1 / 48 帧 / 0 失败`。这种数据进了长势曲线比缺数据更坏 ——
它看起来"正常"。
"""

from __future__ import annotations

import pytest
from analysis.integrity import (
    LEGACY_EXCLUSIONS,
    REASON_CHECK_ERROR,
    REASON_LEGACY_EXCLUDED,
    REASON_SUSPECT_ROUND,
    SUSPECT_MAX_SPAN_H,
    LegacyExclusion,
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
    intervals = suspect_intervals(rounds, exclusions=())
    assert len(intervals["611"]) == 1
    got = intervals["611"][0]
    assert got.reason == REASON_SUSPECT_ROUND
    assert (got.start, got.end) == ("2026-10-09T18:20:00", "2026-10-09T19:10:00")


def test_check_error_round_is_excluded_too():
    """**没查成 ≠ 已确认无重复**：同样排除，但要能说清原因。"""
    intervals = suspect_intervals([rnd("2026-10-09T18:20:00", check_error="服务超时")],
                                  exclusions=())
    assert intervals["611"][0].reason == REASON_CHECK_ERROR


def test_checked_and_clean_rounds_exclude_nothing():
    assert suspect_intervals([rnd("2026-10-09T18:20:00"), rnd("2026-10-09T19:00:00")],
                             exclusions=()) == {}


def test_last_suspect_round_does_not_exclude_forever():
    """可疑轮没有后继时，窗口必须封顶 —— 否则该库房之后的数据全被永久排除。"""
    intervals = suspect_intervals([rnd("2026-10-09T18:20:00", suspect=1)], exclusions=())
    item = intervals["611"][0]
    assert item.end == "2026-10-09T19:20:00"
    assert SUSPECT_MAX_SPAN_H == 1.0


def test_rounds_without_the_new_columns_stay_clean():
    """老数据没有这些列 ⇒ 标记口径下不误排（显式清单另算，见清单那组用例）。"""
    assert suspect_intervals([{"ts": "2026-10-09T18:20:00", "room_id": "611"}],
                             exclusions=()) == {}


# ---------- 排除 ----------


def test_measurements_inside_the_window_are_dropped_with_reasons():
    rounds = [rnd("2026-10-09T18:20:00", suspect=1), rnd("2026-10-09T19:10:00")]
    kept, dropped = partition(
        [measure("2026-10-09T18:25:00"), measure("2026-10-09T19:30:00"),
         measure("2026-10-09T18:25:00", room="612")],
        rounds,
        exclusions=(),
    )
    assert [row["ts"] for row in kept] == ["2026-10-09T19:30:00", "2026-10-09T18:25:00"]
    assert len(dropped) == 1, "只丢可疑轮里的那一行；别的库房不受影响"
    assert dropped[0].reason == REASON_SUSPECT_ROUND
    assert dropped[0].zone == "611"
    assert dropped[0].window.startswith("2026-10-09T18:20:00")


def test_exclusion_reason_is_none_outside_windows():
    intervals = suspect_intervals([rnd("2026-10-09T18:20:00", suspect=1),
                                   rnd("2026-10-09T19:10:00")], exclusions=())
    assert exclusion_reason(intervals, "611", "2026-10-09T18:00:00") is None
    assert exclusion_reason(intervals, "611", "2026-10-09T19:10:00") is None
    assert exclusion_reason(intervals, "612", "2026-10-09T18:30:00") is None


def test_frame_dup_boxes_helper():
    images = [{"room_id": "611", "box_id": "B105", "frame_dup": 1},
              {"room_id": "611", "box_id": "B106", "frame_dup": 0},
              {"room_id": "612", "box_id": "B205", "frame_dup": 1}]
    assert dup_boxes(images, zone="611") == {"B105"}
    assert dup_boxes(images, zone="612") == {"B205"}


# ---------- 显式排除清单（legacy 轮：没有标记列，只能点名排） ----------


def test_legacy_list_has_the_1009_round_with_evidence():
    """第一条就是 10-09 那轮，键、窗口、批准人、"为什么靠标记排不掉"都要写全。"""
    assert LEGACY_EXCLUSIONS, "清单不能空 —— 这就是为了 legacy 轮存在的"
    first = LEGACY_EXCLUSIONS[0]
    assert first.round_id == "12038e7ac0334a8bbc1e178793ed5c09"
    assert first.room_id == "611"
    assert (first.start, first.end) == ("2026-10-09T18:20:02", "2026-10-09T18:28:00")
    assert "35 帧" in first.reason and "legacy" in first.reason
    assert "ok=1" in first.reason, "要记下'轮行看起来完全正常'这个最坑人的点"
    assert first.approved_by == "Chief 2026-10-14"
    assert first.verified is True, "已核准 ⇒ 生效"


def test_1009_window_contains_the_round_row_ts():
    """半开区间 `[start, end)` 的右端必须含轮行 ts（2026-10-09T18:27:34）。

    这是真缺口（发版官 2026-10-14 指出）：右端若取 18:27:34，**轮行自己被判在窗口外**，
    凡按轮行/帧行 ts 匹配的路径就排不掉这一轮 —— `verified=True` 也白搭。
    同时右端要与测量侧 `measure/src/measure/sample_hygiene.py` 的 18:28:00 一致。
    """
    first = LEGACY_EXCLUSIONS[0]
    assert first.start <= "2026-10-09T18:27:34" < first.end


def test_the_1009_round_is_actually_excluded_now_with_reason():
    """生效后：该轮窗口内的测量行被排除，且能说出'哪条规则命中、谁批的'。"""
    kept, dropped = partition(
        [measure("2026-10-09T18:22:00"), measure("2026-10-09T18:22:00", room="612"),
         measure("2026-10-09T18:27:34"), measure("2026-10-09T18:28:00"),
         measure("2026-10-09T21:20:00")],
        [{"ts": "2026-10-09T18:27:34", "room_id": "611",
          "round_id": "12038e7ac0334a8bbc1e178793ed5c09"}],
    )
    # 18:28:00（右端，开区间）起算恢复：半开区间的边界要能说清
    assert [r["ts"] for r in kept] == [
        "2026-10-09T18:22:00", "2026-10-09T18:28:00", "2026-10-09T21:20:00"
    ]
    dropped_zones = {d.zone for d in dropped}
    assert dropped_zones == {"611"}
    assert {d.ts for d in dropped} == {
        "2026-10-09T18:22:00", "2026-10-09T18:27:34"
    }
    assert all(d.reason == REASON_LEGACY_EXCLUDED for d in dropped)
    detail = dropped[0].detail or ""
    assert "round_id 命中" in detail and "Chief 2026-10-14" in detail
    assert dropped[0].window == "2026-10-09T18:20:02~2026-10-09T18:28:00"


def test_window_key_covers_legacy_rows_without_round_id():
    """帧行那一代可能没有 round_id ⇒ 即便 rounds 里查不到该轮，时间窗仍要排掉。"""
    kept, _ = partition([measure("2026-10-09T18:25:00")], [], exclusions=LEGACY_EXCLUSIONS)
    assert kept == [], "没有轮行也要能靠时间窗排掉（两个键的意义就在这里）"


def test_unverified_entries_do_not_exclude_anything():
    """没核准的窗口不许悄悄排数据。"""
    entry = LegacyExclusion(reason="还没批", room_id="611",
                            start="2026-10-09T18:20:05", end="2026-10-09T18:27:34")
    rows = [measure("2026-10-09T18:22:00")]
    kept, dropped = partition(rows, [], exclusions=(entry,))
    assert len(kept) == 1 and dropped == []


def test_verified_window_entry_excludes_and_says_why():
    entry = LegacyExclusion(reason="10-09 那轮：35 帧同字节，legacy 无标记列",
                            room_id="611", start="2026-10-09T18:20:05",
                            end="2026-10-09T18:27:16", verified=True)
    kept, dropped = partition(
        [measure("2026-10-09T18:22:00"), measure("2026-10-09T21:20:00"),
         measure("2026-10-09T18:22:00", room="612")],
        [],
        exclusions=(entry,),
    )
    assert [r["ts"] for r in kept] == ["2026-10-09T21:20:00", "2026-10-09T18:22:00"]
    assert len(dropped) == 1
    assert dropped[0].reason == REASON_LEGACY_EXCLUDED
    assert "35 帧同字节" in (dropped[0].detail or ""), "排除必须能说出为什么"
    assert dropped[0].window == "2026-10-09T18:20:05~2026-10-09T18:27:16"


def test_verified_round_id_entry_uses_the_round_window():
    """有 round_id 的轮：按该轮窗口排（与标记口径一致）。"""
    entry = LegacyExclusion(reason="手工点名排掉", round_id="r-legacy", verified=True)
    rounds = [{"ts": "2026-10-09T18:20:05", "room_id": "611", "round_id": "r-legacy"},
              {"ts": "2026-10-09T21:20:00", "room_id": "611", "round_id": "r-later"}]
    kept, dropped = partition([measure("2026-10-09T18:22:00"),
                               measure("2026-10-09T21:25:00")], rounds,
                              exclusions=(entry,))
    assert [r["ts"] for r in kept] == ["2026-10-09T21:25:00"]
    assert dropped[0].reason == REASON_LEGACY_EXCLUDED


def test_entry_without_key_or_room_is_skipped_not_applied_globally():
    """既没有 round_id 也没有 room_id ⇒ 无法归属 ⇒ **不排**（不许全局生效）。"""
    entry = LegacyExclusion(reason="来历不明", start="2026-10-09T00:00:00",
                            end="2026-10-10T00:00:00", verified=True)
    intervals = suspect_intervals([rnd("2026-10-09T18:20:05", room="611")],
                                  exclusions=(entry,))
    assert intervals == {}
