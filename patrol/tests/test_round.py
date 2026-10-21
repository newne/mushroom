from dataclasses import replace

import pytest
from fmc_fakes import FakeFmcLib
from patrol.capture_client import CaptureError
from patrol.fmc import MotionAborted
from patrol.round import PatrolRound
from patrol.stations import Station, record_station, upsert_station


class FakeStationCapture:
    """按 box_id 决定成败的假采图服务（注入 PatrolRound 的内部缝）。"""

    def __init__(self, fail_box_ids=()):
        self.fail_box_ids = set(fail_box_ids)
        self.ran: list[str] = []

    def run(self, station, ts=None):
        self.ran.append(station.id)
        if station.box_id in self.fail_box_ids:
            raise CaptureError(f"采图失败: {station.box_id}")
        return {"station_id": station.id, "box_id": station.box_id}


class FakeFmc:
    """记录调用的假控制器；`abort` 为真时像真客户端一样抛 MotionAborted。"""

    def __init__(self):
        self.events: list[tuple] = []
        self.abort = None            # 由 PatrolRound 透传进来（被测的就是这个透传）

    def home_all(self, **kw):
        self.abort = kw.get("abort")
        if self.abort is not None and self.abort():
            raise MotionAborted("测试：回零前已急停")
        self.events.append(("home",))

    def goto(self, y, z, **kw):
        self.abort = kw.get("abort")
        if self.abort is not None and self.abort():
            raise MotionAborted("测试：移动前已急停")
        self.events.append(("goto", y, z))

    def wait_stop(self, axes=None, **kw):
        return True


def make_stations(n=4):
    return [Station(id=f"S{i:02d}", box_id=f"B{i:02d}", y=float(i * 100), z=0.0)
            for i in range(1, n + 1)]


def make_round(stations, cap):
    return PatrolRound(FakeFmc(), capture_client=None, stations=stations,
                       station_capture=cap)


def test_round_success_home_first_and_last():
    cap = FakeStationCapture()
    fmc = FakeFmc()
    report = PatrolRound(fmc, None, make_stations(3), station_capture=cap).run()
    assert len(report.results) == 3
    assert report.failures == []
    assert not report.aborted
    assert report.ok
    assert fmc.events[0] == ("home",)             # 生命周期从回零开始
    # 收尾是"回原位 → 停在第一个格点"：最后一条 goto 是格点，不是原点
    assert fmc.events[-2] == ("goto", 0.0, 0.0)
    assert fmc.events[-1] == ("goto", 100.0, 0.0)   # 第一个站位（含 trim）
    assert report.ended_at is not None


def test_round_parks_at_the_first_station_so_the_preview_shows_mushrooms():
    """回原位的落点是导轨尽头，画面里只有货架端头与顶棚；停在第一个格点才有蘑菇看。

    现场口径（2026-10-13）：巡检完成回零后移动到第一个格点位置，打开画面即可直观
    看到蘑菇生长情况。
    """
    stations = [replace(s, trim_y=1.5, trim_z=-0.5) for s in make_stations(3)]
    fmc = FakeFmc()
    PatrolRound(fmc, None, stations, station_capture=FakeStationCapture()).run()
    assert fmc.events[-1] == ("goto", 101.5, -0.5), "停在第一个格点要用**含 trim** 的坐标"


def test_round_stops_early_on_an_avoid_signal_and_says_why():
    """「整轮避让」：保持生效时本轮就地停下——已拍到的帧留下，后面站位不再动机构。

    2026-10-13 分工：判定在部署侧（读 hold.json），执行在这里。
    收尾仍然回原位，但不"停到第一个格点"（那要求本轮是正常跑完）。
    """
    cap = FakeStationCapture()
    fmc = FakeFmc()
    seen = {"n": 0}

    def should_stop():
        seen["n"] += 1
        return "加湿保持至 2026-10-13T17:00:00" if seen["n"] >= 3 else None

    report = PatrolRound(fmc, None, make_stations(4), station_capture=cap,
                         should_stop=should_stop).run()

    assert cap.ran == ["S01", "S02"], "第 3 站之前收到信号 ⇒ 只拍了两站"
    assert len(report.results) == 2
    assert report.aborted is True and not report.ok
    assert "加湿保持" in report.abort_reason
    assert fmc.events[-1] == ("goto", 0.0, 0.0), "避让后仍要安全回原位"
    assert ("goto", 100.0, 0.0) not in fmc.events, "中止的一轮不该再停到格点"


def test_round_reports_the_park_so_the_console_knows_where_it_is():
    """停车是一次性事实，单独一个回调——不能塞进逐站心跳（那会被数进"已过站"）。"""
    seen = []
    fmc = FakeFmc()
    PatrolRound(fmc, None, make_stations(3), station_capture=FakeStationCapture(),
                on_park=lambda **kw: seen.append(kw)).run()
    assert seen == [{"station_id": "S01", "box_id": "B01", "y": 100.0, "z": 0.0}]


def test_round_does_not_report_a_park_when_aborted():
    seen = []
    cap = FakeStationCapture(fail_box_ids={"B02", "B03", "B04"})
    PatrolRound(FakeFmc(), None, make_stations(4), station_capture=cap,
                max_consecutive_failures=3,
                on_park=lambda **kw: seen.append(kw)).run()
    assert seen == []


def test_round_park_can_be_switched_off():
    fmc = FakeFmc()
    PatrolRound(fmc, None, make_stations(2), station_capture=FakeStationCapture(),
                park_at_first=False).run()
    assert fmc.events[-1] == ("goto", 0.0, 0.0), "关掉就该老实停在原点"


def test_round_does_not_park_when_the_round_was_aborted():
    """中途急停/运动失败后机器状态不明，不该再自作主张多动一次。"""
    cap = FakeStationCapture(fail_box_ids={"B02", "B03", "B04"})
    fmc = FakeFmc()
    report = PatrolRound(fmc, None, make_stations(4), station_capture=cap,
                         max_consecutive_failures=3).run()
    assert report.aborted
    assert fmc.events[-1] == ("goto", 0.0, 0.0), "中止的轮次只回原位，不停格点"


def test_round_skips_failing_station():
    cap = FakeStationCapture(fail_box_ids={"B02"})
    report = make_round(make_stations(4), cap).run()
    assert [r["station_id"] for r in report.results] == ["S01", "S03", "S04"]
    assert len(report.failures) == 1
    assert report.failures[0]["box_id"] == "B02"
    assert not report.aborted
    assert cap.ran == ["S01", "S02", "S03", "S04"]  # 失败不中断后续


def test_round_aborts_after_consecutive_failures():
    cap = FakeStationCapture(fail_box_ids={"B02", "B03", "B04"})
    fmc = FakeFmc()
    report = PatrolRound(fmc, None, make_stations(4), station_capture=cap,
                         max_consecutive_failures=3).run()
    assert report.aborted
    assert [r["station_id"] for r in report.results] == ["S01"]
    assert [f["station_id"] for f in report.failures] == ["S02", "S03", "S04"]
    assert fmc.events[-1] == ("goto", 0.0, 0.0)   # 中止后仍回原位


def test_round_no_return_home_option():
    fmc = FakeFmc()
    report = PatrolRound(fmc, None, make_stations(2),
                         station_capture=FakeStationCapture(),
                         return_home=False).run()
    assert not report.aborted
    assert all(e[0] != "goto" for e in fmc.events)


# ---------- 急停请求：能打断正在跑的那一轮 ----------


class EstopStationCapture(FakeStationCapture):
    """在某一站的采图闭环里冒出 MotionAborted —— 站内运动被急停打断的样子。"""

    def __init__(self, station_id: str):
        super().__init__()
        self.station_id = station_id

    def run(self, station, ts=None):
        if station.id == self.station_id:
            self.ran.append(station.id)
            raise MotionAborted(f"测试：{station.id} 运动中按了急停")
        return super().run(station, ts)


def test_estop_mid_round_is_reported_separately_and_does_not_go_home():
    """急停打断一轮：抛 MotionAborted、心跳标 estop、**不回原位**。

    为什么必须单独测：回原位是每轮的正常收尾，而"按下急停之后机器又走了一段"是现场
    最难解释的行为之一。abort 在下发前就拦住 goto，所以这里连一条 goto 都不该有。
    """
    fmc = FakeFmc()
    heartbeats: list[dict] = []
    round_ = PatrolRound(
        fmc, None, make_stations(3), station_capture=EstopStationCapture("S02"),
        on_station=lambda i, n, **f: heartbeats.append(f),
    )
    with pytest.raises(MotionAborted):
        round_.run()

    assert [e[0] for e in fmc.events] == ["home"]        # 只有开场回零，没有回原位
    assert heartbeats[-1]["station_id"] == "S02"
    assert heartbeats[-1]["abort"] == "estop"            # 与"运动失败"分开，别让人去查导轨


def test_estop_already_raised_stops_before_home():
    """急停已置位时连回零都不做（第一件事就被拦下）——不给自己"先动一下"的机会。"""
    fmc = FakeFmc()
    with pytest.raises(MotionAborted):
        PatrolRound(fmc, None, make_stations(2), station_capture=FakeStationCapture(),
                    abort=lambda: True).run()
    assert fmc.events == []
    assert fmc.abort is not None        # 回调确实透到了控制器那一层


# ---------- 示教（并入 stations） ----------

def test_record_station_uses_current_position():
    lib = FakeFmcLib()
    lib.pos = (0.0, 1234.5, -87.0)  # X 未接线, Y=1234.5, Z=-87

    from patrol.fmc import Fmc4030

    st = record_station(Fmc4030(lib), "S07", "B07", camera_ip="192.168.1.231")
    assert (st.y, st.z) == (1234.5, -87.0)
    assert st.id == "S07" and st.box_id == "B07"


def test_upsert_station_updates_in_place():
    stations = make_stations(3)
    updated = Station(id="S02", box_id="B02", y=999.0, z=-1.0)
    result = upsert_station(stations, updated)
    assert result[1] is updated
    assert result[0].id == "S01"


def test_upsert_station_appends_new():
    stations = make_stations(2)
    upsert_station(stations, Station(id="S09", box_id="B09", y=1.0, z=-2.0))
    assert [s.id for s in stations] == ["S01", "S02", "S09"]


def test_before_station_sees_previous_meta_and_does_not_abort():
    """原地暂停钩子：每站拍之前调一次，带上一站的 meta；它不改本轮结论。"""
    cap = FakeStationCapture()
    seen: list[tuple] = []

    def before_station(index, total, station, prev):
        seen.append((index, total, station.id,
                     None if prev is None else prev["station_id"]))

    report = PatrolRound(FakeFmc(), None, make_stations(3), station_capture=cap,
                         before_station=before_station).run()
    assert report.ok and not report.aborted
    assert seen == [(1, 3, "S01", None), (2, 3, "S02", "S01"), (3, 3, "S03", "S02")]
