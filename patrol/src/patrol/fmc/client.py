"""FMC4030 客户端：非阻塞 SDK 封装。

用法（真机）::

    from patrol.fmc import Fmc4030
    fmc = Fmc4030.connect()          # 默认 motion_profile.CONTROLLER_IP / PORT
    fmc.home_all(); fmc.wait_stop()  # 两轴都找负限位（Y 往左、Z 往上，见 ADR-0018）
    fmc.goto(1200.0, 120.0)          # 目标 (y, z) mm
    fmc.lamp(True)
    fmc.close()

本机两轴为 **Y（轴 1）与 Z（轴 2）**，插补走组合号 0x06（X+Y=0x03、X+Z=0x05、
Y+Z=0x06，见《FMC4030二次开发库详解》Line_2Axis）。坐标原点即「两轴都向**负限位**
（= 靠近电机的那一端）回零」后的落点：Y 在左端、Z 在顶端。

行程、速度、加减速、回零方向、控制器 IP 全部取自运动参数单源
``patrol.motion_profile``（ADR-0002 / ADR-0007），本文件不持有任何物理常量。

测试注入假库对象（实现与 SDK 同名函数即可），见 tests/fmc_fakes.py。
"""

from __future__ import annotations

import ctypes
import time
from collections.abc import Callable, Iterable
from functools import partial
from typing import Self

from patrol.fmc.device_para import (
    DevicePara,
    DeviceParaStruct,
    SoftLimitIssue,
    build_device_para,
    parse_device_para,
)
from patrol.fmc.errors import (
    FmcError,
    HomeTimeoutError,
    MotionAborted,
    MotionTimeoutError,
    SoftLimitMismatchError,
    TravelLimitError,
    TravelShortfallError,
)
from patrol.fmc.geometry import Point, approach_point, composite_limits, segment_delta
from patrol.fmc.status import MachineStatus, MachineStatusStruct, parse_machine_status
from patrol.motion_profile import CONTROLLER_IP, CONTROLLER_PORT, DEVICE_ID, M1

Limits = tuple[float, float]


def _check_abort(abort: Callable[[], bool] | None, what: str) -> None:
    """有人在等的时候要求停下（急停）——立刻抛，别把"马上停"拖成"等它跑完"。

    ``abort`` 由上层注入（执行方传的是"急停标志文件还在不在"）。判断本身不做任何
    阻塞动作：**停下是调用方的事**（它才知道要不要 ``stop_everything``）。
    """
    if abort is not None and abort():
        raise MotionAborted(what)

# 「起转窗口」：指令下发后，控制器开始处理之前的一小段时间。这段时间里读到的
# 一切位置/状态标志都还是**上一条指令的残值**，不是这次动作的结果。
# 现场实测（2026-09-12，Y 轴 +200mm 点动，50Hz 采样）：
#   t=0.001s  Check_Axis_Is_Stop=1（"已停"）  状态字 running 也可能是 False
#   t=0.033s  Check_Axis_Is_Stop=0（运动中）
#   t=5.277s  真正到位
# 回零同理：`home_done` 在回零期间会被控制器清零（实测 raw 0x0648 → 0x0681），
# 但**起转之前它仍是上一轮的值**——而 `home_done` 表示"坐标系已建立"，普通点动
# 不会把它作废（实测点动到 100mm 后仍为 True），所以它很容易被误当成"这次已回零"。
# 结论：等"停"和等"回零"都必须先确认**动作已起转**，否则会在起转窗口内直接返回。
START_GRACE_S = 0.3          # 未观察到起转时，"已停"需连续成立多久才认账
HOME_START_TIMEOUT_S = 3.0   # 回零起转的确认窗口（实测起转发生在 22ms）

# 「停稳」确认：running 位清零 ≠ 运动结束。实测 running 清零那一刻 `realSpeed` 还有
# 10.5 mm/s（减速尾巴未走完），此时下发同轴指令会得到 `接收数据错误 -7`、读位置会拿到
# 未收敛值。所以再等速度低于门槛并保持 SETTLE_S 才认停稳。
SETTLE_SPEED_MM_S = 1.0
SETTLE_S = 0.2

# 到位容差：等待停稳之后再比一次"指令 vs 控制器计数"。
#
# ⚠️ **这不是位置精度保证**：FMC4030 无位置反馈，`real_pos` 是脉冲自述，所以本校验
# 只能抓"控制器计数与指令不符"这一类故障（实测能到 4300 mm）。若控制器坚信自己走到了
# （计数=指令），校验会通过——哪怕滑块物理上在别处。对中途位置，它给不了独立保证。
# 现场实测：正常落点 0.000–0.019 mm；但短程段也见过一次 −2.0 mm（且该趟耗时异常），
# 所以 0.5 mm 是"抓大偏差"的工程取值，不是精度指标。
ARRIVAL_TOL_MM = 0.5


class Fmc4030:
    """对厂商 SDK 的薄封装；所有 SDK 负返回值转 FmcError。"""

    def __init__(
        self,
        lib,
        device_id: int = DEVICE_ID,
        ip: str = CONTROLLER_IP,
        port: int = CONTROLLER_PORT,
    ) -> None:
        self._lib = lib
        self.id = device_id
        self.ip = ip
        self.port = port
        self._open = False

    # ---------- 连接管理 ----------

    @classmethod
    def connect(
        cls,
        lib=None,
        device_id: int = DEVICE_ID,
        ip: str = CONTROLLER_IP,
        port: int = CONTROLLER_PORT,
    ) -> Fmc4030:
        if lib is None:
            from patrol.fmc.loader import load_library

            lib = load_library()
        client = cls(lib, device_id, ip, port)
        client.open()
        return client

    def open(self) -> None:
        rc = self._lib.FMC4030_Open_Device(self.id, self.ip.encode(), self.port)
        self._check(rc, "open")
        self._open = True

    def close(self) -> None:
        """退出前必须调用，否则下次连接失败（厂商文档要求）。"""
        if self._open:
            rc = self._lib.FMC4030_Close_Device(self.id)
            self._open = False
            self._check(rc, "close")

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ---------- 状态 ----------

    def get_status(self) -> MachineStatus:
        buf = (ctypes.c_ubyte * ctypes.sizeof(MachineStatusStruct))()
        self._check(self._lib.FMC4030_Get_Machine_Status(self.id, buf), "get_status")
        return parse_machine_status(bytes(buf))

    def current_yz(self) -> Point:
        """两轴当前实际坐标 (y, z) mm。控制器仍是 3 轴状态字，未接线的 X 被丢弃。"""
        pos = self.get_status().real_pos
        return (pos[M1.y.index], pos[M1.z.index])

    # ---------- 软限位 ----------

    def check_travel(self, y: float, z: float) -> None:
        """校验两轴目标坐标是否在机械行程内；越界抛 TravelLimitError（不下发控制器）。

        只作用于**绝对**目标；相对点动（``jog``）的落点要读状态才知道，由调用方
        保证（M0 是人工逐次微调，现场看得见）。
        """
        for spec, pos in zip(M1.axes, (y, z), strict=True):
            if not spec.contains(pos):
                raise TravelLimitError(spec.name, pos, spec.travel_min, spec.travel_max)

    def check_axis_travel(self, axis: int, pos: float) -> None:
        spec = M1.by_index(axis)
        if not spec.contains(pos):
            raise TravelLimitError(spec.name, pos, spec.travel_min, spec.travel_max)

    # ---------- 控制器整定参数（软限位等） ----------

    def get_device_para(self) -> DevicePara:
        """读回控制器设备参数（导程/细分/软限位/回零超时）。"""
        buf = (ctypes.c_ubyte * ctypes.sizeof(DeviceParaStruct))()
        self._check(self._lib.FMC4030_Get_Device_Para(self.id, buf), "get_device_para")
        return parse_device_para(bytes(buf))

    def set_device_para(self, para: DevicePara) -> None:
        """整体写回设备参数（read-modify-write：先 get、改完再 set）。

        厂商文档明确"请勿随意修改，避免造成设备运行错误导致设备损坏"，因此本方法
        只应由显式的整定流程调用，不在巡检/控制路径上。
        """
        struct = build_device_para(para)
        self._check(self._lib.FMC4030_Set_Device_Para(self.id, ctypes.byref(struct)),
                    "set_device_para")

    def soft_limit_issues(self, para: DevicePara | None = None) -> list[SoftLimitIssue]:
        """返回控制器软限位**窄于**机械行程的轴（空列表 = 一致）。

        控制器的软限位是独立于本程序的一层保护：出厂默认 ±200mm（说明书 §三.4）。
        Y 行程 0…4492mm，若未整定，控制器会把 Y 目标自行截断到 200mm 且**返回成功**，
        业务层完全看不出来。符号约定与"取消"的判定见 ``DevicePara.effective_limits``。
        """
        para = para if para is not None else self.get_device_para()
        issues: list[SoftLimitIssue] = []
        for spec in M1.axes:
            eff = para.effective_limits(spec.index)
            if eff is None:
                continue  # 已取消软限位 → 交给本程序的 check_travel 管
            lo, hi = eff
            if lo > spec.travel_min + 1e-6 or hi < spec.travel_max - 1e-6:
                issues.append(
                    SoftLimitIssue(
                        axis_name=spec.name,
                        axis=spec.index,
                        controller_min=lo,
                        controller_max=hi,
                        travel_min=spec.travel_min,
                        travel_max=spec.travel_max,
                    )
                )
        return issues

    def check_soft_limits(self, para: DevicePara | None = None) -> None:
        """巡检/自动运行前调用：软限位与行程不一致即抛 SoftLimitMismatchError。"""
        issues = self.soft_limit_issues(para)
        if issues:
            raise SoftLimitMismatchError(issues)

    # ---------- 运动 ----------

    def home_axis(
        self,
        axis: int,
        *,
        speed: float | None = None,
        accdec: float | None = None,
        release: float | None = None,
        direction: int | None = None,
    ) -> None:
        """单轴回零；省略的参数按该轴整定值填入（方向：1=正限位、2=负限位）。"""
        spec = M1.by_index(axis)
        self._check(
            self._lib.FMC4030_Home_Single_Axis(
                self.id,
                axis,
                spec.home_speed if speed is None else speed,
                spec.home_acc if accdec is None else accdec,
                spec.home_release if release is None else release,
                spec.home_dir if direction is None else direction,
            ),
            f"home_axis({axis})",
        )

    def home_all(
        self,
        axes: Iterable[int] | None = None,
        *,
        wait: bool = True,
        timeout_s: float | None = None,
        poll_s: float = 0.2,
        abort: Callable[[], bool] | None = None,
    ) -> None:
        """两轴依次回零（默认本机接线轴 Y、Z），**默认等待回零完成**。

        默认等待是刻意的：回零未完成时的当前位置不是有效坐标系原点，此时下发绝对
        坐标会整体偏移。厂商手册也提醒回零可能因"长时间未触发限位开关"被控制器
        自行终止——那属于 `HomeTimeoutError`，绝不能当成"回到原点了"继续跑。
        """
        targets = M1.axis_indices if axes is None else tuple(axes)
        for axis in targets:
            _check_abort(abort, f"回零（{M1.by_index(axis).name} 轴下发前）")
            self.home_axis(axis)
        if not wait:
            return
        timeout = M1.home_timeout if timeout_s is None else timeout_s
        if not self.wait_home(targets, timeout_s=timeout, poll_s=poll_s, abort=abort):
            names = "、".join(M1.by_index(a).name for a in targets)
            raise MotionTimeoutError(f"回零到位确认超时（{names}，{timeout:g}s 内未完成）")

    def wait_home(
        self,
        axes: Iterable[int] | None = None,
        *,
        timeout_s: float | None = None,
        poll_s: float = 0.2,
        start_timeout_s: float = HOME_START_TIMEOUT_S,
        abort: Callable[[], bool] | None = None,
    ) -> bool:
        """等待各轴回零完成；返回 False 表示主机侧等待超时。

        与 ``wait_stop`` 的区别：回零有独立的失败态——控制器若在 ``homeTime``（设备
        参数，ms）内没等到限位开关会自行终止回零，并置位「轴回零超时」。这种情况
        立即抛 `HomeTimeoutError`，不必再等满超时。

        **两段式等待**：先逐轴确认"回零已起转"，再等 ``homed``。不能直接等 ``homed``——
        ``home_done`` 的语义是"坐标系已建立"，**普通运动不会把它作废**（实测：Y 轴点动
        到 100 mm 后仍为 True，见 ``START_GRACE_S`` 注释），所以回零刚下发、控制器还没
        起转时读到的是**上一轮的残值**，据此返回会让调用方以为"已回到原点"而继续下发
        绝对坐标（整体偏移）。

        起转判据：``homing`` 置起**或** ``home_done`` 被清零——实测一个完整回零周期里
        两者都会发生（raw 0x0648 → 0x0681 → … → 0x0648）。窗口 ``start_timeout_s``
        内没观察到起转即返回 False（上报为超时，而不是静默当作成功）。
        """
        targets = M1.axis_indices if axes is None else tuple(axes)
        timeout = M1.home_timeout if timeout_s is None else timeout_s
        deadline = time.monotonic() + timeout
        start_deadline = time.monotonic() + start_timeout_s
        started = dict.fromkeys(targets, False)
        while True:
            _check_abort(abort, "等待回零")
            st = self.get_status()  # 顺带充当 1 分钟无交互断连的保活
            overtime = [a for a in targets if st.axes[a].home_overtime]
            if overtime:
                raise HomeTimeoutError("、".join(M1.by_index(a).name for a in overtime))
            for a in targets:
                if not started[a]:
                    started[a] = st.axes[a].homing or not st.axes[a].home_done
            if all(started.values()) and all(st.axes[a].homed for a in targets):
                return True
            if not all(started.values()) and time.monotonic() >= start_deadline:
                return False       # 连起转都没确认，绝不当作"已回零"
            if time.monotonic() >= deadline:
                return False
            time.sleep(poll_s)

    def check_stop(self, axis: int) -> bool:
        return int(self._lib.FMC4030_Check_Axis_Is_Stop(self.id, axis)) == 1

    def wait_stop(self, axes: Iterable[int] | None = None, *, timeout_s: float = 60.0,
                  poll_s: float = 0.05, start_grace_s: float = START_GRACE_S,
                  settle_s: float = SETTLE_S, settle_speed: float = SETTLE_SPEED_MM_S,
                  settle_poll_s: float = 0.02,
                  abort: Callable[[], bool] | None = None) -> bool:
        """轮询等待轴**真正停稳**；超时返回 False。默认等本机两轴（Y、Z）。

        ## 为什么不能只看 running 位（2026-09-13 实测）

        判据用状态字的 ``running`` 位（不用 ``Check_Axis_Is_Stop``：后者在指令下发后
        约 30 ms 内仍返回"已停"）。但**running 清零并不等于运动结束**：实测 Y 轴
        374 mm 移动中，wait_stop 在看到 running 清零后立刻返回，而那一刻
        ``realSpeed`` 还有 **10.5 mm/s**（尚未走完减速尾巴）。

        后果实测到了两条，都很坏：

        * 紧接着下发的**同轴**指令撞上未静定的控制器 → ``接收数据错误 (code=-7)``
          （`goto` 的接近段就是这么失败的）；
        * 此刻读位置会拿到**尚未收敛的值**，到位校验据此误报"未完成"
          （实测见过 1.8 mm / 238.9 mm 这种毫无意义的读数）。

        于是这里在 running 清零之后**再等速度归零并保持 ``settle_s``**，才认"停稳"。

        ## 起转握手（保留）

        只有**见过 running**才认"已停"；若一次都没见到起转（极短的点动可能整个运动
        都落在两次轮询之间），要求"已停"连续成立 ``start_grace_s`` 才算数。
        """
        targets = M1.axis_indices if axes is None else tuple(axes)
        deadline = time.monotonic() + timeout_s
        seen_running = False
        stopped_since: float | None = None
        # 阶段 2：running 已清零后的"速度归零"确认
        quiet_since: float | None = None
        while time.monotonic() < deadline:
            _check_abort(abort, "等待到位")
            st = self.get_status()
            busy = any(st.axes[a].running for a in targets)
            fastest = max((abs(st.real_speed[a]) for a in targets), default=0.0)
            now = time.monotonic()
            if busy:
                seen_running = True
                stopped_since = None
                quiet_since = None
            elif seen_running or (stopped_since is not None
                                  and now - stopped_since >= start_grace_s):
                seen_running = True
                # running 已清零：还要等速度降到门槛以下并保持住
                if fastest > settle_speed:
                    quiet_since = None
                elif quiet_since is None:
                    quiet_since = now
                elif now - quiet_since >= settle_s:
                    return True
            elif stopped_since is None:
                stopped_since = now
            time.sleep(settle_poll_s if seen_running else poll_s)
        return False

    def read_settled_position(self, axis: int, *, attempts: int = 5,
                              retry_s: float = 0.15) -> float:
        """读取某轴的**已静定**位置；读失败（如 -7）重试几次。

        为什么需要：控制器在刚结束运动时会短暂拒绝/出错（实测 ``接收数据错误 -7``），
        单次读取可能拿到错误或过渡值。到位校验必须基于一个"读得到、且读得稳"的值，
        否则就会诞生那种 238.9 mm 的假读数。
        """
        last: Exception | None = None
        for _ in range(max(1, attempts)):
            try:
                return float(self.get_status().real_pos[axis])
            except FmcError as e:      # -7 之类：稍等再读
                last = e
                time.sleep(retry_s)
        raise last  # type: ignore[misc]

    def goto(
        self,
        y: float,
        z: float,
        *,
        from_point: Point | None = None,
        approach_offset: float | None = None,
        speed: float | None = None,
        acc: float | None = None,
        approach_speed: float | None = None,
        approach_acc: float | None = None,
        timeout_s: float = M1.travel_timeout,
        abort: Callable[[], bool] | None = None,
    ) -> None:
        """两段速到达 (y, z)：**巡检段**全速走空程，**接近段**降速走最后 offset mm。

        段序由 ``approach_point`` 的语义决定：它返回「距 target 沿来向退 offset mm」
        的点，于是 ``start → mid`` 是空程（该全速），``mid → target`` 才是最后那
        几毫米（该降速防过冲）。**两段的限值曾写反**——空程按接近档爬行（单轴 50mm/s
        的机器上整轮时长 37 min vs 9.5 min），最后 5 mm 反而全速冲到位，正是接近段要
        避免的事。见 ADR-0007「补充（2026-09-12）：巡检档整定与两段速段序」。

        没有接近段时（``offset=0``，或来向距离 ≤ offset）整段就是空程，按**巡检档**走：
        接近档的意义只在于"最后几毫米"。

        ``speed`` / ``acc`` 等显式参数一旦给出，即作为该段的**合成**量原样下发
        （M0 覆盖语义）；省略时按 ``composite_limits`` 由两轴上限折算，长轴主导的
        行程仍能跑满长轴目标速度，同时不让短轴超速。

        到位确认由本方法负责；超时抛 MotionTimeoutError，调用方无需再 wait_stop。
        """
        self.check_travel(y, z)
        _check_abort(abort, "巡检段（下发前）")
        target = (y, z)
        start = from_point if from_point is not None else self.current_yz()
        offset = M1.approach_offset if approach_offset is None else approach_offset
        mid = approach_point(start, target, offset)
        if mid != target:
            self._goto_segment(start, mid, M1.travel_limits, speed, acc, "巡检段", timeout_s,
                               abort=abort)
            return self._goto_segment(
                mid, target, M1.approach_limits, approach_speed, approach_acc,
                "接近段", timeout_s, abort=abort,
            )
        return self._goto_segment(
            start, target, M1.travel_limits, speed, acc, "巡检段", timeout_s, abort=abort
        )

    def _goto_segment(
        self,
        start: Point,
        end: Point,
        limits: tuple[Limits, ...],
        speed: float | None,
        acc: float | None,
        label: str,
        timeout_s: float,
        *,
        abort: Callable[[], bool] | None = None,
    ) -> None:
        """下发一段绝对运动并等它停稳（``goto`` 的两段共用）。"""
        _check_abort(abort, f"{label}（下发前）")
        v, a = self._segment_limits(segment_delta(start, end), limits, speed, acc)
        self._check(
            self._lib.FMC4030_Line_2Axis(self.id, M1.axis_mask, end[0], end[1], v, a, a),
            f"goto:{label}",
        )
        if not self.wait_stop(timeout_s=timeout_s, abort=abort):
            raise MotionTimeoutError(f"{label}到位确认超时 ({end[0]}, {end[1]})")
        self._verify_arrival(end, label)

    def _verify_arrival(self, target: Point, label: str,
                        tol: float = ARRIVAL_TOL_MM) -> None:
        """停稳后核对"指令 vs 控制器计数"。**这一步是必需的，但不是位置保证**。

        实测（2026-09-13）：`Line_2Axis` 指令 Y 走 4442 mm，而控制器自己的计数最终是
        1435 mm——running 位正常清零、"已停"正常上报、**没有任何错误码**。不主动比这两者，
        这件事就完全不可见，相机便可能在错误的位置拍图并照写该站位编号。

        **能抓什么、抓不到什么**（别把它当精度保证）：

        * 抓得到：控制器计数 ≠ 指令（如上面那 4300 mm 的偏差），以及由此导致的
          "实际移动距离不是你要的距离"。
        * 抓不到：控制器**坚信**自己到位（计数=指令）时的物理偏差。本机无编码器，
          行程中间的物理位置没有独立参照——唯一的物理基准是两端硬限位（回零用）。

        只校验**已接线轴**（本机 Y/Z）；未接线的轴读数无意义。

        **读数走 `read_settled_position`**：控制器刚结束运动时可能短暂报 -7，
        单次读取会拿到错误或过渡值——实测就诞生过 1.8 mm / 238.9 mm 这种假偏差。
        """
        for spec, want in zip(M1.axes, target, strict=True):
            try:
                got = self.read_settled_position(spec.index)
            except FmcError as e:
                raise FmcError(0, f"{label}到位校验读数失败（{spec.name}）: {e}") from e
            if abs(want - got) > tol:
                raise TravelShortfallError(spec.name, want, got, tol)

    @staticmethod
    def _segment_limits(
        delta: Point,
        limits: tuple[Limits, ...],
        speed: float | None,
        acc: float | None,
    ) -> Limits:
        """一段运动的合成速度/加速度：显式值优先，否则按方向余弦折算。"""
        v_comp, a_comp = composite_limits(delta, limits)
        return (v_comp if speed is None else speed, a_comp if acc is None else acc)

    def jog(
        self,
        axis: int,
        dist: float,
        *,
        speed: float | None = None,
        acc: float | None = None,
        dec: float | None = None,
    ) -> None:
        """单轴点动（M0）：以当前坐标为基准，沿轴移动 dist mm（可为负）。

        相对运动（FMC4030_Jog_Single_Axis mode=1）。不等待到位——点动常配合
        反复微调，是否阻塞由调用方决定（参见 M0 CLI 的即时反馈）。
        """
        spec = M1.by_index(axis)
        self._check(
            self._lib.FMC4030_Jog_Single_Axis(
                self.id, axis, dist,
                spec.jog_speed if speed is None else speed,
                spec.jog_acc if acc is None else acc,
                spec.jog_acc if dec is None else dec,
                1,
            ),
            f"jog({axis}, dist={dist})",
        )

    def move_axis(
        self,
        axis: int,
        pos: float,
        *,
        speed: float | None = None,
        acc: float | None = None,
        dec: float | None = None,
        timeout_s: float = M1.jog_timeout,
        abort: Callable[[], bool] | None = None,
    ) -> None:
        """单轴绝对定位（M0）：把 axis 轴移动到绝对坐标 pos mm。

        绝对运动（mode=2）。到位前先做行程校验；等待到位，超时抛 MotionTimeoutError。
        """
        spec = M1.by_index(axis)
        self.check_axis_travel(axis, pos)
        _check_abort(abort, f"定位（{spec.name} 轴下发前）")
        self._check(
            self._lib.FMC4030_Jog_Single_Axis(
                self.id, axis, pos,
                spec.jog_speed if speed is None else speed,
                spec.jog_acc if acc is None else acc,
                spec.jog_acc if dec is None else dec,
                2,
            ),
            f"move_axis({axis}, pos={pos})",
        )
        if not self.wait_stop(axes=(axis,), timeout_s=timeout_s, abort=abort):
            raise MotionTimeoutError(f"单轴到位确认超时 (axis={axis}, pos={pos})")
        actual = self.get_status().real_pos[axis]
        if abs(pos - actual) > ARRIVAL_TOL_MM:
            raise TravelShortfallError(spec.name, pos, actual, ARRIVAL_TOL_MM)

    def goto_2axis(
        self,
        y: float,
        z: float,
        *,
        speed: float | None = None,
        acc: float | None = None,
        dec: float | None = None,
        timeout_s: float = M1.jog_timeout,
        abort: Callable[[], bool] | None = None,
    ) -> None:
        """双轴直线插补绝对定位（M0）：单段直达虚拟坐标 (y, z) mm。

        M0 手动调试用：省略速度/加/减速时按两轴的 M0（点动档）上限折算合成量，
        单段到位，不做 M1 的两段降速。
        """
        self.check_travel(y, z)
        _check_abort(abort, "双轴定位（下发前）")
        v_default, a_default = composite_limits(
            segment_delta(self.current_yz(), (y, z)), M1.jog_limits
        )
        v = v_default if speed is None else speed
        a = a_default if acc is None else acc
        d = a_default if dec is None else dec
        self._check(
            self._lib.FMC4030_Line_2Axis(self.id, M1.axis_mask, y, z, v, a, d),
            "goto_2axis",
        )
        if not self.wait_stop(timeout_s=timeout_s, abort=abort):
            raise MotionTimeoutError(f"双轴到位确认超时 ({y}, {z})")
        self._verify_arrival((y, z), "goto_2axis")

    def stop_axis(self, axis: int, *, immediate: bool = False) -> None:
        """停止单轴（mode=1 减速 / mode=2 立即）。仅用于单轴运动（Jog 相对/绝对）。"""
        mode = 2 if immediate else 1
        self._check(self._lib.FMC4030_Stop_Single_Axis(self.id, axis, mode), f"stop_axis({axis})")

    def stop_all(self) -> None:
        """插补立即停止；配合 stop_axis(immediate=True) 才是完整急停。"""
        self._check(self._lib.FMC4030_Stop_Run(self.id), "stop_all")

    def stop_everything(self) -> list[str]:
        """完整急停：先停插补，再逐轴立即停止；返回失败的动作名（空 = 全部送达）。

        顺序与容错都来自厂商手册：

        * ``Stop_Single_Axis``「只能用于启动单轴运行后停止，**不能用于插补运动时的
          停止**」——插补（``Line_2Axis``）必须先用 ``Stop_Run`` 停，所以顺序反过来；
        * 急停链路必须把每条指令都尽量送出去，不能因为前一条返回了错误码就中断，
          因此这里**收集**失败而不是抛出。调用方（CLI / console）把非空列表当告警上报。
        """
        actions: list[tuple[str, Callable[[], None]]] = [("stop_run", self.stop_all)]
        actions += [
            (f"stop_axis({axis})", partial(self.stop_axis, axis, immediate=True))
            for axis in M1.axis_indices
        ]
        failed: list[str] = []
        for name, action in actions:
            try:
                action()
            except FmcError:
                failed.append(name)
        return failed

    # ---------- IO ----------

    def set_output(self, io: int, on: bool) -> None:
        self._check(self._lib.FMC4030_Set_Output(self.id, io, 1 if on else 0), f"set_output({io})")

    def lamp(self, on: bool, *, io: int = 0) -> None:
        self.set_output(io, on)

    # ---------- 脚本（票 07 用） ----------

    def download_script(self, file_path: str) -> None:
        self._check(self._lib.FMC4030_Download_File(self.id, file_path.encode(), 2), "download_script")

    def start_script(self, name: str) -> None:
        self._check(self._lib.FMC4030_Start_Auto_Run(self.id, name.encode()), "start_script")

    def stop_script(self) -> None:
        self._check(self._lib.FMC4030_Stop_Auto_Run(self.id), "stop_script")

    # ---------- 内部 ----------

    def _check(self, rc: int, action: str) -> None:
        rc = int(rc)
        if rc < 0:
            raise FmcError(rc, action)
