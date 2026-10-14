"""L1–L3 生产接线（票 15 §14-2，Chief 2026-10-14 批准）的契约测试。

钉住四件（Chief 的四条要求）：
1. 行仍是 **20 列**、`estimated_fields` 是 `+` 串、`scale_source` 用环境官枚举；
2. `allow_uncorrected_height` 开启后 `height_uncorrected` **必出**，且**轮日志能看出未补偿**；
3. `no_theta` **不在不可用集**；缺标尺时证据列不写假值；
4. 菌簇的**两个方向局部尺度**分别生效（水平 240 mm、竖直 140 mm），不共用一个 mm/px。
"""

import csv

import numpy as np
import pytest

from measure import contract
from measure.l3 import L3Config, L3Detector, detector_from_settings
from measure.pipeline import aggregate_box
from measure.production import FrameInput, make_measure_fn, rows_from_frames
from measure.record import RECORD_FIELDS


def synth_frame(bag_gray=200, bg_gray=30, n_bags=3, bag_width=200, bag_height=140,
                pins=((0, 30), (1, 20), (2, 40)), pin_gray=255, H=400, W=900):
    """与 test_segmentation_estimate 同构的最简合成帧（袋横排 + 袋顶细杆）。"""
    img = np.full((H, W), bg_gray, np.float32)
    gap = (W - n_bags * bag_width) // (n_bags + 1)
    for i in range(n_bags):
        x0 = gap + i * (bag_width + gap)
        img[H - bag_height:, x0:x0 + bag_width] = bag_gray
    for idx, h in pins:
        x0 = gap + idx * (bag_width + gap)
        cx = x0 + bag_width // 2
        img[H - bag_height - h:H - bag_height, cx - 3:cx + 4] = pin_gray
    return img


def detect(image, **kw):
    cfg = L3Config(**kw)
    return L3Detector(cfg).detect_frame(image, box_id="B105")


def frame_row(box="B105", ts="2026-10-13T15:00:00"):
    return {"ts": ts, "box_id": box, "station_id": "S105", "angle_profile": "level0",
            "cloud_url": "http://minio.local/obj.jpg"}


def stubs(image):
    def fetch(url):
        return b"stub"

    def decode(data):
        return image

    return fetch, decode


# ---------- 两个方向的局部尺度 ----------

def test_cluster_detections_carry_two_direction_scales():
    img = synth_frame()
    res = detect(img, depression_deg=None, bag_cfg=_bag_cfg())
    assert res.detections, "接线后应至少出一个簇"
    for d in res.detections:
        m = d.metrics
        assert m.cluster_w_px > 0 and m.cluster_h_px > 0
        # 水平 240 mm、竖直 140 mm 各自除以同一个袋参考宽
        assert m.mm_per_px_x == pytest.approx(240.0 / _ref_px(d), rel=0.35)
        assert m.mm_per_px_y == pytest.approx(m.mm_per_px_x * 140.0 / 240.0, rel=1e-6)


def test_aggregate_uses_per_axis_scale_not_one_mm_per_px():
    """竖直列必须走 `mm_per_px_y`：若误用 `mm_per_px_x`，高度会大 240/140 倍。"""
    img = synth_frame()
    res = detect(img, depression_deg=None, bag_cfg=_bag_cfg())
    stats = aggregate_box(res.detections)
    d = res.detections[0]
    assert stats.mean_cluster_w_mm == pytest.approx(
        float(np.mean([x.metrics.cluster_w_px * x.metrics.mm_per_px_x
                       for x in res.detections])), rel=1e-6)
    expect_h = float(np.mean([x.metrics.cluster_h_px * x.metrics.mm_per_px_y
                              for x in res.detections]))
    assert stats.mean_cluster_h_mm == pytest.approx(expect_h, rel=1e-6)
    assert stats.mean_cluster_h_mm != pytest.approx(
        float(np.mean([x.metrics.cluster_h_px * x.metrics.mm_per_px_x
                       for x in res.detections])), rel=1e-3)
    # 交叉复算：mm_per_px ≈ ref_mm / ref_px（水平那一路）
    assert d.metrics.mm_per_px_x * _ref_px(d) == pytest.approx(240.0, rel=0.35)


def _bag_cfg():
    from measure.bag_ruler import BagConfig

    return BagConfig(min_area_px=2000, seed_min_distance_px=80, row_band_px=200)


def _ref_px(detection) -> float:
    return 240.0 / detection.metrics.mm_per_px_x


# ---------- 端到端产行（真机路径的离线版） ----------

def test_wired_row_has_20_columns_and_full_evidence():
    img = synth_frame()
    fetch, decode = stubs(img)
    logs: list[str] = []
    rows = rows_from_frames([frame_row()], room_id="611", mm_per_px=None,
                            detector=L3Detector(L3Config(bag_cfg=_bag_cfg())),
                            fetch=fetch, decode=decode, log=logs.append)
    assert len(rows) == 1
    row = rows[0]
    assert tuple(row.keys()) == tuple(list(RECORD_FIELDS) + ["kind"])
    q = contract.parse_flags(row["quality"])
    assert {"estimate", "no_theta", "height_uncorrected"} <= q
    assert "verified" not in q
    assert "no_scale" not in q
    # ① estimated_fields 是 `+` 串；② scale_source 用环境官枚举
    assert row["estimated_fields"] == "mean_cluster_w_mm+mean_cluster_h_mm"
    assert row["scale_source"] == "bag_standard"
    assert row["scale_ref_mm"] == 240.0 and row["scale_ref_px"] > 0
    assert row["mm_per_px"] == pytest.approx(240.0 / row["scale_ref_px"], rel=0.2)
    # ③ no_theta 不在不可用集 ⇒ 行仍可用
    assert "no_theta" not in contract.NON_USABLE_FLAGS
    assert "height_uncorrected" not in contract.NON_USABLE_FLAGS
    assert contract.is_usable(row["quality"])
    # ④ 两个交付列都出值；盖径/杆长仍 NULL
    assert row["mean_cluster_w_mm"] and row["mean_cluster_w_mm"] > 0
    assert row["mean_cluster_h_mm"] and row["mean_cluster_h_mm"] > 0
    assert row["mean_len_mm"] is None and row["mean_cap_mm"] is None
    # ⑤ n = **朵数**（实例数）而不是簇数
    res = L3Detector(L3Config(bag_cfg=_bag_cfg())).detect_frame(img, box_id="B105")
    assert row["n"] == res.evidence.n_instances >= len(res.detections)


def test_round_log_says_the_height_column_is_uncorrected():
    """Chief 要求：半年后要能从轮日志看出竖直列**未补偿**。"""
    img = synth_frame()
    fetch, decode = stubs(img)
    logs: list[str] = []
    rows_from_frames([frame_row()], room_id="611", detector=L3Detector(L3Config(bag_cfg=_bag_cfg())),
                     fetch=fetch, decode=decode, log=logs.append)
    blob = "\n".join(logs)
    assert "height_uncorrected" in blob
    assert "未做 cosθ 补偿的竖直投影跨度" in blob and "不是长度" in blob


def test_missing_reference_emits_enum_none_and_no_fabricated_values():
    """缺标尺：`scale_source=none`、mm 与其余证据列**不写假值**、带 `no_scale`。"""
    # 有纹理（不被 blurry 门拦）但没有菌包结构：噪声图，Otsu 分不出成规模的连通域
    rng = np.random.default_rng(0)
    textured = rng.normal(128.0, 3.0, (400, 900)).astype(np.float32)
    fetch, decode = stubs(textured)
    rows = rows_from_frames([frame_row()], room_id="611",
                            detector=L3Detector(L3Config(bag_cfg=_bag_cfg())),
                            fetch=fetch, decode=decode, emit_empty=True, log=lambda *_: None)
    assert len(rows) == 1
    row = rows[0]
    q0 = contract.parse_flags(row["quality"])
    assert "blurry" not in q0 and "no_light" not in q0, "帧质量门不该拦掉这一帧"
    assert row["scale_source"] == "none"             # 环境官枚举里的"没有标尺"
    assert row["estimated_fields"] is None
    assert row["mm_per_px"] is None and row["scale_ref_mm"] is None and row["scale_ref_px"] is None
    assert row["mean_cluster_w_mm"] is None and row["mean_cluster_h_mm"] is None
    q = contract.parse_flags(row["quality"])
    assert {"no_scale", "no_detection"} <= q
    # 零候选是 no_detection，**不许**混成 unreliable
    assert "unreliable" not in q
    assert not contract.is_usable(row["quality"])


def synth_two_unequal_bags():
    """两袋**不等宽**（200 / 300 px）+ 各一根细杆 ⇒ 能实测"标尺散布"（≥2 个尺度才可估）。

    杆与袋顶之间**留 2px 间隙**：否则合成图里亮杆与袋会连成同一个连通域，
    袋 bbox 被抬到杆顶、`bag_top` 算错，`filter_above_bag_top` 会把真杆判成"袋体内"。
    """
    img = np.full((400, 900), 30.0, np.float32)
    img[260:, 50:250] = 200          # 袋 A：宽 200
    img[260:, 500:800] = 200         # 袋 B：宽 300
    img[228:258, 147:154] = 255      # A 上的细杆（底 257，与袋顶 260 隔 2px）
    img[228:258, 647:654] = 255      # B 上的细杆
    return img


def test_scale_dispersion_reaches_the_round_log():
    """标尺散布（主导误差项）**不是列**（一期不加列）⇒ 必须进轮日志，否则等于没测。

    Chief 2026-10-14 裁定的一半：值留着、进日志/票面/复核材料，**不进页面**。
    """
    img = synth_two_unequal_bags()
    fetch, decode = stubs(img)
    logs: list[str] = []
    rows_from_frames([frame_row()], room_id="611",
                     detector=L3Detector(L3Config(bag_cfg=_bag_cfg())),
                     fetch=fetch, decode=decode, log=logs.append)
    res = L3Detector(L3Config(bag_cfg=_bag_cfg())).detect_frame(img, box_id="B105")
    assert res.evidence.scale_dispersion is not None and res.evidence.scale_dispersion > 0
    assert "仅袋宽分割项" in res.evidence.note
    blob = "\n".join(logs)
    assert "标尺散布 Δw/w" in blob and "仅袋宽分割项" in blob
    assert "不是列" in blob, "日志要写清它不是 measurements 的列（一期不加列）"


def test_station_calibration_has_no_scale_dispersion():
    """站位标定值口径下所有实例共用一个常数 ⇒ 不报"袋宽散布"（避免误导）。"""
    img = synth_frame()
    res = detect(img, station_mm_per_px=0.5, depression_deg=None, bag_cfg=_bag_cfg())
    assert res.evidence.scale_dispersion is None


def test_all_rows_unreliable_yields_unreliable_not_no_detection():
    """**与上一条并排的反例**（Chief 2026-10-14）：整帧只剩不可信的排 ⇒ `unreliable`。

    两条必须成对存在：零候选是"没找到东西"（`no_detection`），
    有候选但判不可信是"找到了但不可靠"（`unreliable`）——后人别把其中一条"简化"掉。
    """
    from measure.bag_ruler import row_is_reliable

    # 低对比档：菌包仍能被分出候选，但每排都判不可信（对比 20）
    img = synth_frame(bag_gray=80, bg_gray=60)
    res = detect(img, depression_deg=None, bag_cfg=_bag_cfg(), fruit_cfg=_fruit_cfg())
    bags, _ = __import__("measure.bag_ruler", fromlist=["find_bags"]).find_bags(img, _bag_cfg())
    assert bags, "低对比排也必须先有候选（L1 不许静默丢排）"
    assert not any(row_is_reliable(bags, b.row_id, _bag_cfg()) for b in bags)
    flags = set(res.evidence.flags)
    assert "unreliable" in flags
    assert "no_detection" not in flags

    # 端到端：空帧也要留痕，且**不许**混成 no_detection
    fetch, decode = stubs(img)
    rows = rows_from_frames([frame_row()], room_id="611",
                            detector=L3Detector(L3Config(bag_cfg=_bag_cfg())),
                            fetch=fetch, decode=decode, emit_empty=True, log=lambda *_: None)
    assert len(rows) == 1
    q = contract.parse_flags(rows[0]["quality"])
    assert "unreliable" in q and "no_detection" not in q
    assert rows[0]["mean_cluster_w_mm"] is None and rows[0]["mean_cluster_h_mm"] is None
    assert "unreliable" in contract.NON_USABLE_FLAGS and not contract.is_usable(rows[0]["quality"])


def _fruit_cfg():
    from measure.fruitbody import FruitConfig

    return FruitConfig(downscale=2)


def test_station_calibration_takes_priority_over_bag_standard():
    """ADR-0024：站位 `--mm-per-px` 有值就优先，`scale_source=calibration_target`。"""
    img = synth_frame()
    res = detect(img, station_mm_per_px=0.5, depression_deg=None, bag_cfg=_bag_cfg())
    d = res.detections[0]
    assert d.metrics.mm_per_px_x == pytest.approx(0.5)
    assert d.metrics.mm_per_px_y == pytest.approx(0.5)      # θ 未注入 ⇒ 不做补偿
    assert res.evidence.scale_source == "calibration_target"
    assert res.evidence.scale_ref_mm is None                # 站位标定不靠参考物尺寸


def test_theta_injected_applies_projection_to_vertical_only():
    img = synth_frame()
    res = detect(img, depression_deg=30.0, bag_cfg=_bag_cfg())
    d = res.detections[0]
    import math

    assert d.metrics.mm_per_px_y == pytest.approx(
        d.metrics.mm_per_px_x * 140.0 / 240.0 / math.cos(math.radians(30.0)), rel=1e-6)
    assert "no_theta" not in res.evidence.flags
    assert "height_uncorrected" not in res.evidence.flags


def test_make_measure_fn_defaults_to_l3_detector():
    from measure.l3 import L3Detector as _L3

    fetch, decode = stubs(synth_frame())
    fn = make_measure_fn(room_id="611", mm_per_px=None, fetch=fetch, decode=decode,
                         log=lambda *_: None)
    rows = fn(type("R", (), {"results": [frame_row()]})())
    assert rows, "默认检出器应已切到 L1–L3 并产出估算行"
    assert rows[0]["scale_source"] == "bag_standard"
    # 显式传 detector 时完全按传的来（回退路径仍在）
    fn2 = make_measure_fn(room_id="611", detector=_L3(L3Config(bag_cfg=_bag_cfg())),
                          fetch=fetch, decode=decode, log=lambda *_: None)
    assert fn2(type("R", (), {"results": [frame_row()]})())[0]["scale_source"] == "bag_standard"
