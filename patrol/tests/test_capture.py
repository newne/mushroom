from datetime import datetime

import pytest
from patrol.capture_client import (
    CAPTURE_URL,
    CaptureClient,
    CaptureError,
    DeviceOfflineError,
    LoginError,
)
from patrol.links import RetryingTransport, TransportError
from patrol.orchestrator import StationCapture
from patrol.stations import Station, image_object_name, load_stations, save_stations

# ---------- stations ----------

def test_station_roundtrip(tmp_path):
    stations = [
        Station(id="S01", box_id="B01", y=1200.0, z=-120.0),
        Station(id="S02", box_id="B02", y=1300.0, z=-120.0, camera_ip="192.168.1.231"),
    ]
    path = str(tmp_path / "stations.yaml")
    save_stations(stations, path)
    loaded = load_stations(path)
    assert loaded == stations


def test_station_yaml_spec_example(tmp_path):
    """站位 yaml 的字段必须与 spec §4.4 的示例逐字一致（y/z，不再是 x/y）。"""
    text = ("stations:\n"
            "  - id: S01\n    box_id: B01\n    y: 1200.0\n    z: -120.0\n"
            "    angle_profile: top45\n    camera_ip: 192.168.1.231\n")
    path = tmp_path / "s.yaml"
    path.write_text(text, encoding="utf-8")
    stations = load_stations(str(path))
    assert stations[0].y == 1200.0
    assert stations[0].z == -120.0
    assert stations[0].angle_profile == "top45"


def test_duplicate_station_ids_rejected(tmp_path):
    path = tmp_path / "s.yaml"
    path.write_text(
        "stations:\n  - {id: S01, box_id: B01, y: 1, z: -2}\n"
        "  - {id: S01, box_id: B02, y: 3, z: -4}\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="重复"):
        load_stations(str(path))


def test_object_name_format():
    st = Station(id="S01", box_id="B01", y=0, z=0, angle_profile="top0")
    ts = datetime(2026, 8, 30, 14, 5, 9)
    assert image_object_name(st, ts) == "20260830/B01_S01_top0_140509"


# ---------- capture client（links.Transport 形状：send(url, *, params, body)） ----------

def ok_transport(url: str, *, params=None, body=None) -> dict:
    assert url == CAPTURE_URL  # URL 字面量在库内，adapter 只接参数
    # 真实截图服务会把请求中的 filename（补 .jpg 后）回显在响应里
    return {"success": True, "message": "ok", "filename": params["filename"] + ".jpg",
            "cloud_url": "http://minio/bucket/" + params["filename"] + ".jpg"}


def test_capture_success():
    client = CaptureClient(transport=ok_transport)
    res = client.capture(ip="192.168.1.231", filename="20260830/B01_S01_top0_140509")
    assert res.object_name == "20260830/B01_S01_top0_140509.jpg"
    assert res.cloud_url.endswith(".jpg")


def test_capture_without_transport_raises_not_implemented():
    with pytest.raises(NotImplementedError):
        CaptureClient().capture(ip="1.2.3.4", filename="f")


def test_capture_login_error():
    def transport(url, *, params=None, body=None) -> dict:
        return {"success": False, "message": "Device login failed",
                "error_code": -1, "detail": "用户名或密码错误"}

    with pytest.raises(LoginError):
        CaptureClient(transport=transport).capture(ip="1.2.3.4", filename="f")


def test_capture_device_offline():
    def transport(url, *, params=None, body=None) -> dict:
        return {"success": False, "message": "Device login failed",
                "error_code": -2, "detail": "设备不在线"}

    with pytest.raises(DeviceOfflineError):
        CaptureClient(transport=transport).capture(ip="1.2.3.4", filename="f")


def test_capture_generic_error():
    def transport(url, *, params=None, body=None) -> dict:
        return {"success": False, "message": "Failed to capture screenshot",
                "error_code": -1239510}

    with pytest.raises(CaptureError) as ei:
        CaptureClient(transport=transport).capture(ip="1.2.3.4", filename="f")
    assert "登录" not in str(ei.value)


# ---------- links：RetryingTransport ----------

class FlakyTransport:
    def __init__(self, fail_times: int):
        self.fail_times = fail_times
        self.calls = 0

    def __call__(self, url, *, params=None, body=None) -> dict:
        self.calls += 1
        if self.calls <= self.fail_times:
            raise TransportError("网络抖动")
        return {"ok": True}


def test_retrying_transport_retries_then_succeeds():
    sleeps: list[float] = []
    t = RetryingTransport(FlakyTransport(2), attempts=3, backoff_s=0.5, sleep=sleeps.append)
    assert t("http://x") == {"ok": True}
    assert sleeps == [0.5, 0.5]


def test_retrying_transport_gives_up():
    sleeps: list[float] = []
    t = RetryingTransport(FlakyTransport(99), attempts=2, backoff_s=0.0, sleep=sleeps.append)
    with pytest.raises(TransportError):
        t("http://x")


# ---------- orchestrator ----------

class ScriptedFmc:
    """最小假 FMC：记录 lamp 与 goto 序列，运动即停。"""

    def __init__(self):
        self.events: list[tuple] = []

    def goto(self, y, z, **kw):
        self.events.append(("goto", y, z))

    def wait_stop(self, axes=None, **kw):
        return True

    def lamp(self, on, *, io=0):
        self.events.append(("lamp", on, io))


def test_capture_station_sequence_and_metadata():
    fmc = ScriptedFmc()
    seen_params: list[dict] = []

    def transport(url, *, params=None, body=None) -> dict:
        seen_params.append(params)
        return ok_transport(url, params=params)

    st = Station(id="S01", box_id="B01", y=10.0, z=-20.0,
                 camera_ip="192.168.1.231", camera_user="admin", camera_pwd="p")
    sc = StationCapture(fmc, CaptureClient(transport=transport),
                        decay_s=0.0, settle_s=0.0, sleep=lambda s: None)
    meta = sc.run(st, ts=datetime(2026, 8, 30, 8, 0, 0))

    assert fmc.events == [("goto", 10.0, -20.0), ("lamp", True, 0), ("lamp", False, 0)]
    assert meta["object_name"] == "20260830/B01_S01_top45_080000.jpg"
    assert meta["yz"] == [10.0, -20.0]
    assert seen_params[0]["ip"] == "192.168.1.231"
    assert meta["cloud_url"].endswith(".jpg")


def test_capture_station_lamp_off_on_failure():
    fmc = ScriptedFmc()

    def transport(url, *, params=None, body=None) -> dict:
        return {"success": False, "message": "Failed to capture screenshot",
                "error_code": -1}

    st = Station(id="S01", box_id="B01", y=0, z=0, camera_ip="1.2.3.4")
    sc = StationCapture(fmc, CaptureClient(transport=transport),
                        decay_s=0.0, settle_s=0.0, retries=1, sleep=lambda s: None)
    with pytest.raises(CaptureError):
        sc.run(st, ts=datetime(2026, 8, 30, 8, 0, 0))
    # 失败后灯必须已关（时序末尾）
    assert fmc.events[-1] == ("lamp", False, 0)


def test_capture_station_retry_uses_new_object_name():
    fmc = ScriptedFmc()
    names: list[str] = []

    def transport(url, *, params=None, body=None) -> dict:
        names.append(params["filename"])
        if len(names) == 1:
            return {"success": False, "message": "Failed to capture screenshot",
                    "error_code": -1}
        return ok_transport(url, params=params)

    st = Station(id="S01", box_id="B01", y=0, z=0, camera_ip="1.2.3.4")
    sc = StationCapture(fmc, CaptureClient(transport=transport),
                        decay_s=0.0, settle_s=0.0, retries=1, sleep=lambda s: None)
    sc.run(st, ts=datetime(2026, 8, 30, 8, 0, 0))
    assert names[0] != names[1]


def test_transport_error_becomes_capture_error_so_the_round_keeps_going():
    """链路错误必须落进 CaptureError 体系。

    实测踩过：池化采图偶发 500 → transport 抛 TransportError → 它**不是** CaptureError，
    于是穿透 orchestrator 的站位级重试与 PatrolRound 的"跳过该站位"，一次抖动直接打断
    整轮 60 站位巡检（`--once` 退出码 1、outbox 空）。
    """

    def boom(url, *, params=None, body=None):
        raise TransportError("connection refused")

    with pytest.raises(CaptureError, match="链路失败"):
        CaptureClient(transport=boom).capture(ip="1.2.3.4", filename="x")


def test_station_retry_recovers_from_a_transient_link_error():
    """站位级重试要能覆盖链路错误：第一次链路失败，第二次成功。

    这条与上一条配对——只有 TransportError 被折算成 CaptureError，重试才有机会发生。
    """
    fmc = ScriptedFmc()
    calls = {"n": 0}

    def flaky(url, *, params=None, body=None) -> dict:
        calls["n"] += 1
        if calls["n"] == 1:
            raise TransportError("connection refused")
        return ok_transport(url, params=params)

    st = Station(id="S01", box_id="B01", y=0.0, z=0.0, camera_ip="1.2.3.4")
    sc = StationCapture(fmc, CaptureClient(transport=flaky),
                        decay_s=0.0, settle_s=0.0, retries=1, sleep=lambda s: None)
    meta = sc.run(st, ts=datetime(2026, 8, 30, 8, 0, 0))
    assert calls["n"] == 2
    assert meta["station_id"] == "S01"


def test_station_retry_recovers_from_a_business_failure_envelope():
    """采图服务的"业务失败信封"（500 + ``success:false``）也要能触发重试。

    真实的池化失效就是这一种：连接缓存过期 → 服务返回 500 信封 → 重试时
    ``get_or_create`` 重建连接 → 成功。
    """
    calls = {"n": 0}

    def envelope_then_ok(url, *, params=None, body=None) -> dict:
        calls["n"] += 1
        if calls["n"] == 1:
            return {"success": False, "message": "Failed to capture screenshot",
                    "error_code": -1239510}
        return ok_transport(url, params=params)

    st = Station(id="S01", box_id="B01", y=0.0, z=0.0, camera_ip="1.2.3.4")
    sc = StationCapture(ScriptedFmc(), CaptureClient(transport=envelope_then_ok),
                        decay_s=0.0, settle_s=0.0, retries=1, sleep=lambda s: None)
    sc.run(st, ts=datetime(2026, 8, 30, 8, 0, 0))
    assert calls["n"] == 2
