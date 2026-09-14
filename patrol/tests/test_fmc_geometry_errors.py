from patrol.fmc.errors import FmcError
from patrol.fmc.geometry import approach_point


def test_approach_point_retreats_along_incoming_direction():
    prev = (0.0, 0.0)
    target = (100.0, 0.0)
    mid = approach_point(prev, target, offset=5.0)
    assert abs(mid[0] - 95.0) < 1e-9
    assert abs(mid[1] - 0.0) < 1e-9


def test_approach_point_diagonal_distance_equals_offset():
    prev = (0.0, 0.0)
    target = (30.0, 40.0)
    mid = approach_point(prev, target, offset=5.0)
    dx, dy = target[0] - mid[0], target[1] - mid[1]
    assert abs((dx * dx + dy * dy) ** 0.5 - 5.0) < 1e-9


def test_approach_point_same_point_returns_target():
    target = (10.0, 10.0)
    assert approach_point(target, target, 5.0) == target


def test_error_text_known_code():
    err = FmcError(-1, "open")
    assert "连接失败" in str(err)
    assert "[open]" in str(err)
    assert err.code == -1


def test_error_text_unknown_code():
    assert "未知错误码" in str(FmcError(-42))
