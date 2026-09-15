"""测试用 FMC4030 假库：记录调用、按脚本返回，模拟轴运动与状态缓冲区。"""

from __future__ import annotations

import ctypes

from patrol.fmc.device_para import DeviceParaStruct
from patrol.fmc.status import (
    AXIS_HOME,
    AXIS_HOME_DONE,
    AXIS_HOME_OVERTIME,
    AXIS_RUNNING,
    AXIS_STOP,
    MODE_AUTO,
    MachineStatusStruct,
)


class FakeFmcLib:
    """实现与 SDK 同名函数的最小假库。

    - calls: (函数名, 参数元组) 列表，按序记录
    - open_rc / line_rc: 可注入失败返回值
    - stop_after_status_calls: 状态查询达到该次数后轴视为停止
    - soft_limits: 控制器软限位**原值** ``(softLimitMin, softLimitMax)``；
      出厂默认 ``(200, 200)`` 即实际区间 ±200mm（见 DevicePara.effective_limits）
    - home_overtime: 置位后状态里带上「轴回零超时」
    - home_status_calls: 「启动单轴回零」到「回零完成」之间隔几次状态查询。
      这段过渡**必须模拟**：真机实测回零期间 `home_done` 会被清零、`homing` 置起
      （raw 0x0648 → 0x0681 → … → 0x0648），而 `home_done` 的语义是"坐标系已建立"、
      普通点动不会把它作废。不模拟的话，"回零刚下发就读到上一轮的 home_done"
      这类竞态在测试里永远暴露不出来。
    """

    def __init__(
        self,
        *,
        open_rc: int = 0,
        line_rc: int = 0,
        stop_after_status_calls: int = 1,
        soft_limits: tuple[int, int] = (200, 200),
        home_overtime: bool = False,
        home_status_calls: int = 2,
    ):
        self.calls: list[tuple[str, tuple]] = []
        self.open_rc = open_rc
        self.line_rc = line_rc
        self.stop_after_status_calls = stop_after_status_calls
        self.soft_limits = soft_limits
        self.home_overtime = home_overtime
        self.home_status_calls = home_status_calls
        self.home_countdown: dict[int, int] = {}
        self._status_calls = 0
        self._check_calls = 0
        self.busy_axes = set()
        self.pos = (0.0, 0.0, 0.0)
        self.outputs = 0
        self.opened = False
        self.stop_single_rc = 0
        self.stop_run_rc = 0

    # --- SDK 同名函数 ---

    def FMC4030_Open_Device(self, dev_id, ip, port):
        self.calls.append(("open", (dev_id, ip, port)))
        if self.open_rc == 0:
            self.opened = True
        return self.open_rc

    def FMC4030_Close_Device(self, dev_id):
        self.calls.append(("close", (dev_id,)))
        self.opened = False
        return 0

    def FMC4030_Get_Machine_Status(self, dev_id, buf):
        self.calls.append(("get_status", (dev_id,)))
        self._status_calls += 1
        stopped = self._status_calls >= self.stop_after_status_calls
        per_axis: list[int] = []
        for axis in range(3):
            left = self.home_countdown.get(axis, 0)
            if left > 0:
                self.home_countdown[axis] = left - 1
                per_axis.append(AXIS_HOME | AXIS_RUNNING)   # 回零中：home_done 被清零
            elif stopped:
                per_axis.append(AXIS_STOP | AXIS_HOME_DONE)
            else:
                per_axis.append(0)
        if self.home_overtime:
            per_axis = [AXIS_HOME_OVERTIME] * 3
        st = MachineStatusStruct(
            realPos=(ctypes.c_float * 3)(*self.pos),
            realSpeed=(ctypes.c_float * 3)(0.0, 0.0, 0.0),
            inputStatus=0,
            outputStatus=self.outputs,
            limitNStatus=0,
            limitPStatus=0,
            machineRunStatus=MODE_AUTO,
            axisStatus=(ctypes.c_int32 * 3)(*per_axis),
            homeStatus=0,
        )
        ctypes.memmove(buf, ctypes.addressof(st), ctypes.sizeof(st))
        return 0

    def FMC4030_Get_Device_Para(self, dev_id, buf):
        self.calls.append(("get_device_para", (dev_id,)))
        lo, hi = self.soft_limits
        st = DeviceParaStruct(
            id=1, bound232=115200, bound485=9600,
            ip=b"192.168.1.239", port=8088,
            div=(ctypes.c_int32 * 3)(1600, 1600, 1600),
            lead=(ctypes.c_int32 * 3)(40, 40, 20),
            softLimitMax=(ctypes.c_int32 * 3)(200, hi, hi),
            softLimitMin=(ctypes.c_int32 * 3)(200, lo, lo),
            homeTime=(ctypes.c_int32 * 3)(30000, 30000, 30000),
        )
        ctypes.memmove(buf, ctypes.addressof(st), ctypes.sizeof(st))
        return 0

    def FMC4030_Set_Device_Para(self, dev_id, ptr):
        self.calls.append(("set_device_para", (dev_id,)))
        # 收到的是 byref(...) 的 CArgObject，要先 cast 成结构体指针才能读字段
        para = ctypes.cast(ptr, ctypes.POINTER(DeviceParaStruct)).contents
        self.written_soft_limits = {
            axis: (int(para.softLimitMin[axis]), int(para.softLimitMax[axis]))
            for axis in range(3)
        }
        return 0

    def FMC4030_Check_Axis_Is_Stop(self, dev_id, axis):
        self.calls.append(("check_stop", (dev_id, axis)))
        return 0 if axis in self.busy_axes else 1

    def FMC4030_Home_Single_Axis(self, dev_id, axis, speed, accdec, release, direction):
        self.calls.append(("home", (dev_id, axis, speed, accdec, release, direction)))
        self.home_countdown[axis] = self.home_status_calls
        return 0

    def FMC4030_Line_2Axis(self, dev_id, axis, end_x, end_y, speed, acc, dec):
        self.calls.append(("line2", (dev_id, axis, end_x, end_y, speed, acc, dec)))
        # 虚拟坐标按掩码选中的轴（升序）依次填入，故 Y+Z(0x06) 的 end_x→Y、end_y→Z
        slots = [i for i in range(3) if axis & (1 << i)]
        p = list(self.pos)
        for slot, value in zip(slots, (end_x, end_y), strict=True):
            p[slot] = value
        self.pos = tuple(p)
        return self.line_rc

    def FMC4030_Jog_Single_Axis(self, dev_id, axis, pos, speed, acc, dec, mode):
        self.calls.append(("jog", (dev_id, axis, pos, speed, acc, dec, mode)))
        # mode 2=绝对（pos 即目标坐标），mode 1=相对（在当前位置上叠加 pos）
        if mode == 2:
            new_pos = pos
        else:
            new_pos = self.pos[axis] + pos
        p = list(self.pos)
        p[axis] = new_pos
        self.pos = tuple(p)
        return 0

    def FMC4030_Stop_Single_Axis(self, dev_id, axis, mode):
        self.calls.append(("stop_single", (dev_id, axis, mode)))
        return self.stop_single_rc

    def FMC4030_Stop_Run(self, dev_id):
        self.calls.append(("stop_run", (dev_id,)))
        return self.stop_run_rc

    def FMC4030_Set_Output(self, dev_id, io, status):
        self.calls.append(("set_output", (dev_id, io, status)))
        if status:
            self.outputs |= 1 << io
        else:
            self.outputs &= ~(1 << io)
        return 0

    def FMC4030_Download_File(self, dev_id, path, file_type):
        self.calls.append(("download", (dev_id, path, file_type)))
        return 0

    def FMC4030_Start_Auto_Run(self, dev_id, name):
        self.calls.append(("start_script", (dev_id, name)))
        return 0

    def FMC4030_Stop_Auto_Run(self, dev_id):
        self.calls.append(("stop_script", (dev_id,)))
        return 0

    # --- 辅助 ---

    def calls_of(self, name: str) -> list[tuple]:
        return [params for fn, params in self.calls if fn == name]


#: 已整定的控制器软限位（`FMC4030_Get_Device_Para` 的**原值**）。
#: 判据是"软限位不得窄于机械行程"（`Fmc4030.soft_limit_issues`，Y 0…4492、Z 0…212）。
#: 出厂默认 (200, 200) 即 ±200mm，会让 Y 的长行程被控制器**静默截断**——那是"没整定"，
#: 不是现场状态：实机读回的是 Y=[0, 4495]（2026-09-12 记录），与这里的 (212, 4600) 同类。
COMMISSIONED_SOFT_LIMITS = (212, 4600)


def commissioned_client(**kwargs):
    """一个**已整定**的假控制器。

    `PatrolDaemon` 每次连接后都会 `check_soft_limits()`（ADR-0008），所以凡是走 daemon
    的测试都必须用整定过的假件；用出厂默认会让每一轮都判"软限位未整定"而失败——
    测出来的是假件的状态，不是被测逻辑。
    """
    from patrol.fmc import Fmc4030

    kwargs.setdefault("soft_limits", COMMISSIONED_SOFT_LIMITS)
    return Fmc4030(FakeFmcLib(**kwargs))


def make_status_bytes(*, pos=(0.0, 0.0, 0.0), outputs=0, run_mode=MODE_AUTO,
                      axis_flags=AXIS_STOP | AXIS_HOME_DONE,
                      speeds=(0.0, 0.0, 0.0)) -> bytes:
    """构造一段 machine_status 原始缓冲区（供解析测试）。

    ``speeds`` 供「停稳确认」用：running 位清零 ≠ 运动结束，实测那一瞬 `realSpeed`
    还有 10.5 mm/s，所以假状态必须能给出速度，否则那条判据测不到。
    """
    st = MachineStatusStruct(
        realPos=(ctypes.c_float * 3)(*pos),
        realSpeed=(ctypes.c_float * 3)(*speeds),
        inputStatus=0,
        outputStatus=outputs,
        limitNStatus=0,
        limitPStatus=0,
        machineRunStatus=run_mode,
        axisStatus=(ctypes.c_int32 * 3)(axis_flags, axis_flags, axis_flags),
        homeStatus=0,
    )
    return bytes(memoryview(st).cast("B"))
