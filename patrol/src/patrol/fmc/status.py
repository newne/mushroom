"""machine_status 结构体定义与解析。

结构体布局以官方 Python 示例（FMC4030Demo.py）与《FMC4030二次开发库详解》为准；
位标志定义取自 linux 版二次开发库 FMC4030.h。
解析为 dataclass 属纯逻辑，可独立测试。
"""

from __future__ import annotations

import ctypes
from dataclasses import dataclass

MAX_AXIS = 3

# machineRunStatus
MODE_MANUAL = 0x0001
MODE_AUTO = 0x0002

# axisStatus 位标志。逐条抄自 linux 版 FMC4030.h，括号内**是厂商原文注释**，
# 不要凭名字猜——`*_NONE` 后缀极易被误读成"没有这个开关"，实际是"未触发/未完成"：
#   实测佐证（2026-09-12，prod 10.77.77.39 读回）：Y/Z 已回零时 home_done 置位、
#   home_none 清零；未接线的轴0 恰好相反。
AXIS_RUNNING = 0x0001        # 轴正在运行
AXIS_PAUSE = 0x0002          # 轴暂停运行
# 0x0004 MACHINE_RESUME      厂商注释为「无」，未使用
AXIS_STOP = 0x0008           # 轴停止运行
AXIS_LIMIT_N = 0x0010        # 负限位触发
AXIS_LIMIT_P = 0x0020        # 正限位触发
AXIS_HOME_DONE = 0x0040      # 轴回零完成
AXIS_HOME = 0x0080           # 轴回零中（回零进行时置位）
# 0x0100 MACHINE_AUTO_RUN    厂商注释为「无」，未使用
AXIS_LIMIT_N_NONE = 0x0200   # 负限位**未触发**（≠「没有负限位开关」）
AXIS_LIMIT_P_NONE = 0x0400   # 正限位**未触发**
AXIS_HOME_NONE = 0x0800      # **未回零**（与 HOME_DONE 互补）
AXIS_HOME_OVERTIME = 0x1000  # 回零超时


class MachineStatusStruct(ctypes.Structure):
    """与 SDK struct machine_status 逐字段对应的 ctypes 结构体。"""

    _fields_ = [
        ("realPos", ctypes.c_float * MAX_AXIS),
        ("realSpeed", ctypes.c_float * MAX_AXIS),
        ("inputStatus", ctypes.c_int32),
        ("outputStatus", ctypes.c_int32),
        ("limitNStatus", ctypes.c_int32),
        ("limitPStatus", ctypes.c_int32),
        ("machineRunStatus", ctypes.c_int32),
        ("axisStatus", ctypes.c_int32 * MAX_AXIS),
        ("homeStatus", ctypes.c_int32),
        ("file", ctypes.c_ubyte * 600),
    ]


@dataclass(frozen=True)
class AxisStatus:
    """单轴状态。

    注意命名陷阱：``limit_n_none`` / ``limit_p_none`` / ``home_none`` 里的 ``none``
    是厂商英文对「未触发 / 未回零」的译法（见上面常量注释的厂商原文），**不是**
    「该轴没有装这个开关」。想判断某个限位开关是否真的装了，不能看这三位。
    """

    running: bool = False
    paused: bool = False
    stopped: bool = False
    limit_n: bool = False
    limit_p: bool = False
    home_done: bool = False
    homing: bool = False
    limit_n_none: bool = False
    limit_p_none: bool = False
    home_none: bool = False
    home_overtime: bool = False

    @property
    def homed(self) -> bool:
        """原点可信 = 回零已完成、不在回零过程中、且未回零位未置起。

        ``home_done`` 与 ``home_none`` 在厂商定义里互补，这里两个都看：一旦控制器
        给出的两位互相矛盾（既不"完成"也不"未回零"以外的组合），宁可判为未回零——
        原点不可信时继续下发绝对坐标会整体偏移，代价远大于多回零一次。
        """
        return self.home_done and not self.homing and not self.home_none


@dataclass(frozen=True)
class MachineStatus:
    real_pos: tuple[float, float, float]
    real_speed: tuple[float, float, float]
    inputs: int
    outputs: int
    run_mode: str  # "manual" | "auto" | "unknown"
    axes: tuple[AxisStatus, ...]


def parse_machine_status(raw: bytes) -> MachineStatus:
    """把 SDK 填充好的缓冲区解析为 MachineStatus。"""
    s = MachineStatusStruct.from_buffer_copy(raw)
    mode = s.machineRunStatus
    run_mode = "manual" if mode & MODE_MANUAL else "auto" if mode & MODE_AUTO else "unknown"
    axes = tuple(
        AxisStatus(
            running=bool(v & AXIS_RUNNING),
            paused=bool(v & AXIS_PAUSE),
            stopped=bool(v & AXIS_STOP),
            limit_n=bool(v & AXIS_LIMIT_N),
            limit_p=bool(v & AXIS_LIMIT_P),
            home_done=bool(v & AXIS_HOME_DONE),
            homing=bool(v & AXIS_HOME),
            limit_n_none=bool(v & AXIS_LIMIT_N_NONE),
            limit_p_none=bool(v & AXIS_LIMIT_P_NONE),
            home_none=bool(v & AXIS_HOME_NONE),
            home_overtime=bool(v & AXIS_HOME_OVERTIME),
        )
        for v in s.axisStatus
    )
    return MachineStatus(
        real_pos=tuple(round(float(p), 3) for p in s.realPos),
        real_speed=tuple(round(float(v), 3) for v in s.realSpeed),
        inputs=int(s.inputStatus),
        outputs=int(s.outputStatus),
        run_mode=run_mode,
        axes=axes,
    )
