"""操作日志：采集、解析、汇总与接口（2026-10-12 用户要求"体现操作员的实际操作"）。"""

import pytest
from fastapi.testclient import TestClient

from analysis.api import create_app
from analysis.audit import (
    AuthError,
    fetch_operations,
    login,
    md5_hex,
    norm_ts,
    parse_operations,
    room_of,
    summarize_operations,
)
from analysis.db import connect, fetch_operations as db_fetch, upsert_operations

RAW = {
    "total": 1,
    "list": [{
        "deviceCode": "TD1_Q3MDHUM02", "pointCode": "On", "pointValue": "5",
        "actionSource": "现场", "actionState": True, "actionModel": "现场操作员",
        "operator": "现场操作员", "remark": None, "operateTime": "2026-10-12 10:10:00",
        "deviceName": "607库-右加湿器", "pointName": "加湿开启设定",
        "actionType": None, "actionContent": "加湿开启设定[5]",
    }],
}


def test_room_of_maps_device_prefix():
    assert room_of("TD1_Q3MDHUM02") == "607"
    assert room_of("TD1_Q1MDTM01") == "611"
    assert room_of("UNKNOWN") is None


def test_norm_ts_is_the_store_shape():
    assert norm_ts("2026-10-12 10:10:00") == "2026-10-12T10:10:00"


def test_md5_helper_matches_platform_expectation():
    assert md5_hex("123456") == "e10adc3949ba59abbe56e057f20f883e"


def test_parse_operations_keeps_the_operating_detail():
    rows = parse_operations(RAW)
    assert len(rows) == 1
    row = rows[0]
    assert row["ts"] == "2026-10-12T10:10:00" and row["zone"] == "607"
    assert row["device_name"] == "607库-右加湿器" and row["point_name"] == "加湿开启设定"
    assert row["value"] == "5" and row["operator"] == "现场操作员"
    assert row["content"] == "加湿开启设定[5]"


def test_parse_operations_skips_rows_without_time_or_device():
    assert parse_operations({"list": [{"deviceCode": "X"}, {"operateTime": "2026-10-12 10:00:00"}]}) == []


def test_login_raises_auth_error_on_400100():
    def transport(url, body, headers=None):
        return {"code": 400100, "message": "认证失败", "data": None}

    with pytest.raises(AuthError, match="认证失败"):
        login("http://ems", "u", md5_hex("p"), transport=transport)


def test_login_returns_token():
    seen = {}

    def transport(url, body, headers=None):
        seen["url"] = url
        seen["body"] = body
        return {"code": 200, "data": {"accessToken": "tok"}}

    assert login("http://ems", "u", md5_hex("p"), transport=transport) == "tok"
    assert seen["url"].endswith("/service/security/auth/login")
    assert seen["body"]["password"] == md5_hex("p")      # 明文会被平台拒掉


def test_fetch_operations_pages_until_total():
    calls = []

    def transport(url, body, headers=None):
        calls.append(body)
        assert headers and headers["X-AUTH-TOKEN"] == "tok"
        if body["pageNum"] == 1:
            return {"code": 200, "data": {"total": 2, "list": RAW["list"]}}
        return {"code": 200, "data": {"total": 2, "list": [dict(RAW["list"][0], pointCode="Off")]}}

    rows = fetch_operations("http://ems", "tok", "2026-10-12 00:00:00", "2026-10-12 23:59:59",
                            page_size=1, transport=transport)
    assert len(rows) == 2 and len(calls) == 2


def test_summarize_operations_counts_by_day_device_and_operator():
    rows = parse_operations({"list": RAW["list"] + [dict(RAW["list"][0])]})
    got = summarize_operations(rows)
    assert got["n"] == 2
    assert got["by_day"] == {"2026-10-12": 2}
    assert got["by_device"] == {"607库-右加湿器": 2}
    assert got["by_operator"] == {"现场操作员": 2}


def test_operations_upsert_is_idempotent(tmp_path):
    conn = connect(str(tmp_path / "t.db"))
    rows = parse_operations(RAW)
    upsert_operations(conn, rows)
    upsert_operations(conn, rows)
    assert len(db_fetch(conn, zone="607")) == 1


def test_api_operations_returns_rows_and_summary(tmp_path):
    db = str(tmp_path / "t.db")
    conn = connect(db)
    upsert_operations(conn, parse_operations(RAW))
    conn.close()
    got = TestClient(create_app(db)).get("/operations", params={"zone": "607"}).json()
    assert got["ok"] is True and len(got["rows"]) == 1
    assert got["rows"][0]["content"] == "加湿开启设定[5]"
    assert got["summary"]["by_day"] == {"2026-10-12": 1}
