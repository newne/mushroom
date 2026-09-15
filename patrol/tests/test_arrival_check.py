"""到位校验：把 2026-09-13 实测到的「移动未按指令完成」钉成回归。

## 实测缺陷

    指令 Y 走 4442 mm，而**控制器自己的计数**最终是 1435 mm —— running 位正常清零、
    "已停"正常上报、**没有任何错误码**。不主动比"指令 vs 计数"，这件事完全不可见。
    20 趟里有 2 趟（偏差 3006 mm 与 991 mm）。

**口径说明（别把断言写成物理结论）**：FMC4030 **无位置反馈**，`real_pos` 是脉冲自述。
所以本文件断言的是"计数器与指令不符"，**不是**"滑块停在 1435"。没有反馈就分不清
"控制器提前放弃"与"滑块被卡住"，本文件不替这件事下结论。

后果比"少拍一帧"严重：`StationCapture` 相信 `goto` 已完成，于是相机可能在**错误的
位置**拍图，而元数据照写该站位的编号——错误数据静默进入测量与生长曲线。

## 另两条实测事实（决定处置方式）

1. 从异常点再做一次绝对移动，落点与指令一致（−0.000 mm）⇒ 坐标系可继续使用，
   处置应是"跳过该站"，不必重新回零。**这不等于"没有丢步"**——只说明计数器自洽。
2. 正常落点 0.000–0.019 mm；容差 0.5 mm 是"抓大偏差"的工程取值。
"""

from __future__ import annotations

import pytest
from fmc_fakes import FakeFmcLib
from patrol.fmc import Fmc4030, TravelShortfallError
from patrol.fmc.client import ARRIVAL_TOL_MM


def make(stop_early_at: float | None = None) -> Fmc4030:
    """假库：`stop_early_at` 给定时，插补只走一部分（模拟"计数器与指令不符"）。"""
    lib = FakeFmcLib()
    lib.stop_early_at = stop_early_at
    return Fmc4030.connect(lib=lib)


# ---------- 假库扩展：让插补只走一部分（计数器随之停在半路） ----------


class ShortStoppingLib(FakeFmcLib):
    """`Line_2Axis` / 绝对 `Jog` 只走到半路就"到位"，且不报错——复刻实测行为。"""

    def __init__(self, *, stop_at_ratio: float = 0.32):
        super().__init__()
        self.stop_at_ratio = stop_at_ratio

    def FMC4030_Line_2Axis(self, dev_id, axis, end_x, end_y, speed, acc, dec):
        self.calls.append(("line2", (dev_id, axis, end_x, end_y, speed, acc, dec)))
        slots = [i for i in range(3) if axis & (1 << i)]
        p = list(self.pos)
        for slot, value in zip(slots, (end_x, end_y), strict=True):
            start = p[slot]
            p[slot] = start + (value - start) * self.stop_at_ratio   # ← 只走了 32%
        self.pos = tuple(p)
        return 0

    def FMC4030_Jog_Single_Axis(self, dev_id, axis, pos, speed, acc, dec, mode):
        self.calls.append(("jog", (dev_id, axis, pos, speed, acc, dec, mode)))
        if mode == 2:
            start = self.pos[axis]
            new = start + (pos - start) * self.stop_at_ratio
        else:
            new = self.pos[axis] + pos * self.stop_at_ratio
        p = list(self.pos)
        p[axis] = new
        self.pos = tuple(p)
        return 0


def short_stopping(*, ratio: float = 0.32) -> Fmc4030:
    return Fmc4030.connect(lib=ShortStoppingLib(stop_at_ratio=ratio))


# ---------- 抓得住 ----------


def test_goto_raises_on_silent_shortfall():
    """实测那一幕：指令 4442，控制器计数约 1435，running 正常清零、无错误码。

    `goto` 分两段（巡检段走空程、接近段走最后 5mm），异常发生在第一段，
    所以报出来的 target 是接近点（4442−5）而不是终点——这本身就是现场要看到的信息。
    """
    fmc = short_stopping(ratio=1435.173 / 4442.0)
    with pytest.raises(TravelShortfallError) as ei:
        fmc.goto(4442.0, 21.2)
    err = ei.value
    assert err.axis_name == "Y"
    assert err.actual < err.target, "必须是『没走到』而不是『走过了』"
    assert err.shortfall > 100.0, f"偏差应远大于容差，实测 {err.shortfall}"
    assert "未执行完成" in str(err)


def test_shortfall_message_names_target_and_actual():
    """现场要能一眼看出差多少——消息里必须有目标、实际、差值与容差。"""
    fmc = short_stopping(ratio=0.32)
    with pytest.raises(TravelShortfallError) as ei:
        fmc.goto(1000.0, 21.2)
    msg = str(ei.value)
    assert "指令" in msg and "控制器计数器报" in msg and "容差" in msg
    assert "无位置反馈" in msg, "消息里必须写明这是计数口径，不是实测位置"


def test_goto_shortfall_reports_which_axis():
    """Z 出问题时要点名 Z——否则现场不知道该查哪根轴。"""
    fmc = short_stopping(ratio=0.0)
    with pytest.raises(TravelShortfallError) as ei:
        fmc.goto(0.0, 190.8)
    assert ei.value.axis_name in {"Y", "Z"}


class LandingOffLib(FakeFmcLib):
    """每段插补都少走固定的 `short_mm`——用来精确试容差边界。"""

    def __init__(self, *, short_mm: float):
        super().__init__()
        self.short_mm = short_mm

    def FMC4030_Line_2Axis(self, dev_id, axis, end_x, end_y, speed, acc, dec):
        self.calls.append(("line2", (dev_id, axis, end_x, end_y, speed, acc, dec)))
        slots = [i for i in range(3) if axis & (1 << i)]
        p = list(self.pos)
        for slot, value in zip(slots, (end_x, end_y), strict=True):
            step = self.short_mm if value > p[slot] else -self.short_mm
            p[slot] = value - step
        self.pos = tuple(p)
        return 0


def landing_off(short_mm: float) -> Fmc4030:
    return Fmc4030.connect(lib=LandingOffLib(short_mm=short_mm))


def test_goto_is_fine_within_tolerance():
    """容差之内不报：实测落点噪声 0.000–0.019 mm，不能把它当故障。"""
    fmc = landing_off(ARRIVAL_TOL_MM * 0.5)
    fmc.goto(100.0, 21.2)          # 不抛
    assert abs(fmc.current_yz()[0] - 100.0) <= ARRIVAL_TOL_MM


def test_goto_just_outside_tolerance_raises():
    fmc = landing_off(ARRIVAL_TOL_MM * 4)
    with pytest.raises(TravelShortfallError):
        fmc.goto(100.0, 21.2)


def test_move_axis_also_verifies_arrival():
    """单轴绝对定位同样要校验——M0 示教与 console 走的是这条路径。"""
    fmc = short_stopping(ratio=0.3)
    with pytest.raises(TravelShortfallError) as ei:
        fmc.move_axis(1, 1000.0)
    assert ei.value.axis_name == "Y"
    assert ei.value.target == 1000.0
    assert ei.value.actual < 1000.0


def test_goto_2axis_also_verifies_arrival():
    """M0 单段直达路径（现场调试台用的就是它）。"""
    fmc = short_stopping(ratio=0.25)
    with pytest.raises(TravelShortfallError):
        fmc.goto_2axis(2000.0, 21.2)


# ---------- 不改正常行为 ----------


def test_healthy_motion_still_passes():
    fmc = make()
    fmc.goto(1200.0, 21.2)
    assert fmc.current_yz() == pytest.approx((1200.0, 21.2))
    fmc.move_axis(1, 500.0)
    fmc.goto_2axis(300.0, 21.2)
    assert fmc.current_yz() == pytest.approx((300.0, 21.2))


def test_shortfall_is_an_fmc_error_for_callers_that_catch_broadly():
    """`TravelShortfallError` 必须是 FmcError 子类：既有调用方按 FmcError 分类，
    否则新错误会穿透它们的兜底逻辑。"""
    from patrol.fmc import FmcError

    assert issubclass(TravelShortfallError, FmcError)
    assert issubclass(TravelShortfallError, RuntimeError)


# ---------- 巡检路径的处置：跳过该站，不中止整轮 ----------


def test_round_skips_the_station_and_keeps_going(tmp_path):
    """移动未完成 ⇒ 该站记失败并**继续下一站**（同采图失败的处置）。

    依据：回零能把坐标系拉回硬限位（落点稳定 0.000），且异常后的下一趟通常正常，
    所以后续站位的绝对定位仍然有效，没有理由废掉整轮。
    """
    from patrol.capture_client import CaptureResult
    from patrol.round import PatrolRound
    from patrol.stations import Station

    lib = ShortStoppingLib(stop_at_ratio=0.3)
    fmc = Fmc4030.connect(lib=lib)

    class OkCapture:
        def capture(self, *, ip, user="admin", pwd="", filename, storage="cloud"):
            return CaptureResult(filename + ".jpg", None, None, {})

    # 第一站必失败；把假库切回正常，验证后面几站照跑
    class FirstOneShort(ShortStoppingLib):
        def __init__(self):
            super().__init__(stop_at_ratio=0.3)
            self.n = 0

        def FMC4030_Line_2Axis(self, dev_id, axis, end_x, end_y, speed, acc, dec):
            self.n += 1
            if self.n > 1:
                self.stop_at_ratio = 1.0
            return ShortStoppingLib.FMC4030_Line_2Axis(
                self, dev_id, axis, end_x, end_y, speed, acc, dec
            )

    fmc = Fmc4030.connect(lib=FirstOneShort())
    stations = [Station(id=f"S10{i}", box_id=f"B10{i}", y=100.0 * i, z=21.2,
                        camera_ip="192.168.1.238") for i in range(1, 4)]
    report = PatrolRound(fmc, OkCapture(), stations, return_home=False).run()

    assert len(report.failures) == 1, "只有第一站该失败"
    assert "未执行完成" in report.failures[0]["error"]
    assert len(report.results) == 2, "后面两站要继续跑"
