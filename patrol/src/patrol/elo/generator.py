"""M2 脱机脚本生成器（票 07）：站位表 → .elo 行文本（spec §4.3 模板）。

纯文本变换，无副作用；部署用 deploy() 写文件并经 SDK 下发。

本机两轴为 Y（轴 1）与 Z（轴 2），因此两轴插补的组合号是 **6**（0x03=X+Y、
0x05=X+Z、0x06=Y+Z），由 ``MotionProfile.axis_mask`` 派生。轴参数与回零方向
（**两轴都向负限位**：Y 往左、Z 往上）取自 ``motion_profile.M2``（ADR-0002/0007/0018）。

插补速度是**合成量**（厂商手册），脚本语言无运算能力，所以每段的合成速度在这里
用 ``composite_limits`` 预先算好后写死进脚本——脚本即文档。
"""

from __future__ import annotations

from pathlib import Path

from patrol.fmc.geometry import approach_point, composite_limits, segment_delta
from patrol.motion_profile import M2, MotionProfile
from patrol.stations import Station

# 脚本参数单一来源：motion_profile.M2（评审 #4，ADR-0002）
LAMP_IO = 0
LOOP_TARGET_LINE = 1  # 循环跳回行1（重新回零）
LOOP_COUNT = 999999

WAIT_SLOTS = 3  # 「等待回零完成」「等待轴运行完成」都是 3 个轴号槽（X/Y/Z 各一）


def _axis_slots(profile: MotionProfile, count: int = WAIT_SLOTS) -> tuple[int, ...]:
    """把**已接线**轴号循环铺满指令的定长参数槽。

    两条「等待…」指令各有 3 个轴号槽（对应 X/Y/Z），厂商文档没有定义"空槽"写法。
    必须用已接线轴回填——填 0（X）会让脚本去等一根根本没接的轴，轻则空等、重则
    卡住整条自动化流程。本机只有 Y/Z 两轴，于是得到 (1, 2, 1)。
    """
    axes = profile.axis_indices
    return tuple(axes[i % len(axes)] for i in range(count))


def _ms(seconds: float) -> int:
    return int(seconds * 1000)


def _fmt(v: float) -> str:
    if isinstance(v, int):
        return str(v)
    return str(int(v)) if float(v).is_integer() else f"{v:.1f}"


def _line(instr: str, *params) -> str:
    parts = [instr] + [_fmt(p) for p in params]
    return " | ".join(parts)


def _home_block(profile: MotionProfile) -> list[str]:
    """回零段：逐轴「设置回零运动参数」（含方向）+「启动单轴回零运动」。"""
    lines: list[str] = []
    for spec in profile.axes:
        lines += [
            _line("设置回零运动参数", spec.index, spec.home_speed, spec.home_acc, spec.home_dir),
            _line("启动单轴回零运动", spec.index, spec.home_release),
        ]
    lines += [
        _line("等待回零完成", *_axis_slots(profile)),
        _line("延时等待", 2000),
    ]
    return lines


def generate(
    stations: list[Station],
    *,
    travel_speed: float | None = None,
    approach_speed: float | None = None,
    profile: MotionProfile = M2,
    lamp_io: int = LAMP_IO,
) -> list[str]:
    """生成 .elo 脚本行序列（行号 = 列表下标 + 1）。

    ``travel_speed`` / ``approach_speed`` 一旦给出即作为该段的合成速度原样写入；
    省略时按各轴上限折算（``composite_limits``）。加/减速度一律取自折算结果。
    """
    lines: list[str] = _home_block(profile)
    if profile.lamp_hold_s is None:
        raise ValueError(
            "M2 脱机脚本需要固定灯窗（lamp_hold_s）；M1 是 None（由采图返回决定关灯），"
            "请传入 M2 或显式构造带 lamp_hold_s 的 profile"
        )
    axis_mask = profile.axis_mask
    prev: tuple[float, float] = (0.0, 0.0)  # 回零后位于原点
    for st in stations:
        target = (st.y, st.z)
        mid = approach_point(prev, target, profile.approach_offset)
        # 段序必须与 M1 的 `Fmc4030.goto` 一致（同一份几何函数，别各写各的）：
        # 有接近段时 prev→mid 是**空程**（巡检档全速），mid→target 才是**接近段**（降速）；
        # 没有接近段（offset=0 或来向距离 ≤ offset）时整段就是空程，按巡检档走。
        # 这里曾把两段的限值写反：空程按接近档爬行（整轮时长放大 5 倍），最后几毫米
        # 反而全速冲到位。见 ADR-0007「补充（2026-09-12）」。
        if mid != target:
            v, a = composite_limits(segment_delta(prev, mid), profile.travel_limits)
            legs = [(travel_speed if travel_speed is not None else v, a, mid)]
            v, a = composite_limits(segment_delta(mid, target), profile.approach_limits)
            legs.append((approach_speed if approach_speed is not None else v, a, target))
        else:
            v, a = composite_limits(segment_delta(prev, target), profile.travel_limits)
            legs = [(travel_speed if travel_speed is not None else v, a, target)]
        for speed, acc, point in legs:
            lines += [
                _line("设置直线插补参数", speed, acc, acc),
                _line("启动两轴直线插补", axis_mask, point[0], point[1]),
                _line("等待轴运行完成", *_axis_slots(profile)),
            ]
        lines += [
            _line("延时等待", _ms(profile.decay_s)),
            _line("本地输出口操作", lamp_io, 1),
            _line("延时等待", _ms(profile.lamp_hold_s)),
            _line("本地输出口操作", lamp_io, 0),
            _line("延时等待", _ms(profile.lamp_after_s)),
        ]
        prev = target
    lines += [
        _line("启动两轴直线插补", axis_mask, 0, 0),
        _line("等待轴运行完成", *_axis_slots(profile)),
        _line("循环", LOOP_TARGET_LINE, LOOP_COUNT),
    ]
    return lines


def generate_text(stations: list[Station], **kwargs) -> str:
    return "\n".join(generate(stations, **kwargs)) + "\n"


def deploy(
    stations: list[Station],
    *,
    file_path: str,
    client,  # patrol.fmc.Fmc4030
    script_name: str | None = None,
    **gen_kwargs,
) -> None:
    """生成脚本、写盘、下发控制器并启动自动运行。"""
    Path(file_path).write_text(generate_text(stations, **gen_kwargs), encoding="utf-8")
    client.download_script(file_path)
    client.start_script(script_name or Path(file_path).stem)
