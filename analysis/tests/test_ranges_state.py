"""票 05：区间表重算的**状态机** —— 空表必须说得出为什么空。

为什么要这些测试：`ranges` 在生产里长期是 0 行，而"0 行"至少对应六件不同的事
（没接线 / 没有测量 / 全被排除 / 没有环境 / 平视下没有可用长势量 / 样本不足）。
以前 `/ranges` 只返回 `[]`，页面只能说"暂无数据"，把"接线问题"和"数据问题"混成一个
沉默的空 —— 这跟"未知字段被静默忽略"是同一类错误。
"""

from __future__ import annotations

from analysis.align import (
    STATE_ALL_EXCLUDED,
    STATE_INSUFFICIENT_SAMPLES,
    STATE_NO_ENVIRONMENT,
    STATE_NO_GROWTH_METRIC,
    STATE_NO_MEASUREMENTS,
    STATE_OK,
    compute_ranges,
    refresh_ranges,
)
from analysis.schema import RANGES_STATE_NEVER_COMPUTED
from analysis.store import (
    fetch_ranges_state,
    insert_measurements,
    insert_rounds,
    upsert_environment,
)


def _clear_measurements(conn) -> None:
    """清空测量表（**生产从不删**测量行，所以门面没有这个函数 —— 只有这条用例需要）。"""
    with conn.raw.cursor() as cur:
        cur.execute(f'DELETE FROM "{conn.schema}".measurements')


def _env(conn, hours: int, zone: str = "611") -> None:
    upsert_environment(conn, [
        {"ts": f"2026-08-01T{i:02d}:00:00", "zone": zone,
         "temp_c": 22.0 + (i % 2) * 4.0, "rh_pct": 85.0, "co2_ppm": 800.0,
         "lamp_state": 2.0}
        for i in range(hours)
    ])


def test_never_computed_is_not_the_same_as_empty(pg):
    """没算过 ⇒ `never_computed`：这是**接线**问题，不是数据问题。"""
    conn = pg
    state = fetch_ranges_state(conn)
    assert state["state"] == RANGES_STATE_NEVER_COMPUTED
    assert state["computed_at"] is None
    assert state["detail"] == {}


def test_state_no_measurements(pg):
    conn = pg
    _env(conn, 24)

    run = compute_ranges(pg)
    assert run.state == STATE_NO_MEASUREMENTS
    assert run.rows == []
    assert run.detail["n_measurements"] == 0
    assert run.detail["n_environment"] == 24


def test_state_no_environment(pg):
    conn = pg
    insert_measurements(conn, [{"ts": "2026-08-01T00:00:00", "room_id": "611",
                                "box_id": "B01", "mean_len_mm": 10.0, "quality": "verified"}])

    run = compute_ranges(pg)
    assert run.state == STATE_NO_ENVIRONMENT
    assert run.detail["n_environment"] == 0
    assert run.detail["n_kept"] == 1


def test_state_no_growth_metric_is_its_own_state(pg):
    """平视单档下杆长恒 NULL ⇒ 要说"没有可用长势量"，不能笼统说"样本不足"。"""
    conn = pg
    _env(conn, 24)
    insert_measurements(conn, [
        {"ts": f"2026-08-01T{i:02d}:00:00", "room_id": "611", "box_id": "B01",
         "quality": "verified", "n": 3}          # 只有检出数，没有任何长势量
        for i in range(6)
    ])

    run = compute_ranges(pg)
    assert run.state == STATE_NO_GROWTH_METRIC
    assert run.rows == []
    assert "长势量" in run.state_text
    assert "mean_len_mm" in run.detail["error"], "要说清缺的是哪些量"


def test_state_all_excluded_counts_by_reason(pg):
    """整段落在可疑轮窗口里 ⇒ `all_excluded`，且原因要数得出来（不静默丢）。"""
    conn = pg
    _env(conn, 4)
    insert_rounds(conn, [
        {"ts": "2026-08-01T00:00:00", "round_id": "r1", "room_id": "611",
         "suspect_duplicate": True, "ok": 1},
        {"ts": "2026-08-01T02:00:00", "round_id": "r2", "room_id": "611", "ok": 1},
    ])
    insert_measurements(conn, [
        {"ts": "2026-08-01T00:30:00", "room_id": "611", "box_id": "B01",
         "mean_len_mm": 10.0, "quality": "verified"},
        {"ts": "2026-08-01T01:00:00", "room_id": "611", "box_id": "B01",
         "mean_len_mm": 11.0, "quality": "verified"},
    ])

    run = compute_ranges(pg)
    assert run.state == STATE_ALL_EXCLUDED
    assert run.detail["n_kept"] == 0
    assert run.detail["n_excluded"] == 2
    assert run.detail["excluded_by_reason"] == {"suspect_duplicate_round": 2}


def test_legacy_exclusion_is_visible_with_reason(pg):
    """10-09 那条显式排除清单命中时：**排掉几条、凭什么、谁批的**都要看得到。"""
    conn = pg
    # 环境覆盖那两天，保证"没有环境"不是这里的空因
    upsert_environment(conn, [
        {"ts": f"2026-10-0{d}T{h:02d}:00:00", "zone": "611", "temp_c": 24.0,
         "rh_pct": 85.0, "co2_ppm": 800.0}
        for d in (8, 9) for h in range(24)
    ])
    insert_measurements(conn, [
        {"ts": "2026-10-09T18:25:00", "room_id": "611", "box_id": "B01",
         "mean_area_mm2": 900.0, "quality": "verified"},   # 窗口内 ⇒ 排除
        {"ts": "2026-10-09T21:20:00", "room_id": "611", "box_id": "B01",
         "mean_area_mm2": 950.0, "quality": "verified"},   # 窗口外 ⇒ 保留
    ])

    run = compute_ranges(pg)
    assert run.detail["excluded_by_reason"] == {"legacy_excluded": 1}
    sample = run.detail["excluded_samples"][0]
    assert sample["ts"] == "2026-10-09T18:25:00"
    assert "Chief 2026-10-14" in sample["detail"], "谁批的要留在证据里"
    # 窗口从清单读，不在这里再抄一份：同一个坏轮的窗口只许有一个出处
    from analysis.integrity import LEGACY_EXCLUSIONS

    first = LEGACY_EXCLUSIONS[0]
    assert sample["window"] == f"{first.start}~{first.end}"
    assert first.start <= "2026-10-09T18:27:34" < first.end, "右端要含轮行 ts"
    assert run.state != STATE_ALL_EXCLUDED, "只排掉一条，剩下的要继续算"


def test_state_insufficient_samples(pg):
    """有测量也有环境，但相邻整点只凑出 2 段速率（每箱 <3）⇒ 明确说"样本不足"。"""
    conn = pg
    _env(conn, 6)
    insert_measurements(conn, [
        {"ts": f"2026-08-01T{i:02d}:15:00", "room_id": "611", "box_id": "B01",
         "mean_area_mm2": 900.0 + i * 10, "quality": "verified"}
        for i in range(3)
    ])

    run = compute_ranges(pg)
    assert run.state == STATE_INSUFFICIENT_SAMPLES
    assert run.rows == []
    assert run.detail["min_samples_per_bin"] == 3
    assert run.detail["n_rate_rows"] == 2, "只在相邻整点间差分：3 个整点 ⇒ 2 段"
    assert "样本不足" in run.state_text


def test_state_ok_and_refresh_persists_it(pg):
    conn = pg
    _env(conn, 24)
    insert_measurements(conn, [
        {"ts": f"2026-08-01T{i:02d}:00:00", "room_id": "611", "box_id": "B01",
         "mean_len_mm": 10.0 + i, "quality": "verified"}
        for i in range(24)
    ])

    n = refresh_ranges(pg, computed_at="2026-08-02T00:00:00")
    assert n >= 1

    state = fetch_ranges_state(pg)
    assert state["state"] == STATE_OK
    assert state["n_rows"] == n
    assert state["computed_at"] == "2026-08-02T00:00:00"
    assert state["metric"] == "mean_len_mm" and state["unit"] == "mm"
    assert state["detail"]["n_rows"] == n
    # 人话里带行数，页面不用自己拼
    assert str(n) in state["state_text"]


def test_state_flips_when_the_data_disappears(pg):
    """状态是"最近一次重算"的快照，不是历史累积：数据没了就要如实翻到空态。"""
    conn = pg
    refresh_ranges(pg, computed_at="2026-08-02T00:00:00")
    assert fetch_ranges_state(conn)["state"] == STATE_NO_MEASUREMENTS

    _env(conn, 24)
    insert_measurements(conn, [
        {"ts": f"2026-08-01T{i:02d}:00:00", "room_id": "611", "box_id": "B01",
         "mean_len_mm": 10.0 + i, "quality": "verified"}
        for i in range(24)
    ])
    assert refresh_ranges(pg, computed_at="2026-08-03T00:00:00") >= 1
    assert fetch_ranges_state(conn)["state"] == STATE_OK

    _clear_measurements(conn)
    assert refresh_ranges(pg, computed_at="2026-08-04T00:00:00") == 0
    assert fetch_ranges_state(conn)["state"] == STATE_NO_MEASUREMENTS
