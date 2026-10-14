"""`measure.production` 的单元测试：接线契约 + 失败帧不产行 + 缺测语义。

取图/解码全靠注入的 stub，所以这些用例不需要网络、不需要 Pillow——现场那台机器
（只有 numpy + Pillow）能跑，CI 也能跑。
"""

import time

import numpy as np
import pytest

from measure.baseline import BaselineConfig, BaselineDetector
from measure.pipeline import Detection
from measure.production import (
    FrameInput,
    make_measure_fn,
    rows_from_frames,
    usable_instance_count,
)

MM_PER_PX = 0.5
ROOM = "611"


def frame(box="B105", ts="2026-10-13T15:00:00", url="http://minio.local/obj.jpg",
          station="S105", angle="level0") -> dict:
    return {"ts": ts, "box_id": box, "station_id": station, "angle_profile": angle,
            "cloud_url": url}


def circle(h=300, w=300, cy=150, cx=150, r=55, bg=30, fg=235) -> np.ndarray:
    yy, xx = np.mgrid[0:h, 0:w]
    img = np.full((h, w, 3), bg, np.uint8)
    img[(yy - cy) ** 2 + (xx - cx) ** 2 <= r * r] = fg
    return img


def stubs(image: np.ndarray, *, fail: bool = False):
    def fetch(url):
        if fail:
            raise RuntimeError("boom")
        return b"stub"

    def decode(data):
        return image

    return fetch, decode


class StubDetector:
    """固定返回一个实例的检出器（不走图像，测接线用）。"""

    def __init__(self, *, flags: str = "", n: int = 1):
        self.flags = flags
        self.n = n

    def detect(self, image, *, mm_per_px, box_id):
        return [Detection(box_id=box_id, len_mm=None, cap_mm=None, flags=self.flags)
                for _ in range(self.n)]


DET = BaselineDetector(BaselineConfig(min_area_px_floor=100))


def test_row_has_contract_fields_and_null_unmeasurable():
    fetch, decode = stubs(circle())
    rows = rows_from_frames([frame()], room_id=ROOM, mm_per_px=MM_PER_PX,
                            detector=DET, fetch=fetch, decode=decode)
    assert len(rows) == 1
    row = rows[0]
    assert row["kind"] == "measurement"
    assert row["room_id"] == ROOM
    assert row["box_id"] == "B105"
    assert row["station_id"] == "S105"
    assert row["angle_profile"] == "level0"
    assert row["ts"] == "2026-10-13T15:00:00"
    # 单档平视：菌盖直径 / 菌杆长度明确缺测
    assert row["mean_cap_mm"] is None
    assert row["mean_len_mm"] is None
    assert row["p10_len"] is None and row["p90_len"] is None
    # 菌簇四列：平视单档下唯一可用的长势量，必须真发（毫米/平方毫米）
    assert row["mean_cluster_h_mm"] == pytest.approx(109 * MM_PER_PX, rel=0.05)
    assert row["mean_cluster_w_mm"] == pytest.approx(109 * MM_PER_PX, rel=0.05)
    assert row["mean_area_mm2"] == pytest.approx(9473 * MM_PER_PX ** 2, rel=0.05)
    assert row["mean_projected_d_mm"] == pytest.approx(109.8 * MM_PER_PX, rel=0.05)
    assert row["n"] == 1
    assert "no_angle_profile" in row["quality"]
    # `baseline` 2026-10-14 起**停发**（Chief 四条裁定）：新行不许再带它
    assert "baseline" not in row["quality"], "新行仍在发历史标记 baseline"
    # 平视口径必须标清（Chief 2026-10-13 裁定）
    assert "level_view" in row["quality"]
    assert "no_detection" not in row["quality"]


def test_detection_value_is_not_a_cap_measurement():
    """回归（票 11 §9）：检出里的 cap 值不许进 mean_cap_mm。"""
    fetch, decode = stubs(circle())
    rows = rows_from_frames([frame()], room_id=ROOM, mm_per_px=MM_PER_PX,
                            detector=StubDetector(), fetch=fetch, decode=decode)
    assert rows[0]["mean_cap_mm"] is None


def test_fetch_failure_produces_no_row_by_default():
    fetch, decode = stubs(circle(), fail=True)
    rows = rows_from_frames([frame()], room_id=ROOM, mm_per_px=MM_PER_PX,
                            detector=DET, fetch=fetch, decode=decode, log=lambda *_: None)
    assert rows == []


def test_fetch_failure_emits_empty_row_when_asked():
    fetch, decode = stubs(circle(), fail=True)
    rows = rows_from_frames([frame()], room_id=ROOM, mm_per_px=MM_PER_PX,
                            detector=DET, fetch=fetch, decode=decode,
                            emit_empty=True, log=lambda *_: None)
    assert len(rows) == 1
    assert rows[0]["n"] == 0
    assert "no_detection" in rows[0]["quality"]
    assert rows[0]["mean_len_mm"] is None and rows[0]["mean_cap_mm"] is None


def test_blank_frame_is_gated_and_produces_no_row():
    """全黑帧过不了质量门（模糊/过曝）⇒ 不产行（失败帧不是 0 测量值）。"""
    fetch, decode = stubs(np.full((200, 200, 3), 20, np.uint8))
    rows = rows_from_frames([frame()], room_id=ROOM, mm_per_px=MM_PER_PX,
                            detector=DET, fetch=fetch, decode=decode, log=lambda *_: None)
    assert rows == []
    rows2 = rows_from_frames([frame()], room_id=ROOM, mm_per_px=MM_PER_PX,
                             detector=DET, fetch=fetch, decode=decode,
                             emit_empty=True, log=lambda *_: None)
    assert "no_detection" in rows2[0]["quality"]


def test_missing_cloud_url_still_skips_or_marks():
    fetch, decode = stubs(circle())
    rows = rows_from_frames([frame(url=None)], room_id=ROOM, mm_per_px=MM_PER_PX,
                            detector=DET, fetch=fetch, decode=decode)
    assert rows == []


def test_frame_without_ts_or_box_is_ignored():
    assert rows_from_frames([{"cloud_url": "x"}], room_id=ROOM, mm_per_px=MM_PER_PX,
                            detector=DET, fetch=lambda u: b"", decode=lambda b: circle()) == []


def test_low_confidence_zeroes_count():
    fetch, decode = stubs(circle())
    rows = rows_from_frames([frame()], room_id=ROOM, mm_per_px=MM_PER_PX,
                            detector=StubDetector(flags="low_confidence"),
                            fetch=fetch, decode=decode)
    assert rows[0]["n"] == 0
    assert "low_confidence" in rows[0]["quality"]


def test_usable_instance_count_from_stats():
    from measure.pipeline import BoxStats

    assert usable_instance_count(BoxStats(box_id="B105", n_used=3)) == 3
    assert usable_instance_count(BoxStats(box_id="B105", n_used=3,
                                          quality_flags="low_confidence")) == 0


def test_measure_fn_runs_against_report_like_object():
    fetch, decode = stubs(circle())
    fn = make_measure_fn(room_id=ROOM, mm_per_px=MM_PER_PX, detector=DET,
                         fetch=fetch, decode=decode)

    class Report:
        results = [frame(box="B105"), frame(box="B106", ts="2026-10-13T15:00:30")]

    rows = fn(Report())
    assert {r["box_id"] for r in rows} == {"B105", "B106"}
    assert all(r["kind"] == "measurement" for r in rows)
    assert all(r["room_id"] == ROOM for r in rows)


def test_make_measure_fn_refuses_empty_room_id():
    with pytest.raises(ValueError):
        make_measure_fn(room_id="", mm_per_px=MM_PER_PX)


def test_two_rooms_same_box_id_do_not_collide():
    """同 ts、同 box_id、不同库房 ⇒ 两行不同（幂等键含 room_id）。"""
    fetch, decode = stubs(circle())
    rows_a = rows_from_frames([frame()], room_id="611", mm_per_px=MM_PER_PX,
                              detector=DET, fetch=fetch, decode=decode)
    rows_b = rows_from_frames([frame()], room_id="612", mm_per_px=MM_PER_PX,
                              detector=DET, fetch=fetch, decode=decode)
    key_a = (rows_a[0]["ts"], rows_a[0]["room_id"], rows_a[0]["box_id"])
    key_b = (rows_b[0]["ts"], rows_b[0]["room_id"], rows_b[0]["box_id"])
    assert key_a != key_b


def test_frame_from_meta_keeps_station_and_angle():
    from measure.production import frame_from_meta

    f = frame_from_meta(frame())
    assert isinstance(f, FrameInput)
    assert f.angle_profile == "level0" and f.station_id == "S105"
    assert frame_from_meta({"box_id": "B105"}) is None


# --- 每帧耗时与超门计数（Chief 2026-10-14：超门要看得见，票 15 §8.1） ---------


def test_timing_summary_counts_over_gate_frames():
    from measure.production import MEASURE_TIME_GATE_S, timing_summary

    # 门已改判：单帧 3.75 s / 轮 180 s（Chief 2026-10-14，票 15 §8.1）
    assert MEASURE_TIME_GATE_S == pytest.approx(180.0 / 48)
    line = timing_summary([0.9, 1.05, 4.4, 0.8], gate_s=1.1, round_gate_s=180.0)
    assert "4 帧" in line and "超门(>1.10 s) 1 帧" in line
    # 偶数帧数的中位数 = 中间两个的均值（[0.8, 0.9, 1.05, 4.4] ⇒ 0.975 ≈ 0.98）
    assert "均 1.79 s/帧" in line and "中位 0.98 s" in line
    assert "轮增量在门内" in line


def test_timing_summary_flags_round_over_gate():
    """轮增量超门（>180 s）必须自己喊出来，不能只报均值。"""
    from measure.production import timing_summary

    line = timing_summary([4.5] * 48, round_gate_s=180.0)
    assert "轮增量超门" in line and "216 s vs ≤180 s" in line


def test_timing_summary_handles_empty_and_all_under_gate():
    from measure.production import timing_summary

    assert "0 帧" in timing_summary([])
    assert "超门(>3.75 s) 0 帧" in timing_summary([0.1, 0.2, 0.3])


def test_rows_from_frames_logs_timing_and_flags_over_gate():
    """每轮必须留下一行耗时摘要；超门帧要单独喊出来（不许只能靠人感觉）。"""
    fetch, decode = stubs(circle())
    logs: list[str] = []
    rows_from_frames([frame()], room_id=ROOM, mm_per_px=MM_PER_PX, detector=DET,
                     fetch=fetch, decode=decode, log=logs.append)
    assert any("测量：1 帧" in m for m in logs), logs

    # 人为把单帧拖过门（慢 decode），验证超门那条路径真的会喊
    def slow_decode(data):
        time.sleep(3.9)
        return circle()

    logs2: list[str] = []
    rows_from_frames([frame()], room_id=ROOM, mm_per_px=MM_PER_PX, detector=DET,
                     fetch=fetch, decode=slow_decode, log=logs2.append)
    assert any("超门" in m for m in logs2), logs2
    assert any("超门(>3.75 s) 1 帧" in m for m in logs2), logs2
