from datetime import datetime

from analysis.env.humidity import humid_state


def _payload(ts_utc: str, running):
    return {"code": 0, "column_meta": [["ts", "TIMESTAMP", 8], ["Running", "FLOAT", 4]],
            "data": [[ts_utc, running]], "rows": 1}


def _state(payloads, *, room="611", now=datetime(2026, 10, 21, 11, 30)):
    calls = iter(payloads)

    def transport(url, sql):
        return next(calls)

    return humid_state(base_url="http://td:6041", room=room, transport=transport, now=now)


def test_any_running_device_means_humidifying():
    out = _state([_payload("2026-10-21T03:29:00.000Z", 0),
                  _payload("2026-10-21T03:29:00.000Z", 2)])
    assert out["humidifying"] is True
    assert [d["running"] for d in out["devices"]] == [0, 2]


def test_both_stopped_means_not_humidifying():
    out = _state([_payload("2026-10-21T03:29:00.000Z", 2),
                  _payload("2026-10-21T03:29:00.000Z", 3)])
    assert out["humidifying"] is False


def test_one_table_failing_still_uses_the_other():
    out = _state([_payload("2026-10-21T03:29:00.000Z", 2), {"code": 9730, "desc": "boom"}])
    assert out["humidifying"] is None  # 只剩一路"停止" ⇒ 不能断言没加湿


def test_stale_rows_are_unknown_not_false():
    out = _state([_payload("2026-10-21T01:00:00.000Z", 0),
                  _payload("2026-10-21T01:00:00.000Z", 0)],
                 now=datetime(2026, 10, 21, 11, 30))
    assert out["humidifying"] is None
    assert all(not d["fresh"] for d in out["devices"])


def test_unknown_room_is_an_error():
    try:
        _state([], room="999")
    except ValueError as exc:
        assert "999" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("未知库房必须报错")
