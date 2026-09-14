import pytest
from fmc_fakes import FakeFmcLib
from patrol.fmc import (
    Fmc4030,
    FmcError,
    HomeTimeoutError,
    MotionTimeoutError,
    SoftLimitMismatchError,
    TravelLimitError,
)
from patrol.motion_profile import CONTROLLER_IP, M1

# 假库的 pos 是控制器的 3 轴坐标 (X, Y, Z)；本机只接 Y(槽1) 与 Z(槽2)，X 槽留 0


def make_client(**lib_kwargs) -> tuple[Fmc4030, FakeFmcLib]:
    lib = FakeFmcLib(**lib_kwargs)
    client = Fmc4030(lib)
    client.open()
    lib.calls.clear()  # open 之后的调用才是被测对象
    return client, lib


def test_open_failure_raises():
    lib = FakeFmcLib(open_rc=-1)
    client = Fmc4030(lib)
    with pytest.raises(FmcError):
        client.open()


def test_open_passes_ip_and_port():
    lib = FakeFmcLib()
    client = Fmc4030(lib, ip="192.168.1.66", port=8088)
    client.open()
    assert lib.calls_of("open") == [(1, b"192.168.1.66", 8088)]


def test_default_endpoint_is_the_deployed_controller():
    lib = FakeFmcLib()
    client = Fmc4030(lib)
    client.open()
    assert lib.calls_of("open") == [(1, CONTROLLER_IP.encode(), 8088)]
    assert CONTROLLER_IP == "192.168.1.239"


def test_goto_two_phase_motion():
    client, lib = make_client()
    lib.pos = (0.0, 0.0, 0.0)  # X 未接线, Y=0, Z=0
    client.goto(100.0, 0.0)
    lines = lib.calls_of("line2")
    assert len(lines) == 2
    # 巡检段：到距目标 5mm 处，走的是**空程** → Y 巡检档全速 150/1500
    assert lines[0] == (1, 0x06, 95.0, 0.0, 150.0, 1500.0, 1500.0)
    # 接近段：最后 5mm 才降速 → Y 接近档（巡检档 × 0.2）= 30/300
    assert lines[1] == (1, 0x06, 100.0, 0.0, 30.0, 300.0, 300.0)


def test_goto_composite_speed_is_limited_by_the_short_axis():
    """长轴主导的行程不被短轴拖慢，但任一根轴都不越限。

    起点 (0, 0) → 目标 (4000, -200)（``approach_offset=0`` ⇒ 没有接近段，整段按巡检档）：
    Y 占 0.9988、Z 占 0.05，合成量由 Y 的上限折算，Y 的实际分量恰好 = 其上限、
    Z 的分量远低于其上限。
    """
    client, lib = make_client()
    lib.pos = (0.0, 0.0, 0.0)
    client.goto(4000.0, -200.0, approach_offset=0.0)
    (call,) = lib.calls_of("line2")
    _, _, _, _, speed, acc, _ = call
    dist = (4000**2 + 200**2) ** 0.5
    assert speed == pytest.approx(M1.y.travel_speed * dist / 4000)
    assert acc == pytest.approx(M1.y.travel_acc * dist / 4000)
    # 各轴实际分量都不超上限
    assert speed * 4000 / dist <= M1.y.travel_speed + 1e-9
    assert speed * 200 / dist <= M1.z.travel_speed + 1e-9


def test_goto_rejects_out_of_travel_before_touching_controller():
    client, lib = make_client()
    with pytest.raises(TravelLimitError):
        client.goto(4493.0, 0.0)      # Y 超上限
    with pytest.raises(TravelLimitError):
        client.goto(100.0, 1.0)       # Z 必须是负数（向上为正，0 为顶端）
    assert lib.calls_of("line2") == []


def test_goto_short_move_single_segment():
    client, lib = make_client()
    lib.pos = (0.0, 98.0, 0.0)  # Y=98
    client.goto(100.0, 0.0, approach_offset=5.0)
    # 来向距离仅 2mm < 接近段 5mm ⇒ 没有接近段，整段就是空程，按**巡检档**（150/1500）
    # 一次到位；接近档只服务于"最后几毫米"，没有那几毫米就不降速。
    lines = lib.calls_of("line2")
    assert len(lines) == 1
    assert lines[0][2:4] == (100.0, 0.0)
    assert lines[0][4:7] == (150.0, 1500.0, 1500.0)


def test_lamp_on_off():
    client, lib = make_client()
    client.lamp(True)
    client.lamp(False)
    assert lib.calls_of("set_output") == [(1, 0, 1), (1, 0, 0)]


def test_line_failure_raises_fmc_error():
    client, _ = make_client(line_rc=-5)
    with pytest.raises(FmcError):
        client.goto(10.0, 0.0)


def test_jog_relative_single_axis():
    client, lib = make_client()
    lib.pos = (0.0, 50.0, 0.0)  # Y=50
    client.jog(1, +10.0, speed=5.0, acc=50.0, dec=80.0)
    # 单轴相对运动（mode=1），位置按当前坐标叠加
    assert lib.calls_of("jog") == [(1, 1, 10.0, 5.0, 50.0, 80.0, 1)]
    assert client.current_yz() == (60.0, 0.0)


def test_jog_defaults_come_from_the_axis_profile():
    """Y 的点动档 = 目标 50/500 × 0.2 = 10/100。"""
    client, lib = make_client()
    lib.pos = (0.0, 50.0, 0.0)
    client.jog(1, -20.0)
    assert lib.calls_of("jog") == [(1, 1, -20.0, 10.0, 100.0, 100.0, 1)]
    assert client.current_yz() == (30.0, 0.0)


def test_jog_z_uses_its_own_slower_profile():
    """Z 的目标速度只有 20，点动档随之降到 4/40。"""
    client, lib = make_client()
    lib.pos = (0.0, 0.0, -100.0)
    client.jog(2, -8.0)
    assert lib.calls_of("jog") == [(1, 2, -8.0, 4.0, 40.0, 40.0, 1)]
    assert client.current_yz() == (0.0, -108.0)


def test_unwired_axis_is_rejected():
    """X（轴 0）未接线，驱动层直接拒绝而不是发指令。"""
    client, lib = make_client()
    with pytest.raises(KeyError):
        client.jog(0, 10.0)
    assert lib.calls_of("jog") == []


def test_move_axis_absolute_single_axis():
    client, lib = make_client()
    lib.pos = (0.0, 0.0, 0.0)
    client.move_axis(1, 350.0, speed=25.0, acc=150.0, dec=150.0)
    # 单轴绝对运动（mode=2），pos 即目标坐标
    assert lib.calls_of("jog") == [(1, 1, 350.0, 25.0, 150.0, 150.0, 2)]
    assert client.current_yz() == (350.0, 0.0)


def test_move_axis_checks_travel_on_the_target_axis_only():
    client, lib = make_client()
    with pytest.raises(TravelLimitError):
        client.move_axis(2, 10.0)  # Z 上限是 0
    assert lib.calls_of("jog") == []


def test_goto_2axis_single_segment_full_params():
    client, lib = make_client()
    lib.pos = (0.0, 0.0, 0.0)
    client.goto_2axis(100.0, -120.0, speed=80.0, acc=300.0, dec=400.0)
    lines = lib.calls_of("line2")
    assert len(lines) == 1
    # M0 直线插补单段直达，速度/加/减速全部由调用方显式给出（原样下发）
    assert lines[0] == (1, 0x06, 100.0, -120.0, 80.0, 300.0, 400.0)


def test_home_all_uses_per_axis_profile():
    """Y 反向（负限位=2）回零、Z 向上（正限位=1）回零，参数各按本轴整定值。"""
    client, lib = make_client()
    client.home_all()
    homes = lib.calls_of("home")
    assert [h[1] for h in homes] == [1, 2]
    assert homes[0][2:] == (90.0, 900.0, 5.0, 2)     # Y：速度 90、加减速 900、脱落 5、负限位
    assert homes[1][2:] == (20.0, 200.0, 5.0, 1)     # Z：速度 20、加减速 200、脱落 5、正限位


def test_home_axis_override_and_unwired_reject():
    client, lib = make_client()
    client.home_axis(2, speed=9.0, direction=2)
    assert lib.calls_of("home") == [(1, 2, 9.0, 200.0, 5.0, 2)]   # accdec 仍取 Z 轴整定值
    with pytest.raises(KeyError):
        client.home_axis(0)


def test_get_status_parses_position():
    client, lib = make_client()
    lib.pos = (12.0, 34.0, -5.0)
    ms = client.get_status()
    assert ms.real_pos == (12.0, 34.0, -5.0)
    assert client.current_yz() == (34.0, -5.0)


def test_stop_everything_stops_interpolation_first_then_both_axes():
    """厂商手册：Stop_Single_Axis「不能用于插补运动时的停止」，插补要用 Stop_Run。

    所以急停顺序是「先停插补、再逐轴立即停止」，与直觉相反但符合手册。
    """
    client, lib = make_client()
    assert client.stop_everything() == []
    assert lib.calls_of("stop_single") == [(1, 1, 2), (1, 2, 2)]
    assert lib.calls_of("stop_run") == [(1,)]
    order = [fn for fn, _ in lib.calls if fn in ("stop_run", "stop_single")]
    assert order == ["stop_run", "stop_single", "stop_single"]


def test_stop_everything_reports_failures_instead_of_aborting_halfway():
    """急停链路不能因为一条指令报错就中断——收集失败并继续把其余指令送出去。"""
    client, lib = make_client()
    lib.stop_run_rc = -5  # 停插补失败
    assert client.stop_everything() == ["stop_run"]
    # stop_run 失败后，两条单轴停止仍必须送达
    assert lib.calls_of("stop_single") == [(1, 1, 2), (1, 2, 2)]


# ---------- 回零等待（控制器可能自行终止回零） ----------


def test_home_all_defaults_to_waiting_for_completion():
    lib = FakeFmcLib(stop_after_status_calls=3)  # 头两次状态查询还没回零完成
    client = Fmc4030(lib, ip="127.0.0.1", port=1)
    client.open()
    client.home_all(poll_s=0.0)
    assert [h[1] for h in lib.calls_of("home")] == [1, 2]
    assert len(lib.calls_of("get_status")) >= 3


def test_home_all_can_skip_waiting():
    client, lib = make_client(stop_after_status_calls=99_999)
    client.home_all(wait=False)
    assert [h[1] for h in lib.calls_of("home")] == [1, 2]
    assert lib.calls_of("get_status") == []


def test_home_reports_controller_overtime_immediately():
    """控制器报「轴回零超时」＝限位开关没触发，原点不可信，必须立刻失败。"""
    lib = FakeFmcLib(home_overtime=True)
    client = Fmc4030(lib, ip="127.0.0.1", port=1)
    client.open()
    with pytest.raises(HomeTimeoutError, match="回零超时"):
        client.home_all()


def test_home_host_side_timeout_raises_motion_timeout():
    client, _ = make_client(stop_after_status_calls=99_999)
    with pytest.raises(MotionTimeoutError, match="回零到位确认超时"):
        client.home_all(timeout_s=0.0, poll_s=0.0)


# ---------- 控制器整定参数（软限位） ----------


def test_device_para_round_trip_from_the_controller():
    client, lib = make_client(soft_limits=(212, 4600))
    para = client.get_device_para()
    assert para.ip == "192.168.1.239"
    assert para.port == 8088
    assert para.raw_limits(1) == (212, 4600)        # 原始字段
    assert para.effective_limits(1) == (-212, 4600)  # 生效区间
    assert lib.calls_of("get_device_para") == [(1,)]


def test_factory_soft_limits_are_reported_as_mismatch():
    """出厂默认 ±200mm：Y 要 4492mm，必须整定——否则控制器会把目标截断且返回成功。"""
    client, _ = make_client()  # 默认即出厂值 (200, 200)
    issues = client.soft_limit_issues()
    assert [i.axis_name for i in issues] == ["Y", "Z"]
    assert "4492" in str(issues[0])
    with pytest.raises(SoftLimitMismatchError, match="软限位未按行程整定"):
        client.check_soft_limits()


def test_commissioned_soft_limits_pass_the_check():
    client, _ = make_client(soft_limits=(212, 4600))
    assert client.soft_limit_issues() == []
    client.check_soft_limits()  # 不抛即通过


def test_negative_soft_limit_means_disabled_not_narrow():
    """说明书 §三.4：正负软限位任一为负即取消软限位——取消不是"过窄"。"""
    client, _ = make_client(soft_limits=(-1, -1))
    assert client.soft_limit_issues() == []


def test_set_device_para_writes_whole_struct():
    client, lib = make_client()
    para = client.get_device_para()
    from dataclasses import replace

    client.set_device_para(replace(para, soft_limit_max=(200, 4600, 200),
                                   soft_limit_min=(200, 212, 200)))
    assert lib.written_soft_limits[1] == (212, 4600)
    assert lib.calls_of("set_device_para") == [(1,)]


def test_close_releases_connection():
    lib = FakeFmcLib()
    client = Fmc4030(lib)
    client.open()
    client.close()
    assert lib.calls_of("close") == [(1,)]
    # 二次 close 不再发指令（幂等）
    client.close()
    assert lib.calls_of("close") == [(1,)]


def test_script_download_and_start():
    client, lib = make_client()
    client.download_script("/tmp/patrol.elo")
    client.start_script("patrol.elo")
    client.stop_script()
    assert lib.calls_of("download") == [(1, b"/tmp/patrol.elo", 2)]
    assert lib.calls_of("start_script") == [(1, b"patrol.elo")]
