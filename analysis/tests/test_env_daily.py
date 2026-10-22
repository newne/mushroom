"""票 24：按生长天数的日分布（五数落库 + 三态）。

测的是**对外行为**：五数之间必须成立的关系、`n` 与样本数一致、三态各自可读。
不断言"某个具体数值来自哪一行"，也不 mock 数据源内部。

为什么这三条状态必须分开（`analysis.env_daily.state_of`）：
`never_computed` 是**接线问题**（每日任务没跑过），`no_environment` 是**数据问题**
（这段窗口库里就没有小时行）。两者都返回空数组，但处置完全不同——合并成
「暂无数据」就等于让人去查错方向。
"""

from __future__ import annotations

from analysis.schema import (
    DAILY_STATE_NEVER_COMPUTED,
    DAILY_STATE_NO_ENVIRONMENT,
    DAILY_STATE_OK,
)
from analysis.store import fetch_environment_daily, upsert_batches, upsert_environment

from analysis import env_daily


def _hourly(conn, day: str = "2026-08-01", zone: str = "611", hours: int = 6,
            base: float = 20.0) -> None:
    """一天若干个小时行：温度线性变化，`temp_min/max` 是采样点级极值（比均值宽）。"""
    upsert_environment(conn, [
        {
            "ts": f"{day}T{i:02d}:00:00",
            "zone": zone,
            "temp_c": base + i,
            "temp_min": base + i - 0.5,
            "temp_max": base + i + 0.5,
            "rh_pct": 85.0,
            "rh_min": 80.0,
            "rh_max": 90.0,
            "co2_ppm": 800.0 + i,
            "co2_min": 700.0,
            "co2_max": 900.0,
            "lamp_on_min": 2.0,
            "lamp_on_s": 2.0,
            "lamp_off_m": 120.0,
            "n": 60,
        }
        for i in range(hours)
    ])


def test_five_numbers_relation_holds(pg):
    """`min ≤ q1 ≤ median ≤ q3 ≤ max`；`n` = 当天**有值**的小时点数（不是小时行数）。"""
    conn = pg
    _hourly(conn, hours=6)
    env_daily.refresh(conn, computed_at="2026-08-02T00:00:00")

    rows = fetch_environment_daily(conn, zone="611")
    assert len(rows) == 1
    row = rows[0]
    assert row["day"] == "2026-08-01"
    assert row["hours"] == 6
    assert row["samples"] == 360

    assert row["temp_c_n"] == 6
    assert row["temp_c_min"] <= row["temp_c_q1"] <= row["temp_c_median"]
    assert row["temp_c_median"] <= row["temp_c_q3"] <= row["temp_c_max"]
    # 五数是**小时口径**；极值走采样点级两列，两者不是一回事
    assert row["temp_c_sample_min"] < row["temp_c_min"]
    assert row["temp_c_sample_max"] > row["temp_c_max"]
    # 点亮分钟是时长，跨小时相加（不是求平均）
    assert row["lamp_on_min_total"] == 12.0


def test_small_sample_keeps_n_visible(pg):
    """n<3 时**照样落库**、把 n 记下来：不给页面留"凭空判断样本不足"的余地。"""
    conn = pg
    _hourly(conn, hours=2)
    env_daily.refresh(conn, computed_at="2026-08-02T00:00:00")
    row = fetch_environment_daily(conn, zone="611")[0]
    assert row["temp_c_n"] == 2
    assert row["hours"] == 2


def test_state_never_computed_when_hourly_exists(client, pg):
    """有小时行、但派生表空 ⇒ **接线问题**，不是"没有数据"。"""
    _hourly(pg, hours=3)
    body = client.get("/environment/daily?zone=611").json()
    assert body["state"] == DAILY_STATE_NEVER_COMPUTED
    assert body["rows"] == []


def test_state_no_environment_when_nothing_at_all(client):
    """连小时行都没有 ⇒ **数据问题**。"""
    body = client.get("/environment/daily?zone=611").json()
    assert body["state"] == DAILY_STATE_NO_ENVIRONMENT
    assert body["rows"] == []


def test_end_date_includes_that_whole_day(client, pg):
    """`end=2026-08-01` 必须**含 08-01 全天**。

    字符串前缀比较下 `'2026-08-01T08:00:00' > '2026-08-01'`，不补齐的话当天的
    小时行会被排除，"日分布没算过"就被误报成"这段窗口没有环境数据"——
    两种空态指向完全不同的排障方向。
    """
    _hourly(pg, day="2026-08-01", hours=3)
    body = client.get("/environment/daily?zone=611&start=2026-08-01&end=2026-08-01").json()
    assert body["state"] == DAILY_STATE_NEVER_COMPUTED


def test_state_ok_and_light_has_no_box(client, pg):
    """三参数各一组五数；光照只带制度末态，**不出五数**（ADR-0021 修订）。"""
    _hourly(pg, hours=6)
    env_daily.refresh(pg, computed_at="2026-08-02T00:00:00")

    body = client.get("/environment/daily?zone=611").json()
    assert body["state"] == DAILY_STATE_OK
    assert body["params"] == ["temp_c", "rh_pct", "co2_ppm"]
    row = body["rows"][0]
    assert set(row["params"]) == {"temp_c", "rh_pct", "co2_ppm"}
    assert set(row["lamp"]) == {
        "lamp_on_min_total", "lamp_on_s", "lamp_off_s", "lamp_on_m", "lamp_off_m",
    }
    assert "lamp_on_min" not in row["params"]


def test_growth_day_comes_from_ledger(pg):
    """生长天数只认台账（ADR-0020）：`entry_date` 之前的天**留 NULL**，不拿最近一批去套。"""
    conn = pg
    upsert_batches(conn, [{"room_id": "611", "entry_date": "2026-08-01", "batch_no": "mogu-1"}])
    _hourly(conn, day="2026-07-31", hours=2)      # 批次之前
    _hourly(conn, day="2026-08-01", hours=2)      # 入库当天 = 第 0 天
    _hourly(conn, day="2026-08-04", hours=2)
    env_daily.refresh(conn, computed_at="2026-08-05T00:00:00")

    rows = {r["day"]: r for r in fetch_environment_daily(conn, zone="611")}
    assert rows["2026-07-31"]["entry_date"] is None
    assert rows["2026-07-31"]["growth_day"] is None
    assert rows["2026-08-01"]["growth_day"] == 0
    assert rows["2026-08-04"]["growth_day"] == 3
    assert rows["2026-08-04"]["batch_no"] == "mogu-1"


def test_refresh_is_idempotent(pg):
    """重算两次不翻倍：`(zone, day)` 覆盖写。"""
    conn = pg
    _hourly(conn, hours=3)
    env_daily.refresh(conn, computed_at="2026-08-02T00:00:00")
    env_daily.refresh(conn, computed_at="2026-08-02T01:00:00")
    rows = fetch_environment_daily(conn, zone="611")
    assert len(rows) == 1
    assert rows[0]["computed_at"] == "2026-08-02T01:00:00"


def test_stale_flag_when_last_refresh_is_old(pg):
    """重算戳过久 ⇒ `stale` 标注（图能画，但末几天可能缺，要说出来）。"""
    conn = pg
    _hourly(conn, hours=24)
    env_daily.refresh(conn, computed_at="2026-08-02T00:00:00")
    rows = fetch_environment_daily(conn, zone="611")
    from datetime import datetime

    meta = env_daily.state_of(rows, has_environment=True, now=datetime(2026, 8, 10, 0, 0))
    assert "stale" in meta["flags"]
    assert meta["state"] == DAILY_STATE_OK


def test_cli_backfills_every_zone_and_pins_the_stamp(pg, monkeypatch, capsys):
    """`daily-refresh`（一次性补数）走的是**同一个** `refresh`，不是另写一条 SQL。

    补数场景要的是"把库里所有库房都补齐"，所以缺省不给 `--zone`；`--computed-at`
    能把重算戳钉住，便于与同一天的其它派生表对齐核对。
    """
    _hourly(pg, zone="611", hours=4)
    _hourly(pg, zone="612", hours=4)
    monkeypatch.setattr(env_daily.store, "connect", lambda **_: pg)

    env_daily.main(["--computed-at", "2026-08-02T03:10:00"])

    for zone in ("611", "612"):
        rows = fetch_environment_daily(pg, zone=zone)
        assert len(rows) == 1, zone
        assert rows[0]["computed_at"] == "2026-08-02T03:10:00"
    out = capsys.readouterr().out
    assert "2 个日分布行" in out
    assert pg.schema in out


def test_cli_zone_flag_does_not_invent_rows(pg, monkeypatch):
    """`--zone` 是"额外清空重算"，**不是**"只算这个库房"：给一个没数据的库房不该凭空出行。"""
    _hourly(pg, zone="611", hours=2)
    monkeypatch.setattr(env_daily.store, "connect", lambda **_: pg)

    env_daily.main(["--zone", "699", "--computed-at", "2026-08-02T03:10:00"])

    assert len(fetch_environment_daily(pg, zone="611")) == 1
    assert fetch_environment_daily(pg, zone="699") == []
