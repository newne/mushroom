"""票 20：估算证据列进测量行 —— 落列、形状、两条 flag 语义。

这一票存在的理由是**字段级**取舍：只用行级 `estimate`，下游只能整行丢或整行收，
两种都错（票 20 §2）。所以这里钉三件事：

1. 五个列真的落进去了（新库建表 + 老库 `ALTER TABLE` 都能补），且 `/measurements` 读得出来；
2. `estimated_fields` **读回来是 list**（写进去给 list 或 `+` 串都行）；
3. `no_theta` / `height_uncorrected` **不让行变不可用**，但也**不进 `verified` 统计** ——
   两层判断各司其职；`height_uncorrected` 的竖直量**不是长度**，不许被当长度用。
"""

from __future__ import annotations

import sqlite3

import pytest
from analysis.api import create_app
from analysis.db import ADDED_COLUMNS, MEASUREMENT_FIELDS, connect, migrate
from analysis.evidence import (
    SCALE_SOURCES,
    field_is_usable,
    format_estimated_fields,
    is_usable,
    mm_per_px_from_ref,
    normalize_scale_source,
    parse_estimated_fields,
)
from fastapi.testclient import TestClient

#: 票 20 新增的列（追加在 `quality` 之后；测量侧同步时顺序也要照这个来）。
NEW_FIELDS = ("estimated_fields", "scale_source", "mm_per_px", "scale_ref_mm", "scale_ref_px")


def measurement(**kw) -> dict:
    row = {
        "kind": "measurement",
        "ts": "2026-10-14T10:00:00",
        "room_id": "611",
        "box_id": "B105",
        "station_id": "S105",
        "angle_profile": "level0",
        "n": 6,
        "quality": "estimate+no_angle_profile",
    }
    row.update(kw)
    return row


def client(tmp_path) -> TestClient:
    return TestClient(create_app(str(tmp_path / "e.db")))


def app_and_db(tmp_path, name="e.db"):
    """返回 `(app, db_path)`：有些断言要直接跑库层（如 `compute_ranges`）。"""
    db = str(tmp_path / name)
    return create_app(db), db


# ---------- 列真的落了 ----------


def test_new_columns_are_in_the_schema_and_in_added_columns(tmp_path):
    add = ADDED_COLUMNS["measurements"]
    assert set(NEW_FIELDS) <= set(add), "老库靠 ADDED_COLUMNS 就地补列，漏一个就是静默丢字段"
    assert MEASUREMENT_FIELDS[-len(NEW_FIELDS):] == NEW_FIELDS, "新列追加在末尾，顺序两侧一致"

    conn = connect(str(tmp_path / "new.db"))
    cols = {r[1] for r in conn.execute("PRAGMA table_info(measurements)")}
    assert set(NEW_FIELDS) <= cols


def test_migration_adds_the_evidence_columns_to_an_old_table(tmp_path):
    """老库（没有这五列）走 `ALTER TABLE ADD COLUMN`，老行留 NULL —— 不编证据。"""
    path = str(tmp_path / "old.db")
    raw = sqlite3.connect(path)
    raw.executescript("""
        CREATE TABLE measurements (
          ts TEXT NOT NULL, room_id TEXT NOT NULL, box_id TEXT NOT NULL,
          quality TEXT, PRIMARY KEY (ts, room_id, box_id)
        );
        INSERT INTO measurements (ts, room_id, box_id, quality)
        VALUES ('2026-10-01T10:00:00', '611', 'B105', 'verified');
    """)
    raw.commit()

    added = migrate(raw)
    assert set(added) >= {f"measurements.{f}" for f in NEW_FIELDS}
    row = raw.execute(
        "SELECT estimated_fields, scale_source, mm_per_px FROM measurements"
    ).fetchone()
    assert row == (None, None, None), "老行是 NULL（那时没有证据），不是 0、不是空串"
    assert migrate(raw) == [], "迁移幂等"


# ---------- 形状：estimated_fields 读回来是 list ----------


def test_estimated_fields_round_trips_as_a_list(tmp_path):
    with client(tmp_path) as c:
        c.post("/ingest", json={"rows": [measurement(
            estimated_fields=["mean_cluster_w_mm", "mean_cluster_h_mm"],
            scale_source="bag_standard", mm_per_px=0.6012,
            scale_ref_mm=240.0, scale_ref_px=399.2,
            mean_cluster_w_mm=61.5, mean_cluster_h_mm=None,
        )]})
        got = c.get("/measurements").json()[0]
    assert got["estimated_fields"] == ["mean_cluster_w_mm", "mean_cluster_h_mm"]
    assert isinstance(got["estimated_fields"], list)
    assert got["scale_source"] == "bag_standard"
    assert got["mm_per_px"] == pytest.approx(0.6012)


def test_estimated_fields_accepts_the_plus_joined_wire_form(tmp_path):
    """测量侧线上发的是 `+` 串（`field_is_usable` 就那么 split）—— 两种形状都收。"""
    with client(tmp_path) as c:
        c.post("/ingest", json={"rows": [measurement(
            estimated_fields="mean_cluster_w_mm+mean_len_mm")]})
        got = c.get("/measurements").json()[0]
    assert got["estimated_fields"] == ["mean_cluster_w_mm", "mean_len_mm"]


def test_no_estimation_means_null_not_empty_string(tmp_path):
    """没有估算 ⇒ NULL（不是 `""`、不是 `[]`）：空串会让"没有估算"和"估算清单是空的"分不清。"""
    with client(tmp_path) as c:
        c.post("/ingest", json={"rows": [measurement(quality="verified")]})
        got = c.get("/measurements").json()[0]
    assert got["estimated_fields"] == []
    assert format_estimated_fields(None) is None and format_estimated_fields([]) is None
    assert parse_estimated_fields(None) == [] and parse_estimated_fields("") == []


def test_estimated_fields_list_is_deduped_and_trimmed():
    assert parse_estimated_fields(" a + b +a ") == ["a", "b"]
    assert format_estimated_fields({"b", "a"}) in ("b+a", "a+b")


# ---------- 标尺来源枚举 ----------


def test_scale_source_enum_and_transition_alias(capsys):
    for value in SCALE_SOURCES:
        assert normalize_scale_source(value) == value
    # 测量侧旧代码发的是 bag_diameter：归一 + 报警（不静默改，也不丢）
    assert normalize_scale_source("bag_diameter") == "bag_standard"
    assert "旧值" in capsys.readouterr().out


def test_unknown_scale_source_is_kept_and_reported(capsys):
    """未知值**保留原文 + 报警**：丢掉它等于"这个 mm 从哪来的"永久不可查。"""
    assert normalize_scale_source("bag_vibes") == "bag_vibes"
    assert "不在枚举" in capsys.readouterr().out
    assert normalize_scale_source(None) is None and normalize_scale_source("") is None


def test_scale_evidence_can_be_cross_computed():
    """`mm_per_px ≈ scale_ref_mm / scale_ref_px` —— 证据要能复算，否则只是两个数字。"""
    assert mm_per_px_from_ref(240.0, 400.0) == pytest.approx(0.6)
    assert mm_per_px_from_ref(240.0, 0) is None and mm_per_px_from_ref(None, 10) is None


# ---------- 两条 flag 的语义 ----------


def test_no_theta_does_not_make_the_row_unusable():
    """`no_theta` = "有标尺、只缺俯角"：行仍可用，字段级取舍交给 `estimated_fields`。"""
    quality = "estimate+no_theta+no_angle_profile"
    assert is_usable(quality) is True, "no_theta 不许进 NON_USABLE_FLAGS"
    assert is_usable("no_scale") is False, "真没标尺才是整行不可用"
    assert is_usable("estimate+height_uncorrected+no_theta") is True


def test_height_uncorrected_row_keeps_its_values_and_stays_out_of_verified_stats(tmp_path):
    """`height_uncorrected` 的竖直量是**投影跨度不是长度**：留得住（展示/相对比较），
    但绝不进 `verified` 统计（区间表/建议层只吃 verified）。"""
    app, db = app_and_db(tmp_path)
    with TestClient(app) as c:
        c.post("/ingest", json={"rows": [measurement(
            quality="estimate+no_theta+height_uncorrected+no_angle_profile",
            estimated_fields=["mean_cluster_w_mm", "mean_cluster_h_mm"],
            scale_source="bag_standard",
            mean_cluster_w_mm=61.5, mean_cluster_h_mm=44.0,
        )]})
        row = c.get("/measurements").json()[0]
    assert row["mean_cluster_h_mm"] == 44.0, "值不许被行级 flag 抹掉"
    assert row["estimated_fields"] == ["mean_cluster_w_mm", "mean_cluster_h_mm"]
    assert field_is_usable(row["quality"], row["estimated_fields"], "mean_cluster_w_mm") is False
    assert "verified" not in (row["quality"] or "")

    from analysis.align import compute_ranges

    run = compute_ranges(db)
    assert run.rows == [], "估算行不许进区间表（这行既没 verified，竖直量也不是长度）"


def test_filter_rule_is_the_measure_side_implementation():
    """过滤规则**只有一处实现**（测量侧），analysis 只是适配器 —— 两套实现迟早会漂。"""
    import measure.contract as contract

    assert field_is_usable("verified+no_angle_profile", "mean_cluster_w_mm", "mean_cluster_w_mm") is False
    assert field_is_usable("verified", ["mean_cluster_w_mm"], "mean_cluster_w_mm") is False
    assert field_is_usable("verified", ["mean_cluster_w_mm"], "mean_area_mm2") is True
    assert contract.NON_USABLE_FLAGS and "no_theta" not in contract.NON_USABLE_FLAGS
    assert "height_uncorrected" not in contract.NON_USABLE_FLAGS
