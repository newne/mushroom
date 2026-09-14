from fmc_fakes import FakeFmcLib
from patrol.capture_client import CaptureError
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
    def __init__(self):
        self.events: list[tuple] = []

    def home_all(self):
        self.events.append(("home",))

    def goto(self, y, z, **kw):
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
    assert fmc.events[-1] == ("goto", 0.0, 0.0)   # 以回原位结束
    assert report.ended_at is not None


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
