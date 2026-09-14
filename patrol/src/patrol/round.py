"""一轮巡检的完整生命周期（架构评审 #2 的 deep module）。

回零 → 站位循环 → 回原位 → RoundReport，全部收在一个 interface（run()）后面。
daemon 只负责何时触发、重连与 outbox；per-station 时序（orchestrator）是其
implementation 的内部缝。
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

from patrol.capture_client import CaptureClient, CaptureError
from patrol.fmc import Fmc4030, FmcError
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
        station_capture: StationCapture | None = None,  # 内部缝：测试注入
        framing: FramingHook | None = None,             # 图像微调（第二步定位）
        sleep=time.sleep,
        log: Callable[[str], None] | None = None,
        on_station: Callable[..., None] | None = None,  # 逐站心跳（结构化，供取证）
    ) -> None:
        self.fmc = fmc
        self.capture_client = capture_client
        self.stations = stations
        self.max_consecutive_failures = max_consecutive_failures
        self.return_home = return_home
        self._station_capture = station_capture
        # 微调钩子原样透给 StationCapture：不传 = 只走"第一步"（推导坐标），
        # 行为与没有这个功能时完全一致。
        self.framing = framing
        self._sleep = sleep
        self._log_sink = log
        self._on_station = on_station

    def run(self) -> RoundReport:
        """执行一轮：回零 → 逐站采图 → 回原位。

        **失败分级**（现场教训，见 ``patrol.journal`` 的由来）：

        * **运动失败**（``FmcError``，含 ``goto`` 的传输/控制器错误）→ 立即记录并
          **抛出**，本轮作废。同一个接线上所有站位都会同样失败，继续跑只会又动 60 次
          机构、又写 60 条无意义的失败。调用方（daemon）负责急停并记 failed。
        * **采图失败**（``CaptureError``）→ 记入 ``report.failures`` 并跳过，
          连续 ``max_consecutive_failures`` 次才中止（链路大概率断了）。
        * **回零失败**（``home_all`` 抛）→ 直接抛出，本轮没开始。

        ``log`` 是人看的行文，``on_station`` 是**结构化**的逐站心跳——被中断的那一轮，
        现场就靠它回答"走到哪一站了"。两者分开：日志文案会改，取证字段不该跟着改。
        """
        report = RoundReport(started_at=datetime.now())
        self.fmc.home_all()
        cap = self._station_capture or StationCapture(
            self.fmc, self.capture_client, sleep=self._sleep, framing=self.framing
        )
        consecutive = 0
        total = len(self.stations)
        for i, st in enumerate(self.stations, 1):
            t0 = time.monotonic()
            try:
                meta = cap.run(st)
            except TravelShortfallError as e:
                # **这一次移动没有按指令完成**——跳过该站，继续下一站（与采图失败同级）。
                # 依据：实测它不具预测性（20 趟里 2 趟，异常后的下一趟通常正常），而且
                # 回零始终能把坐标系拉回硬限位（落点稳定 0.000），所以后续站位的绝对定位
                # 仍有意义。与 FmcError（传输/控制器层面的事实 ⇒ 60 站都会同样失败）区别对待。
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
                # 层面的事实——同一个接线上所有站都会同样失败，继续跑只会又动 60 次
                # 机构、又留下 60 条无意义的失败记录。抛出去交给 daemon：它负责急停并
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
            self.fmc.goto(0.0, 0.0)
        report.ended_at = datetime.now()
        return report

    def _log(self, msg: str) -> None:
        if self._log_sink is not None:
            self._log_sink(msg)

    def _heartbeat(self, index: int, total: int, **fields) -> None:
        if self._on_station is not None:
            self._on_station(index, total, **fields)
