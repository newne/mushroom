"""`StationCapture` 的图像微调接线：trim 叠加、试探帧命名、兜底与灯的收尾。

闭环本身的逻辑在 `test_framing.py` 里测；这里测的是**接线**——它有没有真的去挪、
挪的是不是叠加了 trim 的位置、交出去的图是不是好位置那张、拿不到像素时会不会
把这一站搞成失败。
"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import pytest
from patrol.capture_client import CaptureError
from patrol.framing import FramingError, FramingRecipe
from patrol.orchestrator import FramingHook, StationCapture
from patrol.stations import Station

TS = datetime(2026, 9, 13, 20, 25, 42)
LIMITS = (0.0, 4492.0, 0.0, 212.0)


class FakeFmc:
    def __init__(self):
        self.pos = (0.0, 0.0)
        self.moves: list[tuple[float, float]] = []
        self.lamp_states: list[bool] = []

    def goto(self, y, z, **kw):
        self.pos = (y, z)
        self.moves.append((y, z))

    def lamp(self, on, *, io=0):
        self.lamp_states.append(bool(on))


class FakeCapture:
    """采图：对象名就是传进来的 filename（截图服务会自动补 .jpg，这里照做）。"""

    def __init__(self, *, fail_times: int = 0):
        self.filenames: list[str] = []
        self.fail_times = fail_times

    def capture(self, *, ip, user, pwd, filename):
        self.filenames.append(filename)
        if self.fail_times > 0:
            self.fail_times -= 1
            raise CaptureError("camera busy")
        return SimpleNamespace(object_name=filename + ".jpg",
                               cloud_url="http://minio/mogu/" + filename + ".jpg")


def hook_for(fmc: FakeFmc, *, target: tuple[float, float], scale=1.0, tol=2.0):
    """像素里编码"当前坐标"，评估器据此算出目标还差多少（1px = 1mm）。

    **符号约定**（`FramingError` 的文档口径，别弄反）：
    * ``dx_px > 0`` = 目标偏画面右 ⇒ Y 增大方向（Y 向右为正）；
    * ``dy_px > 0`` = 目标偏画面**下** ⇒ Z **增大**方向（Z 向下为正，ADR-0018）。
    所以纵向是 ``(target_z - z)``（Z 增大 = 向下，ADR-0018）。
    """
    recipe = FramingRecipe(mm_per_px_y=scale, mm_per_px_z=scale, sign_y=1.0, sign_z=1.0,
                           tol_px=tol, max_step_mm=50.0, max_taps=3)

    def pixels(result) -> bytes:
        y, z = fmc.pos
        return f"{y},{z}".encode()

    def evaluate(pixels_bytes: bytes) -> FramingError:
        y, z = (float(v) for v in pixels_bytes.decode().split(","))
        # Z 增大 = 向下（ADR-0018）：目标在相机下方 ⇒ target_z > z ⇒ dy_px > 0
        return FramingError(dx_px=(target[0] - y) / scale, dy_px=(target[1] - z) / scale,
                            confidence=1.0)

    return FramingHook(recipe=recipe, evaluate=evaluate, pixels=pixels)


def station(**kw) -> Station:
    base = {"id": "S101", "box_id": "B101", "y": 187.167, "z": 21.2, "layer": 1, "col": 1}
    base.update(kw)
    return Station(**base)


def make_capture(fmc, capture, *, framing=None, retries=0, logs=None, limits=LIMITS):
    return StationCapture(fmc, capture, framing=framing, retries=retries,
                          framing_limits=limits,
                          log=(logs.append if logs is not None else (lambda _m: None)),
                          sleep=lambda _s: None)


# ---------- 没有微调钩子时行为不变 ----------


def test_without_framing_it_is_one_capture_at_the_station_coordinates():
    fmc, cap = FakeFmc(), FakeCapture()
    meta = make_capture(fmc, cap).run(station(), ts=TS)

    assert fmc.moves == [(187.167, 21.2)]
    assert len(cap.filenames) == 1
    assert meta["yz"] == [187.167, 21.2]
    assert "framing" not in meta
    assert fmc.lamp_states == [True, False], "补光窗口必须闭合"


def test_learned_trim_is_added_to_the_station_coordinates():
    """trim 是"上次学到的偏移"，出发位置要叠加它——否则微调等于白学。"""
    fmc, cap = FakeFmc(), FakeCapture()
    # trim 跟着坐标框架一起镜像：Z 旧的 -3.0 在新框架里是 +3.0（ADR-0018）
    meta = make_capture(fmc, cap).run(station(trim_y=12.5, trim_z=3.0), ts=TS)

    assert fmc.moves == [(187.167 + 12.5, 21.2 + 3.0)]
    assert meta["yz"] == [199.667, 24.2]


# ---------- 有微调钩子 ----------


def test_framing_moves_to_the_target_and_hands_back_that_frame():
    fmc, cap = FakeFmc(), FakeCapture()
    nominal = (187.167, 21.2)
    target = (nominal[0] + 20.0, nominal[1] + 10.0)   # 目标在基准的**下方**：Z 增大 = 向下

    meta = make_capture(fmc, cap, framing=hook_for(fmc, target=target)).run(station(), ts=TS)

    assert fmc.moves[0] == nominal, "第一步永远是走到基准位置"
    assert fmc.moves[-1] == pytest.approx(target), "微调应当把相机挪到目标位置"
    assert meta["yz"] == [round(target[0], 3), round(target[1], 3)]
    assert meta["framing"]["converged"] is True
    assert meta["framing"]["trim"] == pytest.approx([20.0, 10.0], abs=1e-3)
    assert meta["framing"]["suggested_trim"] == pytest.approx([20.0, 10.0], abs=1e-3)
    assert meta["framing"]["base_yz"] == [nominal[0], nominal[1]]
    # 交出去的是**好位置那张**（试探帧带 -probe 后缀）
    assert meta["object_name"].endswith("-probe1.jpg")


def test_suggested_trim_accumulates_on_top_of_the_existing_one():
    fmc, cap = FakeFmc(), FakeCapture()
    start = (187.167 + 10.0, 21.2)
    target = (start[0] + 6.0, start[1] + 4.0)

    meta = make_capture(fmc, cap, framing=hook_for(fmc, target=target)) \
        .run(station(trim_y=10.0), ts=TS)

    assert meta["framing"]["suggested_trim"] == pytest.approx([16.0, 4.0], abs=1e-3)


def test_unknown_target_keeps_the_baseline_frame(tmp_path=None):
    """检测器找不到目标：一步不挪，而且这一站仍然有图（基准那张）。"""
    fmc, cap = FakeFmc(), FakeCapture()

    def evaluate(_pixels):
        return FramingError(found=False, reason="太暗")

    hook = FramingHook(recipe=FramingRecipe(mm_per_px_y=1.0, mm_per_px_z=1.0),
                       evaluate=evaluate, pixels=lambda _r: b"x")
    meta = make_capture(fmc, cap, framing=hook).run(station(), ts=TS)

    assert fmc.moves == [(187.167, 21.2)], "只在基准位置拍，不该有第二步"
    assert meta["framing"]["converged"] is False
    assert meta["framing"]["trim"] == [0.0, 0.0]
    assert meta["object_name"].endswith(".jpg")
    assert "-probe" not in meta["object_name"], "基准帧就是普通命名"
    assert "太暗" in meta["framing"]["reason"]


def test_pixels_unavailable_degrades_to_a_plain_capture():
    """取不到像素只是"没法微调"，不该把这一站判成失败——图已经在 MinIO 里了。"""
    fmc, cap = FakeFmc(), FakeCapture()
    logs: list[str] = []

    def pixels(_result):
        raise RuntimeError("minio 连不上")

    hook = FramingHook(recipe=FramingRecipe(mm_per_px_y=1.0, mm_per_px_z=1.0),
                       evaluate=lambda _p: FramingError(), pixels=pixels)
    meta = make_capture(fmc, cap, framing=hook, logs=logs).run(station(), ts=TS)

    assert meta["ok"] is True
    assert fmc.moves == [(187.167, 21.2)]
    assert any("放弃微调" in m for m in logs)
    assert meta["framing"]["converged"] is False


def test_capture_failure_still_turns_the_lamp_off():
    fmc, cap = FakeFmc(), FakeCapture(fail_times=99)
    with pytest.raises(CaptureError):
        make_capture(fmc, cap, framing=hook_for(fmc, target=(0.0, 0.0))).run(station(), ts=TS)
    assert fmc.lamp_states == [True, False]


def test_retry_moves_the_object_name_forward():
    fmc, cap = FakeFmc(), FakeCapture(fail_times=1)
    meta = make_capture(fmc, cap, retries=1).run(station(), ts=TS)
    assert len(cap.filenames) == 2
    assert cap.filenames[0] != cap.filenames[1], "重试不能覆盖上一张"
    assert meta["ok"] is True
