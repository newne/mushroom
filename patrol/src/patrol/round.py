"""一轮巡检的完整生命周期（架构评审 #2 的 deep module）。

回零 → 站位循环 → 回原位 → **停在第一个格点** → RoundReport，全部收在一个 interface
（run()）后面。daemon 只负责何时触发、重连与 outbox；per-station 时序（orchestrator）
是其 implementation 的内部缝。

末尾那一步"停在第一个格点"不是美观问题：回原位落在导轨尽头，画面里是货架端头与顶棚，
操作者打开预览什么都看不见；停在第一个格点则是一个**有蘑菇的位置**，打开画面就能看长势
（2026-10-13 现场口径）。
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

from patrol.capture_client import CaptureClient, CaptureError
from patrol.fmc import Fmc4030, FmcError, MotionAborted
from patrol.fmc.errors import TravelShortfallError
from patrol.orchestrator import FramingHook, StationCapture
from patrol.stations import Station

MAX_CONSECUTIVE_FAILURES = 3  # 采图连续失败即中止（链路大概率断了）


class FmcRoundAbort(FmcError):
    """站位运动失败 ⇒ 本轮作废（运动失败是传输层面的事实，不是"这一站没拍成"）。

    单独一个类型是为了让调用方能区分"本轮被运动故障打断（该急停、记为 failed）"与
    "本轮跑完了但结果不完整（记为 partial）"——两者对现场的含义完全不同。
    原始 ``FmcError`` 的 SDK 错误码保留在 ``original_code``，并链在 ``__cause__`` 上。
    """

    def __init__(self, station_id: str, exc: BaseException) -> None:
        self.station_id = station_id
        self.original_code = getattr(exc, "code", None)
        self.detail = f"{station_id} 站位运动失败，本轮作废：{exc}"
        FmcError.__init__(self, code=0, action=self.detail)

    def __str__(self) -> str:
        return self.detail


@dataclass
class RoundReport:
    started_at: datetime
    ended_at: datetime | None = None
    results: list[dict] = field(default_factory=list)
    failures: list[dict] = field(default_factory=list)
    aborted: bool = False
    #: 中止的**原因**（人读一句话，如"加湿保持至 …"）。空串 = 正常结束或旧字段没记。
    #: 单独一列是为了让下游能区分"急停/运动失败"与"我们主动避让"——前者是故障，
    #: 后者是策略，混在一个 `aborted` 里现场会去修不该修的东西。
    abort_reason: str = ""

    @property
    def ok(self) -> bool:
        return not self.aborted and not self.failures


class PatrolRound:
    def __init__(
        self,
        fmc: Fmc4030,
        capture_client: CaptureClient,
        stations: list[Station],
        *,
        max_consecutive_failures: int = MAX_CONSECUTIVE_FAILURES,
        return_home: bool = True,
        #: 回原位之后**继续走到第一个格点**停着，让"打开预览"直接看到蘑菇而不是
        #: 导轨尽头的货架端头/顶棚。见模块 docstring。
        park_at_first: bool = True,
        station_capture: StationCapture | None = None,  # 内部缝：测试注入
        framing: FramingHook | None = None,             # 图像微调（第二步定位）
        abort: Callable[[], bool] | None = None,        # 急停请求：透给每一次运动
        #: 「整轮避让」策略：返回非空字符串 = **本轮就此停下**（后面站位不再动机构），
        #: 返回 None = 继续。用于加湿保持这类"现在不该拍"——判断在调用方（deploy 侧读
        #: hold.json），这里只执行。已经拍到的帧留在报告里：它们是停止**之前**的事实。
        should_stop: Callable[[], str | None] | None = None,
        sleep=time.sleep,
        log: Callable[[str], None] | None = None,
        on_station: Callable[..., None] | None = None,  # 逐站心跳（结构化，供取证）
        #: 停在第一个格点之后回调一次（取证用：轮次结束后"机器现在在哪"）。
        #: 与 on_station 分开：那是**逐站**心跳，会被数进"已过站"；停车是一次性事实。
        on_park: Callable[..., None] | None = None,
    ) -> None:
        self.fmc = fmc
        self.capture_client = capture_client
        self.stations = stations
        self.max_consecutive_failures = max_consecutive_failures
        self.return_home = return_home
        self.park_at_first = park_at_first
        self._station_capture = station_capture
        # 微调钩子原样透给 StationCapture：不传 = 只走"第一步"（推导坐标），
        # 行为与没有这个功能时完全一致。
        self.framing = framing
        self.abort = abort
        self.should_stop = should_stop
        self._sleep = sleep
        self._log_sink = log
        self._on_station = on_station
        self._on_park = on_park
    def run(self) -> RoundReport:
        """执行一轮：回零 → 逐站采图 → 回原位。

        **失败分级**（现场教训，见 ``patrol.journal`` 的由来）：

        * **运动失败**（``FmcError``，含 ``goto`` 的传输/控制器错误）→ 立即记录并
          **抛出**，本轮作废。同一个接线上所有站位都会同样失败，继续跑只会又动 48 次
          机构、又写 48 条无意义的失败。调用方（daemon）负责急停并记 failed。
        * **采图失败**（``CaptureError``）→ 记入 ``report.failures`` 并跳过，
          连续 ``max_consecutive_failures`` 次才中止（链路大概率断了）。
        * **回零失败**（``home_all`` 抛）→ 直接抛出，本轮没开始。

        ``log`` 是人看的行文，``on_station`` 是**结构化**的逐站心跳——被中断的那一轮，
        现场就靠它回答"走到哪一站了"。两者分开：日志文案会改，取证字段不该跟着改。
        """
        report = RoundReport(started_at=datetime.now())
        self.fmc.home_all(abort=self.abort)
        cap = self._station_capture or StationCapture(
            self.fmc, self.capture_client, sleep=self._sleep, framing=self.framing,
            abort=self.abort,
        )
        consecutive = 0
        total = len(self.stations)
        for i, st in enumerate(self.stations, 1):
            # 「整轮避让」（分工：判定在调用方、执行在这里）：保持生效时**不再动机构**——
            # 加湿期的帧过曝、对比度塌（§12），拍完整轮只是白动机架 8 分钟。
            # 已经拍到的帧留在报告里：它们是停止之前的事实，不是坏数据。
            hold = self.should_stop() if self.should_stop is not None else None
            if hold:
                report.aborted = True
                report.abort_reason = str(hold)
                self._log(f"[{i}/{total}] 收到避让信号（{hold}）——本轮就此停下，"
                          f"后面 {total - i + 1} 站不再动机构")
                self._heartbeat(
                    i, total, station_id=st.id, box_id=st.box_id, ok=False,
                    elapsed_s=0.0, object_name=None, error=str(hold), abort="hold",
                )
                break
            t0 = time.monotonic()
            try:
                meta = cap.run(st)
            except MotionAborted as e:
                # **急停请求**：它不是"这一站没走对"，而是有人要求整轮停下。单独分类，
                # 以免现场在日志里看到"运动失败"而去找导轨的毛病（找错方向最费时间）。
                report.aborted = True
                report.failures.append(
                    {"station_id": st.id, "box_id": st.box_id, "error": str(e)}
                )
                self._log(f"[{i}/{total}] 收到急停请求，中止本轮：{e}")
                self._heartbeat(
                    i, total, station_id=st.id, box_id=st.box_id, ok=False,
                    elapsed_s=round(time.monotonic() - t0, 2), object_name=None,
                    error=str(e), abort="estop",
                )
                raise
            except TravelShortfallError as e:
                # **这一次移动没有按指令完成**——跳过该站，继续下一站（与采图失败同级）。
                # 依据：实测它不具预测性（20 趟里 2 趟，异常后的下一趟通常正常），而且
                # 回零始终能把坐标系拉回硬限位（落点稳定 0.000），所以后续站位的绝对定位
                # 仍有意义。与 FmcError（传输/控制器层面的事实 ⇒ 48 站都会同样失败）区别对待。
                #
                # ⚠️ 这里**不做**"坐标系没漂移"的断言：控制器无位置反馈，我们无从知道
                # 滑块物理上停在哪里（见 `TravelShortfallError` 的证据边界说明）。
                report.failures.append(
                    {"station_id": st.id, "box_id": st.box_id, "error": str(e)}
                )
                consecutive += 1
                elapsed = round(time.monotonic() - t0, 2)
                self._log(f"[{i}/{total}] {st.id} 未走到目标，跳过（连续 {consecutive}）"
                          f"{elapsed:.1f} s: {e}")
                self._heartbeat(
                    i, total, station_id=st.id, box_id=st.box_id, ok=False,
                    elapsed_s=elapsed, object_name=None, error=str(e),
                    abort="shortfall" if consecutive >= self.max_consecutive_failures else None,
                )
                if consecutive >= self.max_consecutive_failures:
                    report.aborted = True
                    self._log(f"连续 {consecutive} 站没走对，中止本轮")
                    break
                continue
            except FmcError as e:
                # **运动失败立即中止本轮**（与采图失败区别对待）。
                # 采图失败是"这一站没拍成"，下一站还有意义；而运动失败是传输/控制器
                # 层面的事实——同一个接线上所有站都会同样失败，继续跑只会又动 48 次
                # 机构、又留下 48 条无意义的失败记录。抛出去交给 daemon：它负责急停并
                # 把本轮记为 failed（现场教训见 ADR-0011 与 patrol.journal 的由来）。
                elapsed = round(time.monotonic() - t0, 2)
                report.failures.append(
                    {"station_id": st.id, "box_id": st.box_id, "error": str(e)}
                )
                report.aborted = True
                self._log(f"[{i}/{total}] {st.id} 运动失败，中止本轮（{elapsed:.1f} s）: {e}")
                self._heartbeat(
                    i, total, station_id=st.id, box_id=st.box_id, ok=False,
                    elapsed_s=elapsed, object_name=None, error=str(e), abort="motion",
                )
                raise FmcRoundAbort(st.id, e) from e
            except CaptureError as e:
                report.failures.append(
                    {"station_id": st.id, "box_id": st.box_id, "error": str(e)}
                )
                consecutive += 1
                elapsed = round(time.monotonic() - t0, 2)
                self._log(f"[{i}/{total}] {st.id} 失败（连续 {consecutive}）"
                          f"{elapsed:.1f} s: {e}")
                self._heartbeat(
                    i, total, station_id=st.id, box_id=st.box_id, ok=False,
                    elapsed_s=elapsed, object_name=None, error=str(e),
                    abort="capture" if consecutive >= self.max_consecutive_failures else None,
                )
                if consecutive >= self.max_consecutive_failures:
                    report.aborted = True
                    self._log(f"连续失败 {consecutive} 次，中止本轮（链路大概率断了）")
                    break
                continue
            report.results.append(meta)
            consecutive = 0
            self._log(
                f"[{i}/{total}] {st.id} 完成 "
                f"{meta.get('elapsed_s', 0.0):.1f} s（{meta.get('object_name', '-')}）"
            )
            self._heartbeat(
                i, total, station_id=st.id, box_id=st.box_id, ok=True,
                elapsed_s=meta.get("elapsed_s"),
                object_name=meta.get("object_name"), error=None,
            )
        if self.return_home:
            # 回原位同样可被打断：急停之后**不能**再往原点走——那正是"要求停下"的
            # 反面。abort 会在下发前就抛，不会有半段运动。
            self.fmc.goto(0.0, 0.0, abort=self.abort)
            # 回原位的落点是导轨尽头，画面里只有货架端头与顶棚——操作者打开预览
            # 什么都看不见。于是回原位之后**继续走到第一个格点**停着：那是一个有蘑菇
            # 的位置，打开画面就能看长势（2026-10-13 现场口径）。
            # 只在本轮**正常跑完**时停过去：中途急停/运动失败之后机器状态不明，
            # 不该再自作主张多动一次。也不在 return_home=False 时停——那条路是
            # "这一轮不要收尾"（测试与排障用），停到格点去同样多余。
            if self.park_at_first and not report.aborted and self.stations:
                first = self.stations[0]
                ty, tz = first.target
                self.fmc.goto(ty, tz, abort=self.abort)
                self._log(f"本轮结束：停到第一个格点 {first.id}"
                          f"（Y={ty:.1f} Z={tz:.1f}）——打开预览即可看长势")
                if self._on_park is not None:
                    self._on_park(station_id=first.id, box_id=first.box_id,
                                  y=ty, z=tz)
        report.ended_at = datetime.now()
        return report

    def _log(self, msg: str) -> None:
        if self._log_sink is not None:
            self._log_sink(msg)

    def _heartbeat(self, index: int, total: int, **fields) -> None:
        if self._on_station is not None:
            self._on_station(index, total, **fields)
