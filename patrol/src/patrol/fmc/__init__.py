"""FMC4030 运动控制封装（票 01/07）。

本机两轴为 Y（轴 1，水平 0…4492 mm）与 Z（轴 2，竖直 -212…0 mm）——见
``patrol.motion_profile`` 的现场实测参数与 ``docs/adr/0007-per-axis-motion-profile.md``。

控制器**自带**一层软限位，出厂默认 ±200mm，必须按行程整定（``check_soft_limits``）；
详见 ``patrol.fmc.device_para`` 与 ADR-0008。
"""

from patrol.fmc.client import Fmc4030
from patrol.fmc.device_para import DevicePara, SoftLimitIssue, parse_device_para
from patrol.fmc.errors import (
    FmcError,
    HomeTimeoutError,
    MotionTimeoutError,
    SoftLimitMismatchError,
    TravelLimitError,
    TravelShortfallError,
)
from patrol.fmc.geometry import approach_point, composite_limits, segment_delta
from patrol.fmc.status import MachineStatus, parse_machine_status

__all__ = [
    "DevicePara",
    "Fmc4030",
    "FmcError",
    "HomeTimeoutError",
    "MachineStatus",
    "MotionTimeoutError",
    "SoftLimitIssue",
    "SoftLimitMismatchError",
    "TravelLimitError",
    "TravelShortfallError",
    "approach_point",
    "composite_limits",
    "parse_device_para",
    "parse_machine_status",
    "segment_delta",
]
