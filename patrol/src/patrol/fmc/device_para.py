"""machine_device_para 结构体：控制器整定参数的读写（导程/细分/软限位/回零超时）。

布局取自厂商 linux 版二次开发库 ``FMC4030.h``（与《FMC4030控制器使用说明书V2001》
§三.4「参数」一节的字段一一对应）::

    struct machine_device_para{
        unsigned int id;      unsigned int bound232;  unsigned int bound485;
        char ip[15];          int port;
        int div[MAX_AXIS];    // 细分
        int lead[MAX_AXIS];   // 导程（mm/圈 → mm 换算的依据）
        int softLimitMax[MAX_AXIS];
        int softLimitMin[MAX_AXIS];
        int homeTime[MAX_AXIS];  // 回零超时（ms）
    };

**为什么这层必须存在**：控制器自带软限位，且**出厂默认只有 ±200mm**（说明书 §三.4）。
本机 Y 行程是 0…4492mm——如果现场没有把 Y 的 ``softLimitMax`` 提到 4492 以上，
控制器会把 Y 的目标位置**自行截断到 200mm**，表现为"指令下发成功但只走一小段"。
这类故障在业务层看不出来，只能靠读回设备参数发现，所以整定值要和运动参数一样可读、
可校验（``Fmc4030.check_soft_limits``）。

软限位的取消方式：把正、负极限中的任一个设为负数即可（说明书 §三.4）。
"""

from __future__ import annotations

import ctypes
from dataclasses import dataclass

MAX_AXIS = 3
IP_LEN = 15


class DeviceParaStruct(ctypes.Structure):
    """与 SDK struct machine_device_para 逐字段对应的 ctypes 结构体。"""

    _fields_ = [
        ("id", ctypes.c_uint32),
        ("bound232", ctypes.c_uint32),
        ("bound485", ctypes.c_uint32),
        ("ip", ctypes.c_char * IP_LEN),
        ("port", ctypes.c_int32),
        ("div", ctypes.c_int32 * MAX_AXIS),
        ("lead", ctypes.c_int32 * MAX_AXIS),
        ("softLimitMax", ctypes.c_int32 * MAX_AXIS),
        ("softLimitMin", ctypes.c_int32 * MAX_AXIS),
        ("homeTime", ctypes.c_int32 * MAX_AXIS),
    ]


@dataclass(frozen=True)
class DevicePara:
    """控制器设备参数的可读视图（轴参数按 3 轴定长给出，未接线轴留原值）。"""

    ip: str
    port: int
    div: tuple[int, int, int]
    lead: tuple[int, int, int]
    soft_limit_max: tuple[int, int, int]
    soft_limit_min: tuple[int, int, int]
    home_time: tuple[int, int, int]
    dev_id: int = 0
    bound232: int = 0
    bound485: int = 0

    def raw_limits(self, axis: int) -> tuple[int, int]:
        """控制器里存的原值 ``(softLimitMin, softLimitMax)``。"""
        return (self.soft_limit_min[axis], self.soft_limit_max[axis])

    def effective_limits(self, axis: int) -> tuple[int, int] | None:
        """该轴**实际生效**的软限位区间；``None`` = 已取消软限位。

        符号约定（**2026-09-12 已在 prod 机器上实测证实**，见 ADR-0008「现场核实」）：
        厂商标称"软件正限位 200、软件负限位 200"，若 ``softLimitMin`` 存的是有符号边界，
        那默认就是 -200——而说明书又说"把任一软限位设成负数即取消"，两者会自相矛盾。
        因此这里按**「负极限字段存幅值」**解读：实际区间 = ``[-softLimitMin, +softLimitMax]``，
        任一字段为负才是"取消"。

        实测佐证：Y 轴原始 ``softLimitMax=4495, softLimitMin=0`` → 生效 ``[0, 4495]``
        （与 4492mm 行程一致）；Z 轴 ``softLimitMax=-1`` 为负 → 判为取消。
        """
        lo_raw, hi_raw = self.raw_limits(axis)
        if lo_raw < 0 or hi_raw < 0:
            return None
        return (-lo_raw, hi_raw)

    def soft_limit_enabled(self, axis: int) -> bool:
        return self.effective_limits(axis) is not None

    def describe(self) -> str:
        axes = " / ".join(
            f"轴{i} 细分 {self.div[i]} 导程 {self.lead[i]} "
            f"软限位 {self._fmt_limits(i)} 回零超时 {self.home_time[i]}ms"
            for i in range(MAX_AXIS)
        )
        return f"{self.ip}:{self.port} · {axes}"

    def _fmt_limits(self, axis: int) -> str:
        eff = self.effective_limits(axis)
        if eff is None:
            return f"已取消（原值 {self.raw_limits(axis)}）"
        return f"{eff[0]}…{eff[1]}"


def parse_device_para(raw: bytes) -> DevicePara:
    """把 SDK 填充好的缓冲区解析为 DevicePara。"""
    s = DeviceParaStruct.from_buffer_copy(raw)
    return DevicePara(
        ip=bytes(s.ip).split(b"\0", 1)[0].decode("ascii", "replace"),
        port=int(s.port),
        div=tuple(int(v) for v in s.div),                    # type: ignore[arg-type]
        lead=tuple(int(v) for v in s.lead),                  # type: ignore[arg-type]
        soft_limit_max=tuple(int(v) for v in s.softLimitMax),  # type: ignore[arg-type]
        soft_limit_min=tuple(int(v) for v in s.softLimitMin),  # type: ignore[arg-type]
        home_time=tuple(int(v) for v in s.homeTime),         # type: ignore[arg-type]
        dev_id=int(s.id),
        bound232=int(s.bound232),
        bound485=int(s.bound485),
    )


def build_device_para(para: DevicePara) -> DeviceParaStruct:
    """DevicePara → ctypes 结构体（供 ``FMC4030_Set_Device_Para`` 下发）。

    轴参数必须整组给出：控制器只接受完整结构体，没有"只改软限位"的局部写。
    因此调用方应先 ``get_device_para()`` 读回现值，改完再整体写回（read-modify-write）。
    长度不齐的轴元组会被显式拒绝——ctypes 会**静默补 0**，那等于把某根轴的软限位
    悄悄设成 0，属于最难查的一类错误。
    """
    for name in ("div", "lead", "soft_limit_max", "soft_limit_min", "home_time"):
        values = getattr(para, name)
        if len(values) != MAX_AXIS:
            raise ValueError(f"{name} 需要 {MAX_AXIS} 个轴的值，收到 {len(values)} 个：{values!r}")
    ip = para.ip.encode("ascii")[: IP_LEN - 1]
    return DeviceParaStruct(
        id=para.dev_id,
        bound232=para.bound232,
        bound485=para.bound485,
        ip=ip,
        port=para.port,
        div=(ctypes.c_int32 * MAX_AXIS)(*para.div),
        lead=(ctypes.c_int32 * MAX_AXIS)(*para.lead),
        softLimitMax=(ctypes.c_int32 * MAX_AXIS)(*para.soft_limit_max),
        softLimitMin=(ctypes.c_int32 * MAX_AXIS)(*para.soft_limit_min),
        homeTime=(ctypes.c_int32 * MAX_AXIS)(*para.home_time),
    )


@dataclass(frozen=True)
class SoftLimitIssue:
    """软限位与机械行程不一致的一项（供 ``check_soft_limits`` 汇总）。"""

    axis_name: str
    axis: int
    controller_min: int
    controller_max: int
    travel_min: float
    travel_max: float

    def __str__(self) -> str:  # pragma: no cover - 仅用于人读的报错信息
        return (
            f"{self.axis_name}(轴{self.axis}) 控制器软限位 "
            f"{self.controller_min}…{self.controller_max} 窄于机械行程 "
            f"{self.travel_min:g}…{self.travel_max:g} mm"
        )
