"""「起转窗口」竞态的回归测试。

现场背景（2026-09-12，prod 10.77.77.39）：第一次让轴真动时，
`wait_stop` 在点动下发后立刻返回「已停」（耗时 0.00 s），
`wait_home` 则会读到**上一轮遗留的** `home_done`。两者都源于同一件事——
指令下发后控制器还没起转，这段时间读到的状态是**上一条指令的残值**：

    t=0.001s  Check_Axis_Is_Stop=1（"已停"）
    t=0.033s  Check_Axis_Is_Stop=0（运动中）
    t=5.277s  真正到位

而且 `home_done` 的语义是"坐标系已建立"，**普通点动不会把它作废**
（实测：Y 点动到 100 mm 后仍为 True），所以残值特别容易被误当作"这次已回零"。

下面的假状态序列就是照着这条实测时序写的。
"""

from __future__ import annotations

import time

from fmc_fakes import make_status_bytes
from patrol.fmc import Fmc4030
from patrol.fmc.status import (
    AXIS_HOME,
    AXIS_HOME_DONE,
    AXIS_RUNNING,
    AXIS_STOP,
    parse_machine_status,
)

IDLE = AXIS_STOP | AXIS_HOME_DONE      # 已停 + 已回零（静止时的常态）
BUSY = AXIS_RUNNING                     # 轴在跑（注意此时 home_done 也不置位）
HOMING = AXIS_HOME | AXIS_RUNNING       # 回零中


def client_with_flags(sequence: list[int]) -> tuple[Fmc4030, list[int]]:
    """构造一个 get_status 按给定 flag 序列返回的客户端；返回 (client, 取数计数)。"""
    client = Fmc4030(lib=object())      # 用不到真库：状态被整体替换
    seq = list(sequence)
    seen: list[int] = []

    def fake_get_status():
        seen.append(len(seen))
        return parse_machine_status(make_status_bytes(axis_flags=seq[min(len(seen) - 1, len(seq) - 1)]))

    client.get_status = fake_get_status  # type: ignore[method-assign]
    return client, seen


# ---------- wait_stop ----------


def test_wait_stop_does_not_trust_the_stale_stopped_reading():
    """前两次"已停"是残值，必须一直等到看见 running 再看见"已停"才算到位。

    start_grace_s 给一个不可能达到的值，把"从没见过起转"那条兜底路径排除掉，
    于是唯一能返回 True 的途径就是真的观察到了起转 → 已停。
    """
    client, seen = client_with_flags([IDLE, IDLE, BUSY, IDLE])
    assert client.wait_stop(axes=(1,), timeout_s=5.0, poll_s=0.0, start_grace_s=999.0) is True
    # 断言"看见了 running"，而不是"恰好 poll 了 4 次"——后者会把轮询次数的实现细节
    # 写死（加停稳确认后自然变多），从而误报。
    assert len(seen) > 3, "在看见 running 之前就返回了——残值被当成了到位"


# ---------- 停稳确认（2026-09-13 实测补充） ----------


def client_with_speeds(sequence: list[tuple[int, float]]):
    """按给定 (axis_flags, realSpeed) 序列返回状态的客户端。

    停稳判据要看 `realSpeed`，所以假状态必须能给出速度，否则那条路径永远测不到。
    """
    client = Fmc4030(lib=object())
    seq = list(sequence)
    seen: list[int] = []

    def fake_get_status():
        i = len(seen)
        seen.append(i)
        flags, speed = seq[min(i, len(seq) - 1)]
        return parse_machine_status(
            make_status_bytes(axis_flags=flags, speeds=(speed, speed, speed))
        )

    client.get_status = fake_get_status  # type: ignore[method-assign]
    return client, seen


def test_wait_stop_waits_for_speed_to_reach_zero():
    """running 清零那一刻速度还有 10.5 mm/s（实测）——不能就此认"到位"。

    实测时序：Y 374mm 移动，running 清零时 realSpeed=10.48，随后 2.594→2.6xx 才归零。
    若在清零那一刻返回，紧接着的同轴指令会撞上 -7（`goto` 接近段的真实故障）。
    """
    client, seen = client_with_speeds([
        (BUSY, 150.0),      # 运动
        (IDLE, 10.5),       # running 清零，但减速尾巴未走完 ← 关键
        (IDLE, 0.0),        # 速度归零，进入静定窗口
    ])
    assert client.wait_stop(axes=(1,), timeout_s=5.0, poll_s=0.0,
                            settle_s=0.05, settle_speed=1.0) is True
    assert len(seen) >= 5, "速度还很高的时候就返回了"


def test_wait_stop_times_out_when_speed_never_settles():
    """速度一直不降到门槛以下 ⇒ 不能认停稳，超时返回 False。"""
    client, _ = client_with_speeds([(BUSY, 150.0), (IDLE, 8.0)])
    assert client.wait_stop(axes=(1,), timeout_s=0.3, poll_s=0.0,
                            settle_s=0.05, settle_speed=1.0) is False


def test_wait_stop_accepts_a_genuinely_stopped_axis():
    """真的停了（速度 0）⇒ 正常返回 True（别把正常路径一起卡死）。"""
    client, _ = client_with_speeds([(BUSY, 150.0), (IDLE, 0.0)])
    assert client.wait_stop(axes=(1,), timeout_s=5.0, poll_s=0.0,
                            settle_s=0.02, settle_speed=1.0) is True


def test_wait_stop_uses_status_bits_not_check_axis_is_stop():
    """`Check_Axis_Is_Stop` 在起转窗口内报"已停"，不能作为等待判据。"""

    class Boom:
        def FMC4030_Check_Axis_Is_Stop(self, *a):  # 一旦被调用就失败
            raise AssertionError("wait_stop 不该走 Check_Axis_Is_Stop")

    client, _ = client_with_flags([BUSY, IDLE])
    client._lib = Boom()  # type: ignore[assignment]
    assert client.wait_stop(axes=(1,), timeout_s=5.0, poll_s=0.0) is True


def test_wait_stop_falls_back_to_grace_when_motion_is_never_observed():
    """极短的点动可能整段落在两次轮询之间：此时要求"已停"连续成立才算数。"""
    client, seen = client_with_flags([IDLE])
    t0 = time.monotonic()
    assert client.wait_stop(axes=(1,), timeout_s=5.0, poll_s=0.01, start_grace_s=0.05) is True
    assert time.monotonic() - t0 >= 0.05, "没等满宽限期就返回了"
    assert len(seen) >= 2, '第一次读到「已停」就返回了'


def test_wait_stop_times_out_instead_of_claiming_success():
    """一直在跑就必须超时返回 False，不能假装到位。"""
    client, _ = client_with_flags([BUSY])
    assert client.wait_stop(axes=(1,), timeout_s=0.05, poll_s=0.01) is False


# ---------- wait_home ----------


def test_wait_home_ignores_a_leftover_home_done():
    """回零刚下发时读到的是上一轮的 `home_done`，不能据此认为"已回到原点"。"""
    client, seen = client_with_flags([IDLE, HOMING, IDLE])
    assert client.wait_home(axes=(1,), timeout_s=5.0, poll_s=0.0, start_timeout_s=5.0) is True
    assert len(seen) == 3, "在观察到起转之前就返回了——上一轮的 home_done 被当成了本轮结果"


def test_wait_home_reports_timeout_when_homing_never_starts():
    """连起转都没确认 → 返回 False（由调用方上报超时），绝不静默当作成功。"""
    client, _ = client_with_flags([IDLE])
    t0 = time.monotonic()
    assert client.wait_home(axes=(1,), timeout_s=5.0, poll_s=0.01, start_timeout_s=0.05) is False
    assert time.monotonic() - t0 < 1.0, "应该按起转窗口快速返回，而不是等满主机侧超时"


def test_wait_home_accepts_completion_after_homing_was_seen():
    """正常情况下：起转 → 完成，应当返回 True。"""
    client, _ = client_with_flags([IDLE, HOMING, HOMING, IDLE])
    assert client.wait_home(axes=(1,), timeout_s=5.0, poll_s=0.0, start_timeout_s=5.0) is True
