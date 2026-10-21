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


def _gate(monkeypatch, *, blues, humid, budget=600, poll=60):
    clock = Clock()
    queue = list(blues)
    monkeypatch.setattr(patrol_gate, "blue_from_jpeg",
                        lambda blob, **kw: queue.pop(0) if queue else None)
    logs: list[str] = []
    gate = PatrolGate(probe_image=lambda: b"x", fetch_bytes=lambda url: b"x",
                      humid_probe=lambda: humid, budget_s=budget, poll_s=poll,
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


def test_budget_is_shared_between_pre_round_and_stations(monkeypatch):
    """起轮前忍满 30 min 后，轮中**不再重复等**（用户口径："最多等 30 min 总"）。"""
    gate, clock, _logs = _gate(monkeypatch, blues=[True] * 10, humid=False,
                               budget=120.0, poll=60.0)
    gate.wait_before_round()
    assert clock.t == 120.0
    gate.before_station(1, 3, SimpleNamespace(id="S01"), None)
    assert clock.t == 120.0


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
