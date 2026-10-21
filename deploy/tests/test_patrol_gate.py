from io import BytesIO
from types import SimpleNamespace

from deploy import patrol_gate
from deploy.patrol_gate import PatrolGate, blue_from_jpeg, build_gate
from PIL import Image


def _jpeg(color) -> bytes:
    buf = BytesIO()
    Image.new("RGB", (32, 32), color).save(buf, format="JPEG")
    return buf.getvalue()


def test_blue_from_jpeg_three_way():
    assert blue_from_jpeg(_jpeg((10, 12, 160))) is True
    assert blue_from_jpeg(_jpeg((100, 100, 100))) is False
    assert blue_from_jpeg(_jpeg((3, 3, 3))) is None        # 太黑 ⇒ 判不了
    assert blue_from_jpeg(b"not a jpeg") is None


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def sleep(self, seconds):
        self.t += seconds


def _gate(monkeypatch, *, blues, humid, budget=600, poll=60, max_blocked=2):
    clock = Clock()
    queue = list(blues)
    monkeypatch.setattr(patrol_gate, "blue_from_jpeg",
                        lambda blob, **kw: queue.pop(0) if queue else None)
    logs: list[str] = []
    gate = PatrolGate(probe_image=lambda: b"x", fetch_bytes=lambda url: b"x",
                      humid_probe=lambda: humid, budget_s=budget, poll_s=poll,
                      max_blocked_rounds=max_blocked,
                      sleep=clock.sleep, clock=clock, log=logs.append)
    return gate, clock, logs


def test_wait_before_round_returns_as_soon_as_blue_clears(monkeypatch):
    gate, clock, logs = _gate(monkeypatch, blues=[True, True, False], humid=False)
    gate.wait_before_round()
    assert clock.t == 120.0
    assert any("蓝光" in line for line in logs)


def test_wait_before_round_caps_at_budget(monkeypatch):
    gate, clock, _logs = _gate(monkeypatch, blues=[True] * 10, humid=False,
                               budget=120.0, poll=60.0)
    gate.wait_before_round()
    assert clock.t == 120.0          # 只等满预算，之后按口径开始


def test_humidifying_blocks_without_any_image(monkeypatch):
    gate, clock, logs = _gate(monkeypatch, blues=[], humid=True, budget=60.0, poll=60.0)
    gate.wait_before_round()
    assert clock.t == 60.0
    assert any("加湿" in line for line in logs)


def test_unknown_signals_fail_open(monkeypatch):
    gate, clock, _logs = _gate(monkeypatch, blues=[None], humid=None)
    gate.wait_before_round()
    assert clock.t == 0.0


def test_pre_round_and_mid_round_budgets_are_independent(monkeypatch):
    """起轮前忍满后，轮中**还有自己的一份**（首版共用 ⇒ 整轮等于没避让，票 23 §5-A）。"""
    gate, clock, _logs = _gate(monkeypatch, blues=[True] * 10, humid=False,
                               budget=120.0, poll=60.0)
    assert gate.wait_before_round() is True
    assert clock.t == 120.0
    gate.before_station(1, 3, SimpleNamespace(id="S01"), None)
    assert clock.t == 240.0          # 轮中又用掉一份独立的 120 s


def test_second_blocked_round_goes_home_without_waiting(monkeypatch):
    """连续 2 轮遇蓝光 ⇒ 第二轮**一秒都不等**，直接放弃本轮回原点。"""
    gate, clock, logs = _gate(monkeypatch, blues=[True] * 10, humid=False,
                              budget=120.0, poll=60.0)
    assert gate.wait_before_round() is True
    assert clock.t == 120.0
    assert gate.wait_before_round() is False
    assert clock.t == 120.0
    assert any("回原点" in line for line in logs)


def test_blocked_counter_clears_when_signal_goes_away(monkeypatch):
    gate, _clock, logs = _gate(monkeypatch, blues=[True] * 4 + [False], humid=False,
                               budget=120.0, poll=60.0)
    assert gate.wait_before_round() is True
    assert gate.wait_before_round() is False
    assert gate.blocked_rounds == 2
    assert gate.wait_before_round() is True
    assert gate.blocked_rounds == 0
    assert any("计数清零" in line for line in logs)


def test_mid_round_budget_exhausted_short_circuits(monkeypatch):
    """轮中预算用完 ⇒ 余下各站不再取帧、不再记「原地暂停」（票 23 §5-B）。"""
    gate, clock, logs = _gate(monkeypatch, blues=[True] * 6, humid=False,
                              budget=60.0, poll=60.0)
    gate.before_station(1, 3, SimpleNamespace(id="S01"), None)
    assert clock.t == 60.0
    settled = len(logs)
    calls: list[int] = []
    gate.probe_image = lambda: calls.append(1) or b"x"
    gate.fetch_bytes = lambda url: calls.append(1) or b"x"
    for i in (2, 3):
        gate.before_station(i, 3, SimpleNamespace(id=f"S0{i}"),
                            {"cloud_url": "http://x/1.jpg"})
    assert clock.t == 60.0
    assert calls == [], "预算用完后不该再抓拍/取帧"
    assert len(logs) == settled, "预算用完后不该每站再记两行"


def test_before_station_uses_previous_frame_and_pauses(monkeypatch):
    gate, clock, logs = _gate(monkeypatch, blues=[True, False], humid=False,
                              budget=300.0, poll=60.0)
    station = SimpleNamespace(id="S02")
    gate.before_station(2, 4, station, {"cloud_url": "http://x/1.jpg"})
    assert clock.t == 60.0
    assert any("原地暂停" in line for line in logs)


def test_build_gate_off_is_none():
    args = SimpleNamespace(gate="off")
    logs: list[str] = []
    assert build_gate(args, capture=None, room_id="611", log=logs.append) is None


def test_build_gate_on_wires_capture_and_api():
    class FakeCapture:
        def capture(self, **kwargs):
            assert kwargs["ip"] == "192.168.1.238"
            return SimpleNamespace(cloud_url="http://x/probe.jpg")

    args = SimpleNamespace(gate="on", gate_api="http://api:8000",
                           gate_budget_s=1800.0, gate_poll_s=60.0, ingest="")
    gate = build_gate(args, capture=FakeCapture(), room_id="611",
                      log=lambda m: None, fetch=lambda url: b"jpeg")
    assert gate is not None
    assert gate.probe_image() == b"jpeg"
