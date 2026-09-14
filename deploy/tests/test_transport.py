"""部署侧 HTTP 传输：白名单收口、错误折算、GET/POST 形状。"""

from __future__ import annotations

import json

import httpx
import pytest
from deploy.transport import DEFAULT_TIMEOUT_S, HttpxTransport, host_port
from patrol.links import RetryingTransport, TransportError

CAPTURE_URL = "http://127.0.0.1:7003/pool_capture"


def make(handler, *, allowed=("127.0.0.1:7003",), **kwargs):
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return HttpxTransport(allowed_hosts=set(allowed), client=client, **kwargs)


# ---------- host_port ----------


def test_host_port_fills_default_port():
    assert host_port("http://10.77.77.39/ingest") == "10.77.77.39:80"
    assert host_port("https://example.com/x") == "example.com:443"
    assert host_port(CAPTURE_URL) == "127.0.0.1:7003"


def test_host_port_rejects_missing_host():
    with pytest.raises(TransportError):
        host_port("file:///etc/passwd")


# ---------- 白名单 ----------


def test_whitelist_is_case_insensitive():
    t = make(lambda r: httpx.Response(200, json={}), allowed=("MinIO.Local:9000",))
    assert t("http://minio.local:9000/x") == {}


def test_whitelist_rejects_other_host():
    t = make(lambda r: httpx.Response(200, json={"ok": True}))
    with pytest.raises(TransportError, match="白名单"):
        t("http://192.168.1.250:9000/mogu/x.jpg")


def test_whitelist_rejects_same_host_other_port():
    """只放行端口不够——同机别的服务不该被当成采图服务。"""
    t = make(lambda r: httpx.Response(200, json={}))
    with pytest.raises(TransportError, match="白名单"):
        t("http://127.0.0.1:9999/pool_capture")


# ---------- 请求形状 ----------


def test_params_only_uses_get():
    seen = {}

    def handler(request):
        seen["method"] = request.method
        seen["url"] = str(request.url)
        return httpx.Response(200, json={"success": True})

    t = make(handler)
    out = t(CAPTURE_URL, params={"ip": "192.168.1.238", "storage": "local"})
    assert out == {"success": True}
    assert seen["method"] == "GET"
    assert "ip=192.168.1.238" in seen["url"] and "storage=local" in seen["url"]


def test_body_switches_to_post():
    seen = {}

    def handler(request):
        seen["method"] = request.method
        seen["body"] = request.content.decode()
        return httpx.Response(200, json={"ingested": 2})

    t = make(handler, allowed={"10.77.77.39:8000"})
    out = t("http://10.77.77.39:8000/ingest", body={"rows": [{"kind": "round"}]})
    assert out == {"ingested": 2}
    assert seen["method"] == "POST"
    assert json.loads(seen["body"]) == {"rows": [{"kind": "round"}]}


# ---------- 错误折算：一律 TransportError（可重试类）----------


def test_non_2xx_becomes_transport_error():
    t = make(lambda r: httpx.Response(500, text="boom"))
    with pytest.raises(TransportError, match="500"):
        t(CAPTURE_URL)


def test_non_json_becomes_transport_error():
    t = make(lambda r: httpx.Response(200, text="<html>not json</html>"))
    with pytest.raises(TransportError, match="不是 JSON"):
        t(CAPTURE_URL)


def test_connect_error_becomes_transport_error():
    def handler(request):
        raise httpx.ConnectError("connection refused")

    t = make(handler)
    with pytest.raises(TransportError, match="请求失败"):
        t(CAPTURE_URL)


def test_retrying_transport_retries_transport_errors_then_succeeds():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(503, text="busy")
        return httpx.Response(200, json={"ok": True})

    t = RetryingTransport(make(handler), attempts=3, backoff_s=0, sleep=lambda _s: None)
    assert t(CAPTURE_URL) == {"ok": True}
    assert calls["n"] == 3


def test_retrying_transport_gives_up_after_attempts():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(503, text="busy")

    t = RetryingTransport(make(handler), attempts=2, backoff_s=0, sleep=lambda _s: None)
    with pytest.raises(TransportError):
        t(CAPTURE_URL)
    assert calls["n"] == 2


# ---------- 超时 ----------


def test_default_timeout_covers_measured_capture_time():
    """采图实测 capture_time_ms = 5200 ms；默认超时必须远大于它。

    否则会在**设计内耗时**上误判成链路故障——这是"慢服务"被当成"坏服务"的经典坑。
    """
    assert DEFAULT_TIMEOUT_S >= 30.0


# ---------- 业务失败信封（采图服务用 500 表示"这次没拍成"）----------


def test_accept_json_errors_returns_business_failure_envelope():
    """采图服务 500 + ``success:false`` 是**业务失败**，body 里带着 error_code 与
    message。这些语义必须交给业务层，不能当链路故障丢掉。"""
    body = {"success": False, "message": "Failed to capture screenshot (connection pool)",
            "error_code": -1239510}
    t = make(lambda r: httpx.Response(500, json=body), accept_json_errors=("success",))
    assert t(CAPTURE_URL) == body


def test_without_accept_json_errors_unrelated_500_is_still_link_failure():
    """同步端点（未声明信封键）的 5xx 仍按链路故障处理，才能被 RetryingTransport 重试。"""
    t = make(lambda r: httpx.Response(503, json={"success": False}))
    with pytest.raises(TransportError, match="503"):
        t(CAPTURE_URL)


def test_accept_json_errors_does_not_swallow_foreign_500():
    """只认**带信封键**的响应；网关错误页之类的 500 依旧算链路故障。"""
    t = make(lambda r: httpx.Response(500, json={"detail": "bad gateway"}),
             accept_json_errors=("success",))
    with pytest.raises(TransportError, match="500"):
        t(CAPTURE_URL)
