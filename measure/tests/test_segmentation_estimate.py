"""L1 菌包实例 / L3 菇体实例 / 估算层的单测（Chief 2026-10-14 批准的 L1–L3）。

合成图有真值：袋宽、菇高都以像素给死，用来钉住"局部尺度 ↔ mm 换算 ↔ 三码分开"。
**不测真实帧的绝对精度**（那是评估脚本的活，且没有真值）。
"""

import math

import numpy as np
import pytest

from measure import contract
from measure.bag_ruler import BagConfig, find_bags, nearest_bag_in_same_row, row_width_cv
from measure.estimate import (
    BAG_DIAMETER_MM,
    BAG_LENGTH_MM,
    EstimateConfig,
    estimate_cluster,
    estimate_stipe_length,
    local_scale,
)
from measure.fruitbody import FruitConfig, find_fruitbodies, median_height_px


# ---------- 合成图 ----------

def synth_frame(bag_width=200, bag_height=140, n_bags=3, bag_gray=200, bg_gray=30,
                pins=((0, 30), (1, 20), (2, 40)), pin_gray=255, H=400, W=900):
    """n_bags 个菌包横排 + 每袋顶上若干竖直细杆；返回 (gray, 真值 dict)。"""
    img = np.full((H, W), bg_gray, np.float32)
    gap = (W - n_bags * bag_width) // (n_bags + 1)
    truth = {"bag_width_px": bag_width, "pins": []}
    for i in range(n_bags):
        x0 = gap + i * (bag_width + gap)
        img[H - bag_height:, x0:x0 + bag_width] = bag_gray
        truth["bag_x0"] = truth.get("bag_x0", []) + [x0]
    for idx, h in pins:
        x0 = gap + idx * (bag_width + gap)
        cx = x0 + bag_width // 2
        half = 3
        img[H - bag_height - h:H - bag_height, cx - half:cx + half + 1] = pin_gray
        truth["pins"].append({"cx": cx, "height_px": h})
    return img, truth


def bags_of(gray, **kw):
    cfg = BagConfig(min_area_px=2000, seed_min_distance_px=80, row_band_px=200, **kw)
    return find_bags(gray, cfg)


def fruits_of(gray, bag_mask=None, **kw):
    cfg = FruitConfig(tophat_radius=5, tophat_threshold=15.0, min_area_px=10,
                      max_area_px=5000, seed_min_distance_px=6,
                      bag_dilate_px=2, bag_proximity_px=25, **kw)
    return find_fruitbodies(gray, bag_mask, cfg)


# ---------- L1 菌包 ----------

def test_find_bags_splits_adjacent_bags():
    gray, truth = synth_frame()
    bags, diag = bags_of(gray)
    assert diag["n_components"] >= 1
    assert len(bags) == 3, f"相邻菌包没拆开：{len(bags)}"
    widths = sorted(b.width_px for b in bags)
    assert widths == pytest.approx([truth["bag_width_px"]] * 3, rel=0.05)
    assert row_width_cv(bags, bags[0].row_id) == pytest.approx(0.0, abs=0.02)


def test_row_is_reliable_flags_low_contrast_row():
    """低对比/低纹理的排必须能被判成不可信；连候选都分不出时也算不可信。"""
    from measure.bag_ruler import row_is_reliable

    # 有候选、但对比度/纹理低于下限 ⇒ 不可信
    gray, _ = synth_frame(bag_gray=80, bg_gray=60)
    bags, _ = bags_of(gray, min_contrast=60.0, min_sigma=15.0)
    assert bags, "这一档对比度应还能分出候选（判据在下游）"
    assert not row_is_reliable(bags, bags[0].row_id)
    # 正常对比 ⇒ 可信
    gray2, _ = synth_frame()
    bags2, _ = bags_of(gray2)
    assert bags2 and row_is_reliable(bags2, bags2[0].row_id)
    # 完全分不出候选 ⇒ 也算不可信（不能因为"没候选"就当没问题）
    flat, _ = synth_frame(bag_gray=56, bg_gray=55)
    bags3, _ = bags_of(flat)
    assert not row_is_reliable(bags3, 0)


def test_nearest_bag_same_row_refuses_cross_row():
    gray, _ = synth_frame()
    bags, _ = bags_of(gray)
    assert nearest_bag_in_same_row(bags, bags[0].centroid_y + 500, BagConfig(row_band_px=100)) is None


# ---------- L3 菇体 ----------

def test_find_fruitbodies_detects_vertical_structures():
    gray, truth = synth_frame()
    fruits, diag = fruits_of(gray)
    assert diag["n_instances"] >= 2
    heights = sorted(f.height_px for f in fruits)
    truth_h = sorted(p["height_px"] for p in truth["pins"])
    assert heights[-1] >= 0.8 * max(truth_h)      # 最高的那根量得到
    assert median_height_px(fruits) is not None


def test_no_fruitbodies_on_flat_frame():
    flat = np.full((200, 300), 120, np.float32)
    fruits, diag = fruits_of(flat)
    assert fruits == [] and diag["n_instances"] == 0


# ---------- 袋肩门（2026-10-14：72–83% 的 L3 实例是袋面纹理） ----------

def _bag(cy=1000.0, height=200, row_id=2, x0=800, x1=1000):
    from measure.bag_ruler import BagRegion
    return BagRegion(x0=x0, y0=int(cy - height / 2), x1=x1, y1=int(cy + height / 2),
                     centroid_y=cy, centroid_x=(x0 + x1) / 2.0,
                     width_px=x1 - x0, height_px=height, area_px=(x1 - x0) * height,
                     width_profile=np.full(height, x1 - x0), row_id=row_id,
                     contrast=100.0, sigma=25.0)


def _fruit(base_y, *, cx=900.0, cy=None, h=30, w=20):
    from measure.fruitbody import FruitInstance
    cy = base_y - h if cy is None else cy
    return FruitInstance(cx=cx, cy=cy, x0=int(cx - w / 2), y0=int(cy - h / 2),
                         x1=int(cx + w / 2), y1=int(cy + h / 2),
                         height_px=h, width_px=w, area_px=h * w,
                         base_y=base_y, tip_y=base_y - h, on_bag=True)


def test_above_bag_top_keeps_shoulder_drops_surface_texture():
    """袋顶线 = 1000−200/2 = 900。base_y ≤ 910 留；扎在袋体内部的丢。"""
    from measure.estimate import filter_above_bag_top

    bags = [_bag()]
    shoulder = _fruit(base_y=895)      # 长在袋顶上 ⇒ 留
    on_edge = _fruit(base_y=905)       # 袋顶线附近（margin 10）⇒ 留
    surface = _fruit(base_y=980, cx=850)   # 袋体内部 ⇒ 袋面纹理，丢
    kept, diag = filter_above_bag_top([shoulder, on_edge, surface], bags, margin_px=10)
    assert [k.base_y for k in kept] == [895, 905]
    assert diag["above_bag_top"] == 2 and diag["inside_bag_body"] == 1
    assert diag["kept_frac"] == pytest.approx(2 / 3)


def test_above_bag_top_is_fail_closed_for_unassignable_instances():
    """同排找不到袋 ⇒ 证不了它长在袋上，丢弃（不猜、不默认保留）。"""
    from measure.estimate import filter_above_bag_top

    bags = [_bag(cy=1000.0, row_id=2)]
    far = _fruit(base_y=100, cy=100)   # 离袋 900px，超出 row_band ⇒ 未归属
    kept, diag = filter_above_bag_top([far], bags)
    assert kept == [] and diag["unassigned"] == 1 and diag["kept_frac"] == 0.0


def test_above_bag_top_handles_no_bags_and_no_instances():
    from measure.estimate import filter_above_bag_top

    assert filter_above_bag_top([], [_bag()]) == ([], {"kept": 0, "above_bag_top": 0,
                                                      "inside_bag_body": 0,
                                                      "unassigned": 0, "kept_frac": 0.0})
    assert filter_above_bag_top([_fruit(base_y=895)], [])[0] == []


# ---------- 估算层 ----------

def _one_instance_and_bag():
    gray, truth = synth_frame()
    bags, _ = bags_of(gray)
    fruits, _ = fruits_of(gray)
    return bags, fruits


def test_local_scale_requires_injected_diameter():
    """**不许内置猜测值**：不注入菌包直径 ⇒ no_scale、mm_per_px 为 None。"""
    bags, fruits = _one_instance_and_bag()
    s = local_scale(fruits[0], bags, bag_diameter_mm=None)
    assert s.mm_per_px is None and "no_scale" in s.flags
    s2 = local_scale(fruits[0], bags, bag_diameter_mm=140.0)
    assert s2.mm_per_px is not None and s2.source == "bag_diameter"
    assert s2.ref_mm == 140.0 and s2.ref_px > 0


def test_estimate_needs_both_injected_values():
    bags, fruits = _one_instance_and_bag()
    # 缺菌包直径
    e1 = estimate_stipe_length(fruits, bags, bag_diameter_mm=None, depression_deg=20.0)
    assert e1.mean_len_mm is None and "no_scale" in e1.quality
    assert e1.mean_len_px is not None            # 像素量仍有（但不冒充 mm）
    # 缺俯角 ⇒ no_theta（**不是 no_scale**：标尺有，只是投影项缺）
    e2 = estimate_stipe_length(fruits, bags, bag_diameter_mm=140.0, depression_deg=None)
    assert e2.mean_len_mm is None
    assert "no_theta" in e2.quality and "no_scale" not in e2.quality
    # 两个都给 ⇒ 出 mm + estimate
    e3 = estimate_stipe_length(fruits, bags, bag_diameter_mm=140.0, depression_deg=20.0)
    assert e3.mean_len_mm is not None and e3.mean_len_mm > 0
    assert "estimate" in e3.quality and e3.estimated_fields == ("mean_len_mm",)
    assert e3.uncertainty_mm is not None and e3.uncertainty_mm > 0


def test_estimate_mm_math_and_projection():
    """真值核算：袋宽 200px、直径注入 200mm ⇒ 1 mm/px；杆 30px、θ=0 ⇒ 30mm。"""
    gray, truth = synth_frame(bag_width=200)
    bags, _ = bags_of(gray)
    fruits, _ = fruits_of(gray)
    longest = max(fruits, key=lambda f: f.height_px)
    e = estimate_stipe_length([longest], bags, bag_diameter_mm=200.0, depression_deg=0.0)
    assert e.mean_len_mm == pytest.approx(longest.height_px * 1.0, rel=0.02)
    # θ=60° ⇒ cos=0.5 ⇒ 读数翻倍
    e2 = estimate_stipe_length([longest], bags, bag_diameter_mm=200.0, depression_deg=60.0)
    assert e2.mean_len_mm == pytest.approx(e.mean_len_mm / math.cos(math.radians(60)), rel=0.02)


def test_three_codes_are_distinct():
    """`unreliable` / `no_detection` / `no_scale` 不许混成一个（Chief 裁定 5）。"""
    bags, fruits = _one_instance_and_bag()
    e_none = estimate_stipe_length([], bags, bag_diameter_mm=140.0, depression_deg=20.0)
    assert "no_detection" in e_none.quality
    e_unrel = estimate_stipe_length(fruits, bags, bag_diameter_mm=140.0, depression_deg=20.0,
                                    row_reliable=lambda i: False)
    assert "unreliable" in e_unrel.quality and "no_scale" not in e_unrel.quality
    e_noscale = estimate_stipe_length(fruits, bags, bag_diameter_mm=None, depression_deg=None)
    assert "no_scale" in e_noscale.quality
    # 三码都进"不可用"集合（值应为 NULL）
    for code in ("unreliable", "no_detection", "no_scale"):
        assert code in contract.NON_USABLE_FLAGS


def test_estimate_field_is_not_usable_without_verified():
    """票 18 §2：estimate 与 verified 正交；只是 estimate 的行不得进统计。"""
    assert not contract.field_is_usable("baseline+estimate", "mean_len_mm", "mean_len_mm")
    assert contract.field_is_usable("verified", None, "mean_len_mm")
    assert not contract.field_is_usable("verified", "mean_len_mm", "mean_len_mm")


def test_no_zero_is_written_for_missing_values():
    """缺标尺时 mm 列必须是 None（NULL），**不是 0**。"""
    bags, fruits = _one_instance_and_bag()
    e = estimate_stipe_length(fruits, bags, bag_diameter_mm=None, depression_deg=None)
    assert e.mean_len_mm is None and e.mean_len_mm != 0


# ---------- 一期验收量：菌簇长度 / 菌簇直径（Chief 2026-10-14 裁定） ----------

def test_cluster_estimate_uses_standard_bag_sizes():
    """袋长 240mm 定水平、袋径 140mm 定竖直；真值：袋宽 200px ⇒ 水平 1.2 mm/px。"""
    gray, _ = synth_frame(bag_width=200)
    bags, _ = bags_of(gray)
    fruits, _ = fruits_of(gray)
    e = estimate_cluster(fruits, bags, depression_deg=0.0)
    assert e.mm_per_px_x == pytest.approx(240.0 / 200, rel=0.05)
    assert e.mm_per_px_y == pytest.approx(140.0 / 200, rel=0.05)
    assert e.cluster_w_mm == pytest.approx(e.cluster_w_px * e.mm_per_px_x, rel=1e-6)
    assert e.cluster_h_mm == pytest.approx(e.cluster_h_px * e.mm_per_px_y, rel=1e-6)
    assert "estimate" in e.quality
    assert "mean_cluster_w_mm" in e.estimated_fields and "mean_cluster_h_mm" in e.estimated_fields
    assert "交叉校验" in e.scale_note


def test_cluster_height_needs_depression_but_width_does_not():
    """水平不受俯角影响 ⇒ 缺 θ 时直径照出、长度写 NULL + **`no_theta`（不是 `no_scale`）**。

    Chief 2026-10-14：把"θ 缺"记成 `no_scale` 会让 213/218 帧的有效直径被下游整行丢掉。
    """
    gray, _ = synth_frame()
    bags, _ = bags_of(gray)
    fruits, _ = fruits_of(gray)
    e = estimate_cluster(fruits, bags, depression_deg=None)
    assert e.cluster_w_mm is not None
    assert e.cluster_h_mm is None
    assert "no_theta" in e.quality
    assert "no_scale" not in e.quality          # 标尺是有的，不许说成没有
    assert "mean_cluster_h_mm" not in e.estimated_fields
    # 行**不算不可用**：有有效值（w），只是缺投影修正项
    assert contract.is_usable(e.quality), "no_theta 不许让整行变不可用"


def test_no_theta_does_not_kill_the_row_but_no_scale_does():
    """两个标记的处置必须不同：`no_theta` 只描述缺哪个修正项，`no_scale` 才是没标尺。"""
    gray, _ = synth_frame()
    bags, _ = bags_of(gray)
    fruits, _ = fruits_of(gray)
    with_theta = estimate_cluster(fruits, bags, depression_deg=20.0).quality
    no_theta = estimate_cluster(fruits, bags, depression_deg=None).quality
    no_scale = estimate_cluster(fruits, [], depression_deg=20.0).quality   # 没袋 ⇒ 真没标尺
    assert contract.is_usable(with_theta) and contract.is_usable(no_theta)
    assert not contract.is_usable(no_scale)
    assert "no_theta" not in no_scale and "no_scale" in no_scale


def test_uncorrected_height_is_off_by_default():
    """裁定 (b) 的开关**默认关**：不开启时 θ 未注入仍写 NULL（保住 NULL 语义）。"""
    gray, _ = synth_frame()
    bags, _ = bags_of(gray)
    fruits, _ = fruits_of(gray)
    e = estimate_cluster(fruits, bags, depression_deg=None)
    assert e.cluster_h_mm is None
    assert "height_uncorrected" not in e.quality
    assert "mean_cluster_h_mm" not in e.estimated_fields


def test_uncorrected_height_switch_emits_projection_span():
    """开启后竖直向出**未做 cosθ 补偿的投影跨度**：= 补偿值 × cosθ，且标记齐全。"""
    gray, _ = synth_frame()
    bags, _ = bags_of(gray)
    fruits, _ = fruits_of(gray)
    e_on = estimate_cluster(fruits, bags, depression_deg=None,
                            allow_uncorrected_height=True)
    e_theta = estimate_cluster(fruits, bags, depression_deg=30.0)
    assert e_on.cluster_h_mm is not None and e_on.cluster_h_mm > 0
    assert e_on.cluster_h_mm == pytest.approx(
        e_theta.cluster_h_mm * math.cos(math.radians(30.0)), rel=1e-6)
    # 标记齐全：estimate + no_theta + height_uncorrected，且**不许出现 verified**
    flags = contract.parse_flags(e_on.quality)
    assert {"estimate", "no_theta", "height_uncorrected"} <= flags
    assert "verified" not in flags
    assert "no_scale" not in flags
    assert "mean_cluster_h_mm" in e_on.estimated_fields
    assert "mean_cluster_w_mm" in e_on.estimated_fields
    # 「未补偿」不许让整行不可用（字段级取舍交给 estimated_fields）
    assert contract.is_usable(e_on.quality)
    assert "投影跨度" in e_on.note and "不是长度" in e_on.note


def test_uncorrected_height_differs_from_theta_corrected():
    """θ 注入后是同列但**不同口径**：两者数值必须能区分，防止被混比。"""
    gray, _ = synth_frame()
    bags, _ = bags_of(gray)
    fruits, _ = fruits_of(gray)
    e_unc = estimate_cluster(fruits, bags, depression_deg=None,
                             allow_uncorrected_height=True)
    e_cor = estimate_cluster(fruits, bags, depression_deg=25.0)
    assert e_unc.cluster_h_mm < e_cor.cluster_h_mm       # 未补偿必然偏小
    assert "height_uncorrected" in e_unc.quality
    assert "height_uncorrected" not in e_cor.quality


def test_cluster_estimate_three_codes_still_distinct():
    gray, _ = synth_frame()
    bags, _ = bags_of(gray)
    fruits, _ = fruits_of(gray)
    assert "no_detection" in estimate_cluster([], bags, depression_deg=20.0).quality
    assert "unreliable" in estimate_cluster(fruits, bags, depression_deg=20.0,
                                            row_reliable=lambda i: False).quality
    # 第四条：θ 缺 = no_theta；真没标尺 = no_scale（两个不许混）
    assert "no_theta" in estimate_cluster(fruits, bags, depression_deg=None).quality
    assert "no_scale" in estimate_cluster(fruits, [], depression_deg=20.0).quality


def test_cluster_mm_is_none_not_zero_when_unmeasurable():
    gray, _ = synth_frame()
    bags, _ = bags_of(gray)
    fruits, _ = fruits_of(gray)
    e = estimate_cluster(fruits, [], depression_deg=20.0)      # 没有袋 ⇒ 无标尺
    assert e.cluster_w_mm is None and e.cluster_h_mm is None
    assert "no_scale" in e.quality


def test_far_row_without_instances_reports_unreliable_not_no_detection():
    """**远排不许静默消失**：整帧只剩不可信的排时，即使没实例也要出 `unreliable`。"""
    from measure.estimate import all_rows_unreliable, make_row_reliable

    # 造一个低对比"远排"：对比度/纹理都低于下限 ⇒ 该排不可信
    gray, _ = synth_frame(bag_gray=80, bg_gray=60)
    bags, _ = bags_of(gray, min_contrast=60.0, min_sigma=15.0)
    assert bags and all_rows_unreliable(bags, BagConfig(min_contrast=60.0, min_sigma=15.0))
    e = estimate_cluster([], bags, depression_deg=20.0)
    assert "unreliable" in e.quality and "no_detection" not in e.quality
    e2 = estimate_stipe_length([], bags, bag_diameter_mm=140.0, depression_deg=20.0)
    assert "unreliable" in e2.quality

    # 正常排 + 无实例 ⇒ 才是 no_detection
    gray2, _ = synth_frame()
    bags2, _ = bags_of(gray2)
    assert not all_rows_unreliable(bags2)
    e3 = estimate_cluster([], bags2, depression_deg=20.0)
    assert "no_detection" in e3.quality

    # 回调接线：实例 → 同排菌包 → 该排可信否
    rel = make_row_reliable(bags2)
    fruits2, _ = fruits_of(gray2)
    assert all(rel(f) for f in fruits2)
