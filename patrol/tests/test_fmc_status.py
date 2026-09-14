from fmc_fakes import make_status_bytes
from patrol.fmc.status import (
    AXIS_HOME,
    AXIS_HOME_DONE,
    AXIS_HOME_OVERTIME,
    AXIS_LIMIT_N_NONE,
    AXIS_STOP,
    AxisStatus,
    parse_machine_status,
)


def test_parse_fields_roundtrip():
    raw = make_status_bytes(pos=(1200.5, 350.25, 0.0), outputs=0b0011)
    ms = parse_machine_status(raw)
    assert ms.real_pos[0] == 1200.5
    assert ms.real_pos[1] == 350.25
    assert ms.outputs == 0b0011
    assert ms.run_mode == "auto"
    assert len(ms.axes) == 3
    assert ms.axes[0] == AxisStatus(stopped=True, home_done=True)


def test_parse_manual_mode():
    raw = make_status_bytes(run_mode=0x0001)
    assert parse_machine_status(raw).run_mode == "manual"


def test_parse_unknown_mode():
    raw = make_status_bytes(run_mode=0)
    assert parse_machine_status(raw).run_mode == "unknown"


def test_home_done_while_still_homing_is_not_homed():
    """回零过程中「回零完成」位可能已经置上，必须同时看「回零中」才敢当原点用。"""
    mid = parse_machine_status(make_status_bytes(axis_flags=AXIS_HOME_DONE | AXIS_HOME)).axes[0]
    assert mid.home_done is True and mid.homing is True
    assert mid.homed is False
    done = parse_machine_status(make_status_bytes(axis_flags=AXIS_HOME_DONE)).axes[0]
    assert done.homed is True


def test_home_overtime_flag_is_parsed():
    """控制器在 homeTime 内没等到限位开关会置「回零超时」——原点不可信。"""
    ax = parse_machine_status(make_status_bytes(axis_flags=AXIS_HOME_OVERTIME)).axes[0]
    assert ax.home_overtime is True
    assert ax.homed is False


def test_limit_not_triggered_flag_is_parsed():
    ax = parse_machine_status(make_status_bytes(axis_flags=AXIS_STOP | AXIS_LIMIT_N_NONE)).axes[0]
    assert ax.limit_n_none is True and ax.limit_n is False
