"""`analysis.growth`：与上一时间点的生长对比。

重点是**保守**：间隔太短、样本太少、时间戳坏了、菇变短了——每一种"不确定"都要落到
一个说得出理由的判词上，而不是硬算一个速率然后让运维自己怀疑。
"""

from __future__ import annotations

import pytest
from analysis.growth import (
    GrowthPoint,
    compare,
    latest_delta,
    room_summary,
    series,
)


def point(ts, mean_len, *, box="B101", n=6, cap=None, quality=None):
    return GrowthPoint(ts=ts, box_id=box, n=n, mean_len_mm=mean_len, mean_cap_mm=cap,
                       quality=quality)


def row(ts, mean_len, *, box="B101", n=6, cap=None, quality=None):
    return {"ts": ts, "box_id": box, "n": n, "mean_len_mm": mean_len,
            "mean_cap_mm": cap, "quality": quality}


# ---------- 基本判定 ----------


def test_growing():
    d = compare(point("2026-09-13T08:00:00", 40.0), point("2026-09-13T20:00:00", 46.0))
    assert d.verdict == "growing"
    assert d.dt_h == pytest.approx(12.0)
    assert d.d_len_mm == pytest.approx(6.0)
    assert d.rate_len_mm_per_h == pytest.approx(0.5)
    assert d.verdict_text == "在长"


def test_stalled():
    d = compare(point("2026-09-13T08:00:00", 40.0), point("2026-09-13T20:00:00", 40.5))
    assert d.verdict == "stalled"
    assert "±0.1" in d.note


def test_shrinking_is_its_own_verdict_not_stalled():
    """菇不会变短。负增长意味着口径变了（换角度/脏镜头/被遮），必须能单独看见。"""
    d = compare(point("2026-09-13T08:00:00", 46.0), point("2026-09-13T20:00:00", 40.0))
    assert d.verdict == "shrinking"
    assert "看图核对" in d.verdict_text


def test_cap_delta_is_reported_when_both_have_it():
    d = compare(point("2026-09-13T08:00:00", 40.0, cap=30.0),
                point("2026-09-13T20:00:00", 46.0, cap=34.0))
    assert d.d_cap_mm == pytest.approx(4.0)
    assert d.rate_cap_mm_per_h == pytest.approx(4.0 / 12.0)


def test_cap_missing_on_one_side_does_not_block_the_length_verdict():
    d = compare(point("2026-09-13T08:00:00", 40.0), point("2026-09-13T20:00:00", 46.0))
    assert d.d_cap_mm is None and d.rate_cap_mm_per_h is None
    assert d.verdict == "growing"


# ---------- 不下结论的情况 ----------


def test_short_interval_refuses_to_call_it_stalled():
    """隔 10 分钟时长得再慢也说明不了问题——报"停滞"只会制造假警报。"""
    d = compare(point("2026-09-13T08:00:00", 40.0), point("2026-09-13T08:10:00", 40.01))
    assert d.verdict == "interval_too_short"
    assert "10 分钟" in d.note
    assert d.rate_len_mm_per_h is None, "不下结论就不要给出速率"


def test_too_few_detections_is_not_comparable():
    d = compare(point("2026-09-13T08:00:00", 40.0, n=1), point("2026-09-13T20:00:00", 46.0))
    assert d.verdict == "not_comparable"
    assert "检出朵数不足" in d.note


def test_missing_length_is_not_comparable():
    d = compare(point("2026-09-13T08:00:00", None), point("2026-09-13T20:00:00", 46.0))
    assert d.verdict == "not_comparable"
    assert "没测到长度" in d.note


def test_unparsable_timestamp_says_so():
    d = compare(point("not-a-date", 40.0), point("2026-09-13T20:00:00", 46.0))
    assert d.verdict == "not_comparable"
    assert "解析不了" in d.note


def test_time_going_backwards_is_not_comparable():
    d = compare(point("2026-09-13T20:00:00", 40.0), point("2026-09-13T08:00:00", 46.0))
    assert d.verdict == "not_comparable"
    assert "时间没有前进" in d.note


def test_quality_flags_are_surfaced_in_the_note():
    d = compare(point("2026-09-13T08:00:00", 40.0, quality="blurry"),
                point("2026-09-13T20:00:00", 46.0))
    assert "blurry" in d.note


# ---------- 序列与整库 ----------


def test_series_starts_with_no_prev_and_is_time_ordered():
    rows = [row("2026-09-13T20:00:00", 46.0),
            row("2026-09-13T08:00:00", 40.0),
            row("2026-09-13T14:00:00", 43.0)]
    got = series(rows, "B101")
    assert [d.ts for d in got] == ["2026-09-13T08:00:00", "2026-09-13T14:00:00",
                                   "2026-09-13T20:00:00"]
    assert got[0].verdict == "no_prev"
    assert [d.verdict for d in got[1:]] == ["growing", "growing"]


def test_series_filters_by_box():
    rows = [row("2026-09-13T08:00:00", 40.0), row("2026-09-13T20:00:00", 46.0, box="B102")]
    got = series(rows, "B101")
    assert len(got) == 1 and got[0].verdict == "no_prev"


def test_series_limit_takes_the_tail():
    rows = [row("2026-09-13T08:00:00", 40.0), row("2026-09-13T14:00:00", 43.0),
            row("2026-09-13T20:00:00", 46.0)]
    got = series(rows, "B101", limit=2)
    assert [d.ts for d in got] == ["2026-09-13T14:00:00", "2026-09-13T20:00:00"]


def test_latest_delta_is_the_last_point():
    rows = [row("2026-09-13T08:00:00", 40.0), row("2026-09-13T20:00:00", 40.2)]
    d = latest_delta(rows, "B101")
    assert d is not None and d.verdict == "stalled"
    assert latest_delta(rows, "B999") is None


def test_room_summary_lists_every_box_asked_for():
    """要了几个框就得回几个框——少一个的话现场分不清"没长"还是"没拍"。"""
    rows = [row("2026-09-13T08:00:00", 40.0), row("2026-09-13T20:00:00", 46.0)]
    got = room_summary(rows, boxes=["B101", "B102", "B103"])
    assert got["n_boxes"] == 3
    assert set(got["boxes"]) == {"B101", "B102", "B103"}
    assert got["boxes"]["B101"]["verdict"] == "growing"
    assert got["boxes"]["B102"]["verdict"] == "no_prev"
    assert "该框还没有任何测量记录" in got["boxes"]["B103"]["note"]
    assert got["counts"] == {"growing": 1, "no_prev": 2}


def test_room_summary_counts_only_nonzero_verdicts():
    rows = [row("2026-09-13T08:00:00", 40.0), row("2026-09-13T20:00:00", 40.1)]
    got = room_summary(rows, boxes=["B101"])
    assert got["counts"] == {"stalled": 1}
    assert "growing" not in got["counts"]


def test_row_is_serialisable_for_the_api():
    d = latest_delta([row("2026-09-13T08:00:00", 40.0), row("2026-09-13T20:00:00", 46.0)], "B101")
    r = d.to_row()
    assert r["verdict"] == "growing"
    assert r["verdict_text"] == "在长"
    assert r["dt_h"] == 12.0
    assert r["rate_len_mm_per_h"] == 0.5
